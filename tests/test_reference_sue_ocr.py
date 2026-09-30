"""Tests for reference/sue_ocr.py — reading SUE sheets with no text layer, via Tesseract.

`_tick_words` is exercised with `_tesseract_words` monkeypatched, so the coordinate-mapping
maths back to the upright page is pinned without needing a real render or the tesseract
binary. `_to_page` is a pure function, tested directly.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from reference import sue_ocr
from reference.sue_sheets import Callout


def test_a_tick_label_turned_clockwise_is_mapped_back_to_its_upright_box(monkeypatch: pytest.MonkeyPatch) -> None:
    # An upright page 100 rows x 300 columns; the label sits at columns [50, 90], rows [20, 35].
    # `_tick_words` tries the clockwise turn first, then counter-clockwise (both turned images
    # are (300, 100), so distinguish by call order, not shape): forward through
    # cv2.ROTATE_90_CLOCKWISE that word lands at columns [65, 80], rows [50, 90] on the turned
    # image — verified against what cv2 itself produces for this exact box, not hand-derived.
    clean = np.zeros((100, 300), np.uint8)
    calls = []

    def fake_tesseract_words(image: np.ndarray, px_per_point: float) -> list[dict]:
        calls.append(image.shape)
        if len(calls) == 1:  # clockwise pass
            return [{"text": "CH-20", "x0": 65.0, "top": 50.0, "x1": 80.0, "bottom": 90.0}]
        return []  # counter-clockwise pass: nothing readable

    monkeypatch.setattr(sue_ocr, "_tesseract_words", fake_tesseract_words)
    (word,) = sue_ocr._tick_words(clean, px_per_point=1.0)
    assert (word["x0"], word["top"], word["x1"], word["bottom"]) == (50.0, 20.0, 90.0, 35.0)


def test_a_tick_label_turned_counterclockwise_is_mapped_back_to_its_upright_box(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # Same upright box, this time only readable off the counter-clockwise-turned image. Forward
    # coordinates through cv2.ROTATE_90_COUNTERCLOCKWISE, verified against what cv2 itself
    # produces for this exact box, not hand-derived.
    clean = np.zeros((100, 300), np.uint8)
    calls = []

    def fake_tesseract_words(image: np.ndarray, px_per_point: float) -> list[dict]:
        calls.append(image.shape)
        if len(calls) == 1:  # clockwise pass: nothing readable
            return []
        return [{"text": "CH-20", "x0": 20.0, "top": 210.0, "x1": 35.0, "bottom": 250.0}]

    monkeypatch.setattr(sue_ocr, "_tesseract_words", fake_tesseract_words)
    (word,) = sue_ocr._tick_words(clean, px_per_point=1.0)
    assert (word["x0"], word["top"], word["x1"], word["bottom"]) == (50.0, 20.0, 90.0, 35.0)


def test_words_that_are_not_tick_labels_are_dropped_as_sideways_noise(monkeypatch: pytest.MonkeyPatch) -> None:
    clean = np.zeros((100, 300), np.uint8)
    monkeypatch.setattr(sue_ocr, "_tesseract_words",
                        lambda image, px_per_point: [{"text": "ELECTRIC", "x0": 0.0, "top": 0.0,
                                                       "x1": 10.0, "bottom": 10.0}])
    assert sue_ocr._tick_words(clean, px_per_point=1.0) == []


def test_tick_word_boxes_are_scaled_by_px_per_point(monkeypatch: pytest.MonkeyPatch) -> None:
    clean = np.zeros((100, 300), np.uint8)
    calls = []

    def fake_tesseract_words(image: np.ndarray, px_per_point: float) -> list[dict]:
        calls.append(image.shape)
        if len(calls) == 1:
            return [{"text": "CH-20", "x0": 65.0, "top": 50.0, "x1": 80.0, "bottom": 90.0}]
        return []

    monkeypatch.setattr(sue_ocr, "_tesseract_words", fake_tesseract_words)
    (word,) = sue_ocr._tick_words(clean, px_per_point=10.0)
    assert (word["x0"], word["top"], word["x1"], word["bottom"]) == (5.0, 2.0, 9.0, 3.5)


def test_to_page_divides_the_box_by_scale_and_leaves_everything_else_alone() -> None:
    callout = Callout(utility="UC", depth_m=0.77, chainage_m=12.0, chainage_source="plan_ticks",
                      box=(10.0, 20.0, 30.0, 40.0))
    scaled = sue_ocr._to_page(callout, scale=2.0)
    assert scaled.box == (5.0, 10.0, 15.0, 20.0)
    assert (scaled.utility, scaled.depth_m, scaled.chainage_m, scaled.chainage_source) == \
        ("UC", 0.77, 12.0, "plan_ticks")


def test_tesseract_available_reflects_whether_the_binary_is_on_path() -> None:
    assert sue_ocr.tesseract_available() == (sue_ocr.shutil.which("tesseract") is not None)


@pytest.mark.skipif(not sue_ocr.tesseract_available(), reason="tesseract binary not installed")
def test_a_synthetic_upright_word_is_read_by_the_real_ocr(tmp_path: Path) -> None:
    # A minimal end-to-end smoke test with the real tesseract binary: no rotation, no line
    # erasure — just proving _tesseract_words can read a rendered word back at all.
    image = np.full((100, 400), 255, np.uint8)
    cv2.putText(image, "ELECTRIC", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 0, 2, cv2.LINE_AA)
    words = sue_ocr._tesseract_words(image, px_per_point=1.0)
    assert any("ELECTRIC" in w["text"].upper() for w in words)
