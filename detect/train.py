"""Train the shape detector (YOLOv8) on a YOLO-format dataset.

    python -m detect.train --data datasets/synthetic/batch1/data.yaml --epochs 100
    python -m detect.train --data ... --epochs 3 --device mps     # a quick check on a Mac

Checkpoints land in runs/detect/<name>/. Training never touches weights/best.pt on its
own: pass --install to copy the best checkpoint there, and only after reading its
validation numbers. A detector that has merely been *trained* is not yet a *good* one,
and whatever sits in weights/best.pt is what the live pipeline will run.

The compute device is left to Ultralytics unless --device is given (CLAUDE.md: never
force one — the company PC's hardware is unknown).
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Any

import yaml

from core.config import load_config

DEFAULT_BASE_MODEL = "yolov8n.pt"  # nano: fastest to run on a CPU-only field laptop
DEFAULT_PROJECT = Path("runs/detect")

# Augmentation for radargrams, not photographs. Every setting that would produce a
# picture this radar could never record is switched off.
RADARGRAM_AUGMENTATION: dict[str, float] = {
    "fliplr": 0.5,  # the line could have been walked either way: physically valid
    "flipud": 0.0,  # time runs down; a hyperbola never opens upward
    "mosaic": 0.0,  # stitching four frames puts a ground surface mid-image
    "mixup": 0.0,  # blending two B-scans makes echoes no single survey contains
    "degrees": 0.0,  # rotation tilts the time axis
    "shear": 0.0,
    "perspective": 0.0,
    "hsv_h": 0.0,  # greyscale input: no hue or saturation to vary
    "hsv_s": 0.0,
    "hsv_v": 0.3,  # brightness: stands in for gain differences between instruments
    "translate": 0.05,  # small shifts: time-zero and line-start differences
    "scale": 0.2,  # modest: rescaling also changes a hyperbola's apparent velocity
}


class DatasetMismatchError(ValueError):
    """The dataset's or checkpoint's class names do not match what the pipeline accepts."""


def dataset_classes(data_yaml: Path) -> tuple[str, ...]:
    """Class names from a YOLO data.yaml, in class-id order."""
    raw = yaml.safe_load(data_yaml.read_text())
    names = raw.get("names") if isinstance(raw, dict) else None
    if isinstance(names, dict):
        return tuple(str(names[key]) for key in sorted(names))
    if isinstance(names, list):
        return tuple(str(name) for name in names)
    raise DatasetMismatchError(f"{data_yaml} has no usable 'names' entry")


def check_classes(found: tuple[str, ...], expected: tuple[str, ...], source: str) -> None:
    """Refuse a class list the pipeline would not accept.

    detect/model.py drops any detection whose class is not in config.yaml's
    detection.classes, so a mismatched model does not fail — it silently finds nothing.
    Catching it here, before hours of training, is the point.
    """
    if tuple(found) != tuple(expected):
        raise DatasetMismatchError(
            f"{source} classes {list(found)} do not match config detection.classes {list(expected)} "
            "(names and order must be identical, or the pipeline discards every detection)"
        )


def train(
    data_yaml: Path,
    *,
    epochs: int,
    imgsz: int = 640,
    batch: int = 16,
    device: str | None = None,
    base_model: str = DEFAULT_BASE_MODEL,
    project: Path = DEFAULT_PROJECT,
    name: str = "shapes",
) -> Path:
    """Train and return the path of the best checkpoint."""
    if epochs <= 0:
        raise ValueError(f"epochs must be positive, got {epochs}")
    from ultralytics import YOLO  # heavy import, only needed when actually training

    model = YOLO(base_model)
    options: dict[str, Any] = {
        "data": str(data_yaml),
        "epochs": epochs,
        "imgsz": imgsz,
        "batch": batch,
        "project": str(project.resolve()),
        "name": name,
        "exist_ok": False,  # a new run folder every time; never overwrite a previous run
        **RADARGRAM_AUGMENTATION,
    }
    if device is not None:
        options["device"] = device
    model.train(**options)
    trainer = model.trainer
    if trainer is None:
        raise RuntimeError("Ultralytics finished training without a trainer to read results from")
    best = Path(trainer.save_dir) / "weights" / "best.pt"
    if not best.exists():
        raise RuntimeError(f"training finished without writing {best}")
    return best


def install(checkpoint: Path, weights_path: Path, expected: tuple[str, ...], *, force: bool = False) -> Path:
    """Copy a checkpoint to where the pipeline loads weights from, after checking its classes."""
    from ultralytics import YOLO

    names = YOLO(str(checkpoint)).names
    check_classes(tuple(str(names[key]) for key in sorted(names)), expected, f"checkpoint {checkpoint}")
    if weights_path.exists() and not force:
        raise FileExistsError(f"{weights_path} already exists — pass --force to replace the detector the pipeline runs")
    weights_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(checkpoint, weights_path)
    return weights_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m detect.train", description="Train the GPR shape detector.")
    parser.add_argument("--data", type=Path, required=True, help="YOLO data.yaml")
    parser.add_argument("--epochs", type=int, required=True)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default=None, help="e.g. cpu, mps, 0 — default: let Ultralytics choose")
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    parser.add_argument("--name", default="shapes")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--install", action="store_true", help="copy the best checkpoint to detection.weights_path")
    parser.add_argument("--force", action="store_true", help="with --install: replace existing weights")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    expected = config.detection.classes
    check_classes(dataset_classes(args.data), expected, f"dataset {args.data}")
    best = train(
        args.data, epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, device=args.device,
        base_model=args.base_model, name=args.name,
    )
    print(f"best checkpoint: {best}")
    if args.install:
        print(f"installed: {install(best, Path(config.detection.weights_path), expected, force=args.force)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
