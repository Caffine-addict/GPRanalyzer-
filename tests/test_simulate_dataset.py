"""Tests for simulate/dataset.py — end to end from simulated outputs to a YOLO dataset."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import h5py
import numpy as np
import pytest
import yaml

from core.config import load_config
from detect.shapes import SHAPE_CLASSES
from simulate import dataset, instrument, runner
from simulate.scenes import LINE_LENGTH_M, Medium, Pipe, Scene

REAL_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"
DT_S, ITERATIONS = 14e-12, 2000


def _pipe_scene(index: int) -> Scene:
    return Scene(
        scene_id=f"scene_{index:05d}", seed=index, soil=Medium(9.0, 0.002), layers=(),
        pipes=(Pipe(3.0 + index, 0.5, 0.05, "metal"),), slabs=(), zones=(), voids=(), stones=(),
        frequency_mhz=466.0, antenna_offset_m=0.1,
    )


def _wavelet(peak_s: float) -> np.ndarray:
    t = np.arange(ITERATIONS) * DT_S
    return np.exp(-(((t - peak_s) / 0.3e-9) ** 2)) - 0.5 * np.exp(-(((t - peak_s - 0.8e-9) / 0.3e-9) ** 2))


def _write(path: Path, field: np.ndarray) -> None:
    with h5py.File(path, "w") as handle:
        handle.attrs["dt"] = DT_S
        handle.create_dataset("rxs/rx1/Ez", data=field)


def _fake_simulation(scene_dir: Path, scene: Scene) -> None:
    """Outputs shaped like gprMax's: a direct wave, plus the pipe's hyperbola in the scene."""
    background = _wavelet(3e-9)
    pipe, velocity = scene.pipes[0], scene.soil.velocity_m_per_ns
    columns = []
    for trace in range(instrument.TRACES_PER_FRAME):
        offset = trace * instrument.TRACE_SPACING_M - pipe.x_m
        echo_ns = 2 * np.hypot(offset, pipe.depth_m) / velocity
        columns.append(background + (0.3 * _wavelet(3e-9 + echo_ns * 1e-9) if abs(offset) < 0.8 else 0.0))
    _write(scene_dir / runner.BACKGROUND_OUTPUT, background)
    _write(scene_dir / runner.SCENE_OUTPUT, np.stack(columns, axis=1))


@pytest.fixture
def runs(tmp_path: Path) -> Path:
    runs_dir = tmp_path / "runs"
    for index in range(3):
        scene = _pipe_scene(index)
        _fake_simulation(runner.prepare_scene(scene, runs_dir), scene)
    return runs_dir


def _build(runs: Path, out: Path) -> dataset.BuildSummary:
    return dataset.build_dataset(runs, out, load_config(REAL_CONFIG).enhancement, val_fraction=0.3)


def test_every_simulated_scene_becomes_an_image_and_a_label(runs: Path, tmp_path: Path) -> None:
    out = tmp_path / "ds"
    summary = _build(runs, out)
    assert summary.frames == 3
    assert summary.boxes_by_class == {"point_reflector": 3}
    assert sum(summary.by_split.values()) == 3 and summary.by_split.get("val", 0) >= 1
    images = sorted(out.glob("images/*/*.png"))
    assert len(images) == 3
    assert cv2.imread(str(images[0]), cv2.IMREAD_UNCHANGED).shape == (640, 640)


def test_each_label_box_sits_over_its_pipe(runs: Path, tmp_path: Path) -> None:
    out = tmp_path / "ds"
    _build(runs, out)
    for index in range(3):
        [label] = list(out.glob(f"labels/*/scene_{index:05d}.txt"))
        [line] = label.read_text().splitlines()
        class_id, cx, *_ = line.split()
        assert class_id == "0"
        # Distance runs across the image: the box centre is the pipe's position along the line.
        assert float(cx) == pytest.approx((3.0 + index) / (LINE_LENGTH_M + instrument.TRACE_SPACING_M), abs=0.03)


def test_data_yaml_names_the_shapes_in_class_id_order(runs: Path, tmp_path: Path) -> None:
    out = tmp_path / "ds"
    _build(runs, out)
    data = yaml.safe_load((out / "data.yaml").read_text())
    assert data["names"] == dict(enumerate(SHAPE_CLASSES))
    assert (data["train"], data["val"]) == ("images/train", "images/val")


def test_manifest_and_gain_record_how_the_set_was_made(runs: Path, tmp_path: Path) -> None:
    out = tmp_path / "ds"
    _build(runs, out)
    lines = [json.loads(line) for line in (out / "manifest.jsonl").read_text().splitlines()]
    assert [line["scene_id"] for line in lines] == ["scene_00000", "scene_00001", "scene_00002"]
    # Three scenes are far too few to estimate the instrument's depth gain: left at unity.
    assert json.loads((out / "gain.json").read_text())["derived_from_frames"] == 0


def test_one_broken_scene_is_skipped_and_reported_not_fatal(runs: Path, tmp_path: Path) -> None:
    broken = runner.prepare_scene(_pipe_scene(3), runs)
    (broken / runner.SCENE_OUTPUT).write_bytes(b"not hdf5")
    (broken / runner.BACKGROUND_OUTPUT).write_bytes(b"not hdf5")
    summary = _build(runs, tmp_path / "ds")
    assert summary.frames == 3
    assert len(summary.skipped) == 1 and summary.skipped[0].startswith("scene_00003")


def test_no_simulated_scenes_is_an_error(tmp_path: Path) -> None:
    runner.prepare_scene(_pipe_scene(0), tmp_path / "runs")
    with pytest.raises(ValueError, match="no usable simulated scenes"):
        _build(tmp_path / "runs", tmp_path / "ds")


def test_splits_are_stable_per_scene() -> None:
    ids = [f"scene_{i:05d}" for i in range(200)]
    first, second = dataset.assign_splits(ids, 0.15), dataset.assign_splits(ids[:100], 0.15)
    assert all(first[sid] == second[sid] for sid in ids[:100])
    assert 0.08 < sum(v == "val" for v in first.values()) / 200 < 0.25


def test_a_small_set_still_gets_a_validation_frame() -> None:
    assert "val" in dataset.assign_splits(["scene_a", "scene_b"], 0.15).values()


def test_no_validation_fraction_means_no_validation_frames() -> None:
    assert set(dataset.assign_splits(["scene_a", "scene_b", "scene_c"], 0.0).values()) == {"train"}


def test_an_impossible_validation_fraction_is_refused() -> None:
    with pytest.raises(ValueError):
        dataset.assign_splits(["scene_a"], 1.0)


def test_an_empty_scene_builds_with_no_labels_and_stays_out_of_the_gain(runs: Path, tmp_path: Path) -> None:
    empty = Scene(
        scene_id="scene_00009", seed=9, soil=Medium(9.0, 0.002), layers=(), pipes=(), slabs=(), zones=(),
        voids=(), stones=(), frequency_mhz=466.0, antenna_offset_m=0.1,
    )
    scene_dir = runner.prepare_scene(empty, runs)
    background = _wavelet(3e-9)
    _write(scene_dir / runner.BACKGROUND_OUTPUT, background)
    _write(scene_dir / runner.SCENE_OUTPUT, np.tile(background[:, None], (1, instrument.TRACES_PER_FRAME)))
    out = tmp_path / "ds"
    summary = _build(runs, out)
    assert summary.frames == 4
    [label] = list(out.glob("labels/*/scene_00009.txt"))
    assert label.read_text() == ""
    # Three scenes are far too few to estimate the instrument's depth gain: left at unity.
    assert json.loads((out / "gain.json").read_text())["derived_from_frames"] == 0
