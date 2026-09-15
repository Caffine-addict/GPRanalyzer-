"""Simulated scenes -> the YOLO dataset the detector trains on.

Every frame is rendered by exactly what the live pipeline does to a real line before
detection: `render.bscan.traces_to_image`, then `preprocess.enhance.enhance` with the
settings in config.yaml. A synthetic image rendered any other way would teach the
detector about pictures it will never be shown.

Output layout (standard Ultralytics):

    <out>/images/{train,val}/<scene>.png
    <out>/labels/{train,val}/<scene>.txt   one "class cx cy w h" line per visible object
    <out>/data.yaml                        class names in detect/shapes.py order
    <out>/manifest.jsonl                   per frame: scene parameters, boxes, objects left unlabelled and why
    <out>/gain.json                        the time-gain curve and how many frames it came from
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import yaml

from core.config import EnhancementConfig
from detect.shapes import SHAPE_CLASSES
from preprocess.enhance import enhance
from render.bscan import traces_to_image
from simulate import convert, labels, runner
from simulate.scenes import Scene

_NOISE_SCALE_RANGE = (0.5, 2.0)  # around the measured noise ratio, so the detector sees cleaner and dirtier lines
SPLITS = ("train", "val")


@dataclass(frozen=True)
class BuildSummary:
    frames: int
    by_split: dict[str, int]
    boxes_by_class: dict[str, int]
    unlabelled_objects: int
    skipped: tuple[str, ...]


def _split_score(scene_id: str) -> float:
    """A stable number in [0, 1) per scene, so a scene never changes split as the set grows."""
    digest = hashlib.sha256(scene_id.encode()).digest()
    return int.from_bytes(digest[:4], "big") / 2**32


def assign_splits(scene_ids: list[str], val_fraction: float) -> dict[str, str]:
    """Hash-based train/val split, with at least one validation frame whenever there are two or more."""
    if not 0.0 <= val_fraction < 1.0:
        raise ValueError(f"val_fraction must be in [0, 1), got {val_fraction}")
    splits = {sid: "val" if _split_score(sid) < val_fraction else "train" for sid in scene_ids}
    if len(scene_ids) >= 2 and val_fraction > 0 and "val" not in splits.values():
        splits[min(scene_ids, key=_split_score)] = "val"
    return splits


def load_simulated_frames(runs_dir: Path) -> tuple[list[tuple[Scene, convert.SimulatedFrame]], list[str]]:
    """Every simulated scene under `runs_dir` on the instrument grid, plus the ones that could not be read."""
    frames: list[tuple[Scene, convert.SimulatedFrame]] = []
    skipped: list[str] = []
    for scene_dir in sorted(d for d in runs_dir.iterdir() if d.is_dir()):
        if not runner.is_simulated(scene_dir):
            continue
        try:
            scene, _dx = runner.load_scene(scene_dir)
            scene_field, dt_s = convert.read_receiver(scene_dir / runner.SCENE_OUTPUT)
            background_field, background_dt_s = convert.read_receiver(scene_dir / runner.BACKGROUND_OUTPUT)
            if not math.isclose(dt_s, background_dt_s, rel_tol=1e-9):
                raise convert.SimulationOutputError("scene and background were solved with different time steps")
            frames.append((scene, convert.simulated_frame(scene_field, background_field, dt_s)))
        except (convert.SimulationOutputError, FileNotFoundError, ValueError) as exc:
            # One bad scene must not sink a batch that took hours to simulate; it is
            # reported in the summary rather than silently dropped.
            skipped.append(f"{scene_dir.name}: {exc}")
    return frames, skipped


def write_data_yaml(out_dir: Path) -> Path:
    path = out_dir / "data.yaml"
    content = {
        "path": str(out_dir.resolve()),
        "train": "images/train",
        "val": "images/val",
        "names": dict(enumerate(SHAPE_CLASSES)),
    }
    path.write_text(yaml.safe_dump(content, sort_keys=False))
    return path


def build_dataset(
    runs_dir: Path,
    out_dir: Path,
    enhancement: EnhancementConfig,
    *,
    val_fraction: float = 0.15,
    seed: int = 0,
) -> BuildSummary:
    """Turn every simulated scene under `runs_dir` into a labelled training image."""
    frames, skipped = load_simulated_frames(runs_dir)
    if not frames:
        raise ValueError(f"no usable simulated scenes under {runs_dir}" + (f" ({len(skipped)} skipped)" if skipped else ""))

    gain, gain_frames = convert.time_gain_for([frame for _, frame in frames])
    splits = assign_splits([scene.scene_id for scene, _ in frames], val_fraction)
    for split in SPLITS:
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(seed)
    boxes_by_class: Counter[str] = Counter()
    unlabelled = 0
    manifest_lines = []
    for scene, frame in frames:
        gained = convert.apply_gain(frame, gain)
        noise_scale = float(rng.uniform(*_NOISE_SCALE_RANGE))
        sigma = convert.noise_sigma(gained.total, noise_scale)
        result = labels.label_scene(scene, gained.scattered, sigma)
        recorded = convert.add_noise(gained.total, sigma, rng)

        # traces_to_image takes (n_traces, n_samples); the frame is (n_samples, n_traces).
        image = enhance(traces_to_image(recorded.T), enhancement)
        n_samples, n_traces = recorded.shape
        split = splits[scene.scene_id]
        cv2.imwrite(str(out_dir / "images" / split / f"{scene.scene_id}.png"), image)
        label_text = "".join(box.to_yolo(n_traces, n_samples) + "\n" for box in result.boxes)
        (out_dir / "labels" / split / f"{scene.scene_id}.txt").write_text(label_text)

        boxes_by_class.update(box.shape_class for box in result.boxes)
        unlabelled += len(result.unlabelled)
        manifest_lines.append(
            json.dumps(
                {
                    "scene_id": scene.scene_id,
                    "split": split,
                    "noise_scale": round(noise_scale, 3),
                    "boxes": [vars(box) for box in result.boxes],
                    "unlabelled": list(result.unlabelled),
                    "scene": scene.to_dict(),
                }
            )
        )

    (out_dir / "manifest.jsonl").write_text("\n".join(manifest_lines) + "\n")
    (out_dir / "gain.json").write_text(json.dumps({"derived_from_frames": gain_frames, "gain": gain.round(6).tolist()}) + "\n")
    write_data_yaml(out_dir)
    return BuildSummary(
        frames=len(frames),
        by_split=dict(Counter(splits.values())),
        boxes_by_class=dict(boxes_by_class),
        unlabelled_objects=unlabelled,
        skipped=tuple(skipped),
    )
