"""Tests for simulate/validate.py — the cavity polarity rule against scenes with known answers."""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from simulate import __main__ as cli
from simulate import instrument, runner, validate
from simulate.scenes import DisturbedZone, Medium, Scene, Void, two_way_time_ns

DT_S, ITERATIONS = 14e-12, 2000


def _scene(index: int, **objects) -> Scene:
    base = {
        "scene_id": f"scene_{index:05d}", "seed": index, "soil": Medium(9.0, 0.002), "layers": (), "pipes": (),
        "slabs": (), "zones": (), "voids": (), "stones": (), "frequency_mhz": 466.0, "antenna_offset_m": 0.1,
    }
    base.update(objects)
    return Scene(**base)


def _wavelet(peak_s: float) -> np.ndarray:
    t = np.arange(ITERATIONS) * DT_S
    return np.exp(-(((t - peak_s) / 0.3e-9) ** 2)) - 0.5 * np.exp(-(((t - peak_s - 0.8e-9) / 0.3e-9) ** 2))


def _write(path: Path, field: np.ndarray) -> None:
    with h5py.File(path, "w") as handle:
        handle.attrs["dt"] = DT_S
        handle.create_dataset("rxs/rx1/Ez", data=field)


def _simulate_flat_echo(runs_dir: Path, scene: Scene, x0: float, x1: float, top_m: float, polarity: float) -> None:
    """Outputs with a direct wave, plus a flat echo off the object's top with the given polarity."""
    scene_dir = runner.prepare_scene(scene, runs_dir)
    background = _wavelet(3e-9)
    echo_s = 3e-9 + two_way_time_ns(top_m, scene.soil, ()) * 1e-9
    columns = [
        background + (polarity * 0.4 * _wavelet(echo_s) if x0 <= trace * instrument.TRACE_SPACING_M <= x1 else 0.0)
        for trace in range(instrument.TRACES_PER_FRAME)
    ]
    _write(scene_dir / runner.BACKGROUND_OUTPUT, background)
    _write(scene_dir / runner.SCENE_OUTPUT, np.stack(columns, axis=1))


@pytest.fixture
def runs(tmp_path: Path) -> Path:
    runs_dir = tmp_path / "runs"
    # A void: soil into air keeps the direct wave's polarity.
    _simulate_flat_echo(runs_dir, _scene(0, voids=(Void(3.0, 4.5, 0.4, 0.6),)), 3.0, 4.5, 0.4, polarity=+1.0)
    # A trench: wetter backfill flips it.
    zone = DisturbedZone(5.0, 7.0, 0.3, 0.9, 0.05, 0.15, 7)
    _simulate_flat_echo(runs_dir, _scene(1, zones=(zone,)), 5.0, 7.0, 0.3, polarity=-1.0)
    return runs_dir


def test_the_rule_calls_the_void_a_cavity_and_leaves_the_trench_alone(runs: Path) -> None:
    report = validate.cavity_rule_report(runs)
    assert (report.voids_called_cavity, report.voids_total) == (1, 1)
    assert (report.zones_called_cavity, report.zones_total) == (0, 1)


def test_a_trench_with_an_air_like_echo_is_counted_as_a_false_alarm(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    zone = DisturbedZone(5.0, 7.0, 0.3, 0.9, 0.05, 0.15, 7)
    _simulate_flat_echo(runs_dir, _scene(1, zones=(zone,)), 5.0, 7.0, 0.3, polarity=+1.0)
    report = validate.cavity_rule_report(runs_dir)
    assert (report.zones_called_cavity, report.zones_total) == (1, 1)


def test_no_simulated_scenes_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no usable simulated scenes"):
        validate.cavity_rule_report(tmp_path)


def test_the_command_prints_the_counts(runs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["validate", "--runs", str(runs)]) == 0
    out = capsys.readouterr().out
    assert "voids called cavity:            1/1" in out
    assert "trenches wrongly called cavity: 0/1" in out


def test_the_command_says_when_there_is_too_little_to_judge(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runs_dir = tmp_path / "runs"
    _simulate_flat_echo(runs_dir, _scene(0, voids=(Void(3.0, 4.5, 0.4, 0.6),)), 3.0, 4.5, 0.4, polarity=+1.0)
    cli.main(["validate", "--runs", str(runs_dir)])
    assert "not enough simulated voids and trenches" in capsys.readouterr().err
