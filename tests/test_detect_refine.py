"""Tests for detect/refine.py — shape detections to taxonomy classes."""

from __future__ import annotations

import logging

import numpy as np
import pytest

from core.contracts import Detection
from detect import refine
from detect.shapes import DISTURBED_OR_VOID, LINEAR_REFLECTOR, POINT_REFLECTOR

TAXONOMY = (
    "cavities", "elongated_linear_target", "intersecting_linear_and_point_reflector",
    "strong_high_contrast_reflector", "multiple_point_reflectors", "low_snr_point_reflector",
    "cluttered_multi_target", "disturbed_zone", "clear_point_reflector",
)
IMAGE = (640, 640)
N_TRACES, N_SAMPLES = 384, 256


def _det(class_name: str, x1: float, y1: float, x2: float, y2: float, confidence: float = 0.9) -> Detection:
    return Detection(class_name=class_name, confidence=confidence, bbox_xyxy=(x1, y1, x2, y2))


def _image_box(t0: int, t1: int, s0: int, s1: int) -> tuple[float, float, float, float]:
    """The rendered-image box covering traces [t0, t1) and samples [s0, s1)."""
    return (t0 / N_TRACES * 640, s0 / N_SAMPLES * 640, t1 / N_TRACES * 640, s1 / N_SAMPLES * 640)


def _ground(seed: int = 0) -> np.ndarray:
    traces = np.random.default_rng(seed).normal(0.0, 0.2, size=(N_TRACES, N_SAMPLES))
    traces[:, 18:23] += [-3.0, 4.0, 10.0, 4.0, -3.0]  # positive direct wave
    return traces


def _refine(
    detections: list[Detection], traces: np.ndarray | None = None, sample_interval_ns: float | None = None
) -> list[refine.Refinement]:
    return refine.refine_detections(
        detections, taxonomy=TAXONOMY, image_shape=IMAGE, traces=traces, sample_interval_ns=sample_interval_ns
    )


def test_a_taxonomy_detection_passes_through_untouched() -> None:
    detection = _det("cavities", 10, 10, 50, 50)
    [result] = _refine([detection])
    assert result.detection is detection
    assert result.shape is None


def test_an_unknown_class_is_dropped_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="detect.refine"):
        assert _refine([_det("tractor", 10, 10, 50, 50)]) == []
    assert "unknown_class" in caplog.text


def test_a_linear_reflector_becomes_an_elongated_linear_target() -> None:
    [result] = _refine([_det(LINEAR_REFLECTOR, 10, 100, 400, 130)])
    assert result.detection.class_name == "elongated_linear_target"
    assert result.shape == LINEAR_REFLECTOR


def test_refinement_keeps_the_box_and_confidence() -> None:
    detection = _det(LINEAR_REFLECTOR, 10, 100, 400, 130, confidence=0.61)
    [result] = _refine([detection])
    assert result.detection.bbox_xyxy == detection.bbox_xyxy
    assert result.detection.confidence == 0.61


def test_an_unmeasurable_point_falls_back_to_the_low_snr_class() -> None:
    [result] = _refine([_det(POINT_REFLECTOR, 100, 100, 150, 150)], traces=None)
    assert result.detection.class_name == "low_snr_point_reflector"
    assert "not measurable" in result.rule


def test_a_strong_point_with_traces_is_clear() -> None:
    traces = _ground()
    traces[100:110, 120:126] += 8.0
    [result] = _refine([_det(POINT_REFLECTOR, *_image_box(98, 112, 115, 132))], traces=traces, sample_interval_ns=0.1)
    assert result.detection.class_name == "clear_point_reflector"


def test_a_faint_point_with_traces_is_low_snr() -> None:
    traces = _ground()
    traces[100:110, 120:126] += 0.3
    [result] = _refine([_det(POINT_REFLECTOR, *_image_box(98, 112, 115, 132))], traces=traces, sample_interval_ns=0.1)
    assert result.detection.class_name == "low_snr_point_reflector"
    assert "<" in result.rule


def test_a_point_on_a_linear_reflector_is_an_intersection() -> None:
    linear = _det(LINEAR_REFLECTOR, 50, 200, 500, 230)
    point = _det(POINT_REFLECTOR, 200, 190, 240, 240)
    results = {r.shape: r.detection.class_name for r in _refine([linear, point])}
    assert results[POINT_REFLECTOR] == "intersecting_linear_and_point_reflector"
    assert results[LINEAR_REFLECTOR] == "elongated_linear_target"


def test_evenly_spaced_points_at_one_depth_are_multiple_point_reflectors() -> None:
    row = [_det(POINT_REFLECTOR, x, 300, x + 30, 330) for x in (100, 200, 300, 400)]
    assert {r.detection.class_name for r in _refine(row)} == {"multiple_point_reflectors"}


