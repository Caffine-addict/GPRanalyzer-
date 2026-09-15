"""Tests for detect/model.py.

Real trained weights don't exist yet (no company data), so every test here
either exercises the "weights absent" path directly, or mocks
detect.model.YOLO — imported at module level specifically so it's a
patchable symbol, per the original brief's "mock the model where weights
are unavailable."
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import detect.model as detect_model
from core.config import DetectionConfig
from detect.model import Detector, ModelLoadError, ModelNotFoundError

REPO_ROOT = Path(__file__).resolve().parent.parent


def _config(weights_path: str) -> DetectionConfig:
    return DetectionConfig(
        weights_path=weights_path,
        conf_threshold=0.25,
        iou_threshold=0.7,
        classes=("cavities", "elongated_linear_target"),
        taxonomy=("cavities", "elongated_linear_target"),
    )


def test_detect_raises_model_not_found_for_missing_weights(tmp_path: Path) -> None:
    cfg = _config(str(tmp_path / "does_not_exist.pt"))
    detector = Detector(cfg)
    with pytest.raises(ModelNotFoundError):
        detector.detect(np.zeros((10, 10), dtype=np.uint8))


def test_model_not_found_error_names_the_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "best.pt"
    detector = Detector(_config(str(missing)))
    with pytest.raises(ModelNotFoundError) as exc_info:
        detector.detect(np.zeros((10, 10), dtype=np.uint8))
    assert str(missing) in str(exc_info.value)


class _FakeTensor:
    def __init__(self, array: np.ndarray) -> None:
        self._array = array

    def cpu(self) -> _FakeTensor:
        return self

    def numpy(self) -> np.ndarray:
        return self._array


class _FakeBoxes:
    def __init__(self, xyxy: list, conf: list, cls: list) -> None:
        self.xyxy = _FakeTensor(np.array(xyxy, dtype=float).reshape(-1, 4))
        self.conf = _FakeTensor(np.array(conf, dtype=float))
        self.cls = _FakeTensor(np.array(cls, dtype=float))

    def __len__(self) -> int:
        return self.xyxy._array.shape[0]


class _FakeResult:
    def __init__(self, names: dict, boxes: _FakeBoxes | None) -> None:
        self.names = names
        self.boxes = boxes


class _FakeModel:
    def __init__(self, path: str) -> None:
        self.path = path
        self.predict_calls: list[dict] = []

    def predict(self, *, source, conf, iou, verbose):
        self.predict_calls.append({"conf": conf, "iou": iou})
        names = {0: "cavities", 1: "elongated_linear_target"}
        # Deliberately asymmetric (x1 != y1, x2 != y2) in both boxes — a
        # fixture with x1==y1 (e.g. [10,10,50,50]) can't catch an x/y
        # coordinate transposition bug, since the swap would be invisible.
        boxes = _FakeBoxes(
            xyxy=[[10, 20, 50, 90], [15, 145, 200, 155]],
            conf=[0.9, 0.6],
            cls=[0, 1],
        )
        return [_FakeResult(names=names, boxes=boxes)]


def _fake_weights(tmp_path: Path) -> Path:
    weights = tmp_path / "best.pt"
    weights.write_bytes(b"fake")  # only needs to exist; YOLO() itself is mocked
    return weights


def test_detect_converts_results_to_detections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(detect_model, "YOLO", _FakeModel)
    detector = Detector(_config(str(_fake_weights(tmp_path))))

    detections = detector.detect(np.zeros((640, 640), dtype=np.uint8))

    assert len(detections) == 2
    assert detections[0].class_name == "cavities"
    assert detections[0].confidence == pytest.approx(0.9)
    assert detections[0].bbox_xyxy == (10.0, 20.0, 50.0, 90.0)
    assert detections[1].class_name == "elongated_linear_target"
    assert detections[1].confidence == pytest.approx(0.6)
    assert detections[1].bbox_xyxy == (15.0, 145.0, 200.0, 155.0)


def test_detect_confidence_and_bbox_coordinates_are_distinct_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # x1 and confidence are both in-range for each other's field here — a
    # confidence<->bbox field swap would still produce a validly-constructed
    # Detection (no crash to rely on), so this needs explicit value checks.
    class _DistinctValuesModel:
        def __init__(self, path: str) -> None:
            pass

        def predict(self, *, source, conf, iou, verbose):
            boxes = _FakeBoxes(xyxy=[[0.3, 20.0, 50.0, 90.0]], conf=[0.85], cls=[0])
            return [_FakeResult(names={0: "cavities"}, boxes=boxes)]

    monkeypatch.setattr(detect_model, "YOLO", _DistinctValuesModel)
    detector = Detector(_config(str(_fake_weights(tmp_path))))

    detections = detector.detect(np.zeros((10, 10), dtype=np.uint8))

    assert detections[0].confidence == pytest.approx(0.85)
    assert detections[0].bbox_xyxy[0] == pytest.approx(0.3)


def test_detect_passes_thresholds_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(detect_model, "YOLO", _FakeModel)
    cfg = _config(str(_fake_weights(tmp_path)))
    detector = Detector(cfg)

    detector.detect(np.zeros((10, 10), dtype=np.uint8))

    calls = detector._model.predict_calls  # type: ignore[union-attr]
    assert calls[0]["conf"] == cfg.conf_threshold
    assert calls[0]["iou"] == cfg.iou_threshold


def test_detect_with_no_boxes_returns_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _NoDetectionsModel:
        def __init__(self, path: str) -> None:
            pass

        def predict(self, *, source, conf, iou, verbose):
            return [_FakeResult(names={}, boxes=None)]

    monkeypatch.setattr(detect_model, "YOLO", _NoDetectionsModel)
    detector = Detector(_config(str(_fake_weights(tmp_path))))

    assert detector.detect(np.zeros((10, 10), dtype=np.uint8)) == []


def test_detect_with_empty_boxes_returns_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _EmptyBoxesModel:
        def __init__(self, path: str) -> None:
            pass

        def predict(self, *, source, conf, iou, verbose):
            empty = _FakeBoxes(xyxy=[], conf=[], cls=[])
            return [_FakeResult(names={}, boxes=empty)]

    monkeypatch.setattr(detect_model, "YOLO", _EmptyBoxesModel)
    detector = Detector(_config(str(_fake_weights(tmp_path))))

    assert detector.detect(np.zeros((10, 10), dtype=np.uint8)) == []


def test_detect_loads_model_once_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    load_count = {"n": 0}

    class _CountingModel(_FakeModel):
        def __init__(self, path: str) -> None:
            load_count["n"] += 1
            super().__init__(path)

    monkeypatch.setattr(detect_model, "YOLO", _CountingModel)
    detector = Detector(_config(str(_fake_weights(tmp_path))))

    detector.detect(np.zeros((10, 10), dtype=np.uint8))
    detector.detect(np.zeros((10, 10), dtype=np.uint8))

    assert load_count["n"] == 1


def test_detect_logs_latency_and_detection_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(detect_model, "YOLO", _FakeModel)
    detector = Detector(_config(str(_fake_weights(tmp_path))))

    with caplog.at_level(logging.INFO, logger="detect.model"):
        detector.detect(np.zeros((10, 10), dtype=np.uint8))

    messages = [r.getMessage() for r in caplog.records]
    assert any("detect.infer" in m and "n_detections=2" in m for m in messages)


def test_detect_raises_model_load_error_for_corrupt_weights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _BrokenModel:
        def __init__(self, path: str) -> None:
            raise RuntimeError("corrupt checkpoint")

    monkeypatch.setattr(detect_model, "YOLO", _BrokenModel)
    detector = Detector(_config(str(_fake_weights(tmp_path))))

    with pytest.raises(ModelLoadError, match="corrupt checkpoint"):
        detector.detect(np.zeros((10, 10), dtype=np.uint8))


def test_detect_skips_box_with_non_finite_coordinates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class _NanBoxModel:
        def __init__(self, path: str) -> None:
            pass

        def predict(self, *, source, conf, iou, verbose):
            boxes = _FakeBoxes(
                xyxy=[[float("nan"), 0.0, 10.0, 10.0], [0.0, 0.0, 10.0, 10.0]],
                conf=[0.9, 0.8],
                cls=[0, 0],
            )
            return [_FakeResult(names={0: "cavities"}, boxes=boxes)]

    monkeypatch.setattr(detect_model, "YOLO", _NanBoxModel)
    detector = Detector(_config(str(_fake_weights(tmp_path))))

    with caplog.at_level(logging.WARNING, logger="detect.model"):
        detections = detector.detect(np.zeros((10, 10), dtype=np.uint8))

    # The NaN box is skipped, not silently constructed and not crashing the
    # whole frame — the other, valid box still comes through.
    assert len(detections) == 1
    assert detections[0].confidence == pytest.approx(0.8)
    messages = [r.getMessage() for r in caplog.records]
    assert any("detect.skip_box" in m and "non_finite" in m for m in messages)


def test_detect_skips_degenerate_box_without_losing_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class _DegenerateBoxModel:
        def __init__(self, path: str) -> None:
            pass

        def predict(self, *, source, conf, iou, verbose):
            boxes = _FakeBoxes(
                xyxy=[[10.0, 10.0, 10.0, 10.0], [0.0, 0.0, 10.0, 10.0]],  # first is zero-area
                conf=[0.9, 0.8],
                cls=[0, 0],
            )
            return [_FakeResult(names={0: "cavities"}, boxes=boxes)]

    monkeypatch.setattr(detect_model, "YOLO", _DegenerateBoxModel)
    detector = Detector(_config(str(_fake_weights(tmp_path))))

    with caplog.at_level(logging.WARNING, logger="detect.model"):
        detections = detector.detect(np.zeros((10, 10), dtype=np.uint8))

    assert len(detections) == 1
    assert detections[0].confidence == pytest.approx(0.8)
    messages = [r.getMessage() for r in caplog.records]
    assert any("detect.skip_box" in m for m in messages)


def test_detect_skips_and_warns_on_unmapped_class_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # An unmapped class_id falls back to a stringified int ("99"), which is
    # never going to coincidentally match a real taxonomy class name — so
    # this now also gets caught (and skipped) by the taxonomy allowlist
    # check, on top of the unmapped_class warning.
    class _UnmappedClassModel:
        def __init__(self, path: str) -> None:
            pass

        def predict(self, *, source, conf, iou, verbose):
            boxes = _FakeBoxes(xyxy=[[0.0, 0.0, 10.0, 10.0]], conf=[0.9], cls=[99])
            return [_FakeResult(names={0: "cavities"}, boxes=boxes)]  # 99 not in names

    monkeypatch.setattr(detect_model, "YOLO", _UnmappedClassModel)
    detector = Detector(_config(str(_fake_weights(tmp_path))))

    with caplog.at_level(logging.WARNING, logger="detect.model"):
        detections = detector.detect(np.zeros((10, 10), dtype=np.uint8))

    assert detections == []
    messages = [r.getMessage() for r in caplog.records]
    assert any("detect.unmapped_class" in m for m in messages)
    assert any("class_not_in_taxonomy" in m for m in messages)


def test_detect_skips_box_with_class_name_not_in_configured_taxonomy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A mapped-but-wrong class name — e.g. a mismatched/corrupted weights
    # file, an ML-ops mistake rather than an adversary — must not put
    # arbitrary text into what later becomes an LLM prompt.
    class _WrongTaxonomyModel:
        def __init__(self, path: str) -> None:
            pass

        def predict(self, *, source, conf, iou, verbose):
            boxes = _FakeBoxes(xyxy=[[0.0, 0.0, 10.0, 10.0]], conf=[0.9], cls=[0])
            return [_FakeResult(names={0: "definitely_not_a_real_class"}, boxes=boxes)]

    monkeypatch.setattr(detect_model, "YOLO", _WrongTaxonomyModel)
    detector = Detector(_config(str(_fake_weights(tmp_path))))

    with caplog.at_level(logging.WARNING, logger="detect.model"):
        detections = detector.detect(np.zeros((10, 10), dtype=np.uint8))

    assert detections == []
    messages = [r.getMessage() for r in caplog.records]
    assert any("class_not_in_taxonomy" in m and "definitely_not_a_real_class" in m for m in messages)


def test_detect_accepts_class_name_within_configured_taxonomy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(detect_model, "YOLO", _FakeModel)  # uses "cavities"/"elongated_linear_target"
    detector = Detector(_config(str(_fake_weights(tmp_path))))

    detections = detector.detect(np.zeros((10, 10), dtype=np.uint8))

    assert {d.class_name for d in detections} == {"cavities", "elongated_linear_target"}


def test_ultralytics_safe_load_is_set_before_ultralytics_is_imported() -> None:
    """`os.environ.setdefault("ULTRALYTICS_SAFE_LOAD", "true")` only protects YOLO(...)'s
    torch.load() (the standard pickle RCE surface — see the comment above that line in
    detect/model.py) if it runs BEFORE `from ultralytics import YOLO`: ultralytics.utils.SAFE_LOAD
    is a module-level constant read once from the env var at import time, never re-checked per
    call, so setting the var one statement too late leaves SAFE_LOAD False forever in that
    process even though the env var itself does end up set to "true".

    Every other test in this file monkeypatches detect_model.YOLO directly, bypassing
    ultralytics.utils entirely — none of them would notice a future edit that reordered these
    two lines. This has to run in a fresh subprocess with ULTRALYTICS_SAFE_LOAD scrubbed from
    the environment: within this pytest process ultralytics may already have been imported by
    an earlier test file (or by this file's own `from ultralytics import YOLO` at module load),
    so SAFE_LOAD's value can only be observed honestly on a first, clean import.
    """
    env = {k: v for k, v in os.environ.items() if k != "ULTRALYTICS_SAFE_LOAD"}
    result = subprocess.run(
        [sys.executable, "-c", "import detect.model; import ultralytics.utils as u; print(u.SAFE_LOAD)"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True", (
        f"ultralytics.utils.SAFE_LOAD was not True after importing detect.model — the "
        f"setdefault ran too late (or not at all) to protect YOLO(...)'s torch.load() call. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
