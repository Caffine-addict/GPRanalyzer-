"""YOLOv8 inference wrapper. Loads weights from config, returns Detection objects, fails clearly on absent weights."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

# Must be set before `ultralytics` is imported: `ultralytics.utils.SAFE_LOAD` is a module-level
# constant read once from this env var at import time, not re-checked per call. `YOLO(...)`
# otherwise does an unrestricted `torch.load()` — the standard pickle RCE surface — on whatever
# file `weights_path` in config.yaml names. `setdefault` so an operator can still explicitly opt
# out (e.g. to load a legacy checkpoint with custom classes outside ultralytics' allow-list).
os.environ.setdefault("ULTRALYTICS_SAFE_LOAD", "true")

import numpy as np
from ultralytics import YOLO

from core.config import DetectionConfig
from core.contracts import Detection

logger = logging.getLogger(__name__)


class ModelNotFoundError(Exception):
    """Raised when the configured YOLO weights file doesn't exist.

    Deliberately a clear, typed error rather than letting ultralytics'
    own (much less obvious) failure surface as a stack trace — this
    project has no trained weights yet, so this is the expected path
    until Session 10 or a training run produces weights/best.pt.
    """


class ModelLoadError(Exception):
    """Raised when the weights file exists but ultralytics/torch can't load it (corrupt/incompatible checkpoint)."""


class Detector:
    """Wraps a YOLOv8 model. Weights are loaded lazily on first detect() call, not at construction."""

    def __init__(self, config: DetectionConfig) -> None:
        self._config = config
        self._weights_path = Path(config.weights_path)
        self._model: YOLO | None = None

    def _load_model(self) -> YOLO:
        if self._model is not None:
            return self._model
        if not self._weights_path.exists():
            raise ModelNotFoundError(
                f"YOLO weights not found at {self._weights_path} (config: detection.weights_path). "
                "Train or provide weights before running detection."
            )

        start = time.monotonic()
        try:
            self._model = YOLO(str(self._weights_path))
        except Exception as e:
            raise ModelLoadError(
                f"failed to load YOLO weights at {self._weights_path}: {e}"
            ) from e
        logger.info(
            "detect.model_load latency_ms=%.2f path=%s",
            (time.monotonic() - start) * 1000,
            self._weights_path,
        )
        return self._model

    def detect(self, image: np.ndarray) -> list[Detection]:
        model = self._load_model()

        start = time.monotonic()
        results = list(
            model.predict(
                source=image,
                conf=self._config.conf_threshold,
                iou=self._config.iou_threshold,
                verbose=False,
            )
        )
        latency_ms = (time.monotonic() - start) * 1000

        detections = _to_detections(results[0], self._config.classes)
        logger.info("detect.infer latency_ms=%.2f n_detections=%d", latency_ms, len(detections))
        return detections


def _to_detections(result, allowed_classes: tuple[str, ...]) -> list[Detection]:
    names = getattr(result, "names", None) or {}
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []

    xyxy = boxes.xyxy.cpu().numpy().tolist()
    confs = boxes.conf.cpu().numpy().tolist()
    classes = boxes.cls.cpu().numpy().tolist()

    detections: list[Detection] = []
    for (x1, y1, x2, y2), conf, cls in zip(xyxy, confs, classes, strict=True):
        # NaN bypasses Detection's own x2>x1/y2>y1 check (NaN comparisons
        # are always False), so it would otherwise construct silently rather
        # than being rejected — check explicitly instead of relying on that.
        if not all(np.isfinite(v) for v in (x1, y1, x2, y2, conf)):
            logger.warning("detect.skip_box reason=non_finite_values bbox=%s conf=%s", (x1, y1, x2, y2), conf)
            continue

        class_id = int(cls)
        if class_id not in names:
            logger.warning("detect.unmapped_class class_id=%d names_keys=%s", class_id, list(names))
        class_name = str(names.get(class_id, class_id))

        if class_name not in allowed_classes:
            # The weights file's own embedded class names aren't otherwise
            # validated against the project's taxonomy — a mistrained or
            # mismatched checkpoint (an ML-ops mistake, no adversary
            # required) could put arbitrary text into what later becomes an
            # LLM prompt (reason/prompt.py). detection.classes in config.yaml
            # is the allowlist; enforce it here rather than leaving it dead.
            logger.warning(
                "detect.skip_box reason=class_not_in_taxonomy class_name=%r allowed=%s",
                class_name,
                allowed_classes,
            )
            continue

        try:
            detection = Detection(
                class_name=class_name,
                confidence=float(conf),
                bbox_xyxy=(float(x1), float(y1), float(x2), float(y2)),
            )
        except ValueError as e:
            # One degenerate box (e.g. zero-width after NMS) shouldn't
            # discard every other valid detection in the frame.
            logger.warning("detect.skip_box reason=%s bbox=%s", e, (x1, y1, x2, y2))
            continue
        detections.append(detection)
    return detections