def test_irregularly_crowded_points_are_cluttered() -> None:
    crowd = [_det(POINT_REFLECTOR, x, y, x + 20, y + 20) for x, y in ((100, 300), (120, 360), (170, 280))]
    assert {r.detection.class_name for r in _refine(crowd)} == {"cluttered_multi_target"}


def test_two_points_are_neither_a_row_nor_a_crowd() -> None:
    pair = [_det(POINT_REFLECTOR, 100, 300, 130, 330), _det(POINT_REFLECTOR, 400, 300, 430, 330)]
    assert {r.detection.class_name for r in _refine(pair)} == {"low_snr_point_reflector"}


def test_a_regular_row_close_enough_to_also_be_crowded_is_still_multiple_points() -> None:
    # These 4 points are both an evenly-spaced row (regular_row) and, because the
    # spacing is tight, each sits within crowd-reach of 2+ neighbours (_is_crowded)
    # too. No other fixture in this file makes both conditions true at once, so a
    # bug that checked _is_crowded before regular_row would slip past every other
    # test here and only show up as this class flipping to "cluttered_multi_target".
    row = [_det(POINT_REFLECTOR, x, 300, x + 20, 320) for x in (100, 140, 180, 220)]
    assert {r.detection.class_name for r in _refine(row)} == {"multiple_point_reflectors"}


def test_an_area_without_traces_is_a_disturbed_zone() -> None:
    [result] = _refine([_det(DISTURBED_OR_VOID, 200, 250, 350, 400)], traces=None)
    assert result.detection.class_name == "disturbed_zone"


def test_an_area_whose_top_echo_keeps_the_direct_waves_polarity_is_a_cavity() -> None:
    traces = _ground()
    traces[150:200, 120:123] += [-2.0, 5.0, -2.0]
    [result] = _refine(
        [_det(DISTURBED_OR_VOID, *_image_box(150, 200, 110, 140))], traces=traces, sample_interval_ns=0.1
    )
    assert result.detection.class_name == "cavities"
    assert "not yet validated" in result.rule


def test_an_area_with_a_reversed_top_echo_is_a_disturbed_zone() -> None:
    traces = _ground()
    traces[150:200, 120:123] += [2.0, -5.0, 2.0]
    [result] = _refine(
        [_det(DISTURBED_OR_VOID, *_image_box(150, 200, 110, 140))], traces=traces, sample_interval_ns=0.1
    )
    assert result.detection.class_name == "disturbed_zone"


def test_traces_without_a_sample_interval_are_refused() -> None:
    # Silently falling back to a guessed sample rate would let a caller apply
    # DIRECT_WAVE_WINDOW_NS's time-based skip against the wrong scale — refuse instead.
    with pytest.raises(ValueError, match="sample_interval_ns"):
        _refine([_det(POINT_REFLECTOR, 100, 100, 150, 150)], traces=_ground())


def test_a_class_missing_from_the_taxonomy_is_dropped_loudly(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="detect.refine"):
        results = refine.refine_detections(
            [_det(LINEAR_REFLECTOR, 10, 100, 400, 130)], taxonomy=("cavities",), image_shape=IMAGE, traces=None
        )
    assert results == []
    assert "class_not_in_taxonomy" in caplog.text


def test_box_in_traces_rescales_image_pixels_onto_samples() -> None:
    rows, cols = refine.box_in_traces(_image_box(100, 110, 120, 130), IMAGE, (N_TRACES, N_SAMPLES))
    assert (cols.start, cols.stop) == (100, 110)
    assert (rows.start, rows.stop) == (120, 130)


def test_box_in_traces_never_returns_an_empty_or_out_of_range_slice() -> None:
    rows, cols = refine.box_in_traces((639.9, 639.9, 640.0, 640.0), IMAGE, (N_TRACES, N_SAMPLES))
    assert cols.stop <= N_TRACES and cols.stop > cols.start
    assert rows.stop <= N_SAMPLES and rows.stop > rows.start


def test_box_in_traces_uses_image_height_for_samples_and_width_for_traces() -> None:
    # IMAGE above is square (640, 640), so a height/width axis swap inside
    # box_in_traces produces byte-for-byte identical output and every other test in
    # this file — all built on IMAGE — cannot tell the axes apart. A non-square image
    # (400 tall, 800 wide) can: x (0..800) must scale onto n_traces via the *width*,
    # y (0..400) onto n_samples via the *height*. Swapping them changes both results.
    rows, cols = refine.box_in_traces((100.0, 50.0, 300.0, 150.0), (400, 800), (N_TRACES, N_SAMPLES))
    assert (cols.start, cols.stop) == (48, 144)
    assert (rows.start, rows.stop) == (32, 96)
