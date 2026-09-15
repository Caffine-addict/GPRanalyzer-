"""Tests for evidence/extract.py — the confidence discipline is the entire point of this module."""

from __future__ import annotations

import math

import numpy as np
import pytest

from core.config import EvidenceConfig
from core.contracts import Detection, ScanFrame, SourceCapabilities
from evidence.extract import extract_evidence

_EVIDENCE_CONFIG = EvidenceConfig(assumed_max_depth_m=2.0)

_UNCALIBRATED_CAPS = SourceCapabilities(
    has_calibrated_depth=False,
    has_real_position=False,
    has_true_amplitude=False,
    latency_class="batch",
)

_FULLY_CALIBRATED_CAPS = SourceCapabilities(
    has_calibrated_depth=True,
    has_real_position=True,
    has_true_amplitude=True,
    latency_class="realtime",
)

# All-True/all-False capability fixtures can't tell "gated by the right
# flag" apart from "gated by any flag" — these vary one flag independently
# of the others specifically to catch that class of bug.
_DEPTH_ONLY_CALIBRATED_CAPS = SourceCapabilities(
    has_calibrated_depth=True,
    has_real_position=False,
    has_true_amplitude=False,
    latency_class="realtime",
)

_DEPTH_NOT_CALIBRATED_OTHERS_TRUE_CAPS = SourceCapabilities(
    has_calibrated_depth=False,
    has_real_position=True,
    has_true_amplitude=True,
    latency_class="realtime",
)

_POSITION_NOT_REAL_OTHERS_TRUE_CAPS = SourceCapabilities(
    has_calibrated_depth=True,
    has_real_position=False,
    has_true_amplitude=True,
    latency_class="realtime",
)


def _detection(bbox=(100.0, 200.0, 150.0, 260.0), confidence: float = 0.8) -> Detection:
    return Detection(class_name="cavities", confidence=confidence, bbox_xyxy=bbox)


_N_TRACES, _N_SAMPLES = 384, 256
_IMAGE_SHAPE = (640, 640)  # render.bscan.TARGET_SIZE x TARGET_SIZE


def _rendered_box(t0: int, t1: int, s0: int, s1: int) -> tuple[float, float, float, float]:
    """A bbox in the rendered-image space covering native traces [t0, t1) and samples [s0, s1)."""
    return (t0 / _N_TRACES * 640, s0 / _N_SAMPLES * 640, t1 / _N_TRACES * 640, s1 / _N_SAMPLES * 640)


def _replay_frame(**overrides) -> ScanFrame:
    base = {
        "source_type": "replay",
        "provenance": {},
        "image": np.full((640, 640), 100, dtype=np.uint8),
        "position": 3.0,
        "position_source": "synthetic",
    }
    base.update(overrides)
    return ScanFrame(**base)


def test_hyperbola_width_measured_from_bbox() -> None:
    detection = _detection(bbox=(100.0, 0.0, 150.0, 10.0))
    ev = extract_evidence(detection, _replay_frame(), _UNCALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.hyperbola_width_px == 50.0


def test_detection_class_and_confidence_pass_through() -> None:
    detection = _detection(confidence=0.73)
    ev = extract_evidence(detection, _replay_frame(), _UNCALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.detection_class == "cavities"
    assert ev.detection_confidence == 0.73


# --- depth ---


def test_depth_estimated_when_no_calibration_metadata() -> None:
    detection = _detection(bbox=(0.0, 0.0, 10.0, 640.0))  # y-center = 320, exactly half height
    frame = _replay_frame()  # capabilities uncalibrated by default in this test
    ev = extract_evidence(detection, frame, _UNCALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.depth_confidence == "estimated"
    assert ev.depth_m == pytest.approx(0.5 * _EVIDENCE_CONFIG.assumed_max_depth_m)


def test_depth_estimated_when_capabilities_calibrated_but_frame_missing_metadata() -> None:
    detection = _detection()
    frame = _replay_frame()  # no sample_interval_ns / dielectric_assumed
    ev = extract_evidence(detection, frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.depth_confidence == "estimated"


def test_depth_calibrated_formula_matches_expected_value() -> None:
    # Exact-value assertion, not just "not None and > 0" — catches a wrong
    # /2.0 (two-way travel time), a swapped sample_interval_ns/
    # dielectric_assumed, or any other arithmetic regression in the formula.
    detection = _detection(bbox=(0.0, 0.0, 10.0, 640.0))  # y-center = 320 -> fraction 0.5
    frame = _replay_frame(sample_interval_ns=0.1, dielectric_assumed=9.0)
    ev = extract_evidence(detection, frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG)

    fraction_of_height = 0.5
    n_samples = 640  # no traces on this frame -> falls back to image height
    sample_index = fraction_of_height * n_samples
    two_way_travel_time_ns = sample_index * 0.1
    velocity_m_per_ns = 0.2998 / math.sqrt(9.0)
    expected_depth_m = (two_way_travel_time_ns * velocity_m_per_ns) / 2.0

    assert ev.depth_confidence == "calibrated"
    assert ev.depth_m == pytest.approx(expected_depth_m)


def test_depth_calibrated_gated_specifically_by_has_calibrated_depth() -> None:
    detection = _detection(bbox=(0.0, 0.0, 10.0, 640.0))
    frame = _replay_frame(sample_interval_ns=0.1, dielectric_assumed=9.0)
    ev = extract_evidence(detection, frame, _DEPTH_ONLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.depth_confidence == "calibrated"


def test_depth_not_calibrated_when_has_calibrated_depth_false_even_if_others_true() -> None:
    # Proves the gate checks has_calibrated_depth specifically, not "any
    # capability flag is True".
    detection = _detection(bbox=(0.0, 0.0, 10.0, 640.0))
    frame = _replay_frame(sample_interval_ns=0.1, dielectric_assumed=9.0)
    ev = extract_evidence(detection, frame, _DEPTH_NOT_CALIBRATED_OTHERS_TRUE_CAPS, _EVIDENCE_CONFIG)
    assert ev.depth_confidence == "estimated"


def test_depth_unavailable_when_frame_has_no_image_and_no_image_shape() -> None:
    # No image_shape given -> no known mapping from the bbox (in whatever space it's in)
    # onto frame.traces' samples, so this stays "unavailable" rather than guessing.
    detection = _detection()
    frame = _replay_frame(image=None, traces=np.zeros((10, 500)))
    ev = extract_evidence(detection, frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.depth_confidence == "unavailable"
    assert ev.depth_m is None


def test_depth_estimated_for_traces_only_frame_via_box_in_traces_reconciliation() -> None:
    # The coordinate-space fix: image_shape lets a traces-only frame's rendered-image bbox be
    # rescaled onto native samples (detect.refine.box_in_traces), instead of "unavailable".
    # Exact-value assertion against the same formula test_depth_calibrated_formula_matches_
    # expected_value already pins, just with sample_index derived via the rescale this time.
    detection = _detection(bbox=_rendered_box(100, 110, 150, 170))  # sample centre = (150+170)/2 = 160
    traces = np.zeros((_N_TRACES, _N_SAMPLES))
    frame = _replay_frame(image=None, traces=traces, sample_interval_ns=0.1, dielectric_assumed=9.0)
    ev = extract_evidence(
        detection, frame, _UNCALIBRATED_CAPS, _EVIDENCE_CONFIG, image_shape=_IMAGE_SHAPE
    )

    sample_index = 160.0
    two_way_travel_time_ns = sample_index * 0.1
    velocity_m_per_ns = 0.2998 / math.sqrt(9.0)
    expected_depth_m = (two_way_travel_time_ns * velocity_m_per_ns) / 2.0

    assert ev.depth_confidence == "estimated"  # a real formula, but SPR_MEDIUM_DIELECTRIC is assumed, not measured
    assert ev.depth_m == pytest.approx(expected_depth_m)


def test_depth_calibrated_for_traces_only_frame_when_capabilities_say_so() -> None:
    detection = _detection(bbox=_rendered_box(100, 110, 150, 170))
    traces = np.zeros((_N_TRACES, _N_SAMPLES))
    frame = _replay_frame(image=None, traces=traces, sample_interval_ns=0.1, dielectric_assumed=9.0)
    ev = extract_evidence(
        detection, frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG, image_shape=_IMAGE_SHAPE
    )
    assert ev.depth_confidence == "calibrated"


def test_depth_falls_back_to_the_crude_estimate_for_traces_only_frame_without_header_data() -> None:
    # image_shape is given (a render did happen) but the frame carries no sample_interval_ns/
    # dielectric_assumed at all -- same crude fraction*assumed_max_depth_m fallback as the
    # image-frame case, not "unavailable" (the position is still knowable, just not the physics).
    detection = _detection(bbox=_rendered_box(100, 110, 0, _N_SAMPLES // 2))  # sample centre = fraction 0.25
    traces = np.zeros((_N_TRACES, _N_SAMPLES))
    frame = _replay_frame(image=None, traces=traces)
    ev = extract_evidence(
        detection, frame, _UNCALIBRATED_CAPS, _EVIDENCE_CONFIG, image_shape=_IMAGE_SHAPE
    )
    assert ev.depth_confidence == "estimated"
    assert ev.depth_m == pytest.approx(0.25 * _EVIDENCE_CONFIG.assumed_max_depth_m, rel=0.05)


def test_depth_unavailable_for_traces_only_frame_when_image_shape_is_not_given() -> None:
    # A source that supplies traces without ever having rendered an image from them (no
    # detection could have run, so no bbox coordinate space exists to reconcile) still falls
    # back to "unavailable" rather than guessing a mapping.
    detection = _detection()
    traces = np.zeros((_N_TRACES, _N_SAMPLES))
    frame = _replay_frame(image=None, traces=traces, sample_interval_ns=0.1, dielectric_assumed=9.0)
    ev = extract_evidence(detection, frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.depth_confidence == "unavailable"
    assert ev.depth_m is None


# --- position ---


def test_position_unavailable_for_synthetic_source() -> None:
    frame = _replay_frame(position=3.0, position_source="synthetic")
    ev = extract_evidence(_detection(), frame, _UNCALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.position_confidence == "unavailable"
    assert ev.position_m is None


def test_position_unavailable_when_none() -> None:
    frame = _replay_frame(position=None, position_source="unknown")
    ev = extract_evidence(_detection(), frame, _UNCALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.position_confidence == "unavailable"


def test_position_unavailable_for_unknown_source_even_with_a_value_present() -> None:
    # Exercises the "unknown" branch independently of the "position is None"
    # branch — a prior version of this test always paired them, so removing
    # "unknown" from the unavailable-source set went undetected.
    frame = _replay_frame(position=12.5, position_source="unknown")
    ev = extract_evidence(_detection(), frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.position_confidence == "unavailable"
    assert ev.position_m is None


def test_position_calibrated_when_capabilities_confirm_real_position() -> None:
    frame = _replay_frame(position=12.5, position_source="gps")
    ev = extract_evidence(_detection(), frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.position_confidence == "calibrated"
    assert ev.position_m == 12.5


def test_position_estimated_when_present_but_not_capability_confirmed() -> None:
    frame = _replay_frame(position=12.5, position_source="odometer")
    ev = extract_evidence(_detection(), frame, _UNCALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.position_confidence == "estimated"
    assert ev.position_m == 12.5


def test_position_estimated_gated_specifically_by_has_real_position() -> None:
    # Other capability flags being True must not make this "calibrated" —
    # only has_real_position gates it.
    frame = _replay_frame(position=12.5, position_source="gps")
    ev = extract_evidence(_detection(), frame, _POSITION_NOT_REAL_OTHERS_TRUE_CAPS, _EVIDENCE_CONFIG)
    assert ev.position_confidence == "estimated"


# --- amplitude ---


def test_amplitude_estimated_from_image_pixels_when_no_true_amplitude() -> None:
    frame = _replay_frame(image=np.full((640, 640), 42, dtype=np.uint8))
    ev = extract_evidence(_detection(), frame, _UNCALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.amplitude_confidence == "estimated"
    assert ev.amplitude == pytest.approx(42.0)


def test_amplitude_calibrated_from_traces_when_capability_and_traces_present() -> None:
    traces = np.full((300, 500), 7.0)
    frame = _replay_frame(image=np.zeros((640, 640), dtype=np.uint8), traces=traces)
    detection = _detection(bbox=(100.0, 200.0, 150.0, 260.0))
    ev = extract_evidence(detection, frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.amplitude_confidence == "calibrated"
    assert ev.amplitude == pytest.approx(7.0)


def test_amplitude_calibrated_samples_correct_trace_axis_not_swapped() -> None:
    # A uniform array can't catch a trace/sample axis swap (any axis choice
    # gives the same mean) — this has a distinct value only in the region
    # the bbox is supposed to select.
    traces = np.zeros((300, 500))
    traces[100:150, 200:260] = 9.0  # (trace, sample) axes, matching the bbox below
    frame = _replay_frame(image=np.zeros((640, 640), dtype=np.uint8), traces=traces)
    detection = _detection(bbox=(100.0, 200.0, 150.0, 260.0))
    ev = extract_evidence(detection, frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.amplitude == pytest.approx(9.0)


def test_amplitude_calibrated_even_for_near_zero_width_bbox() -> None:
    # _clip_range guarantees a >=1-wide slice from the index math, but the
    # underlying array can still yield an empty region in other ways (see
    # the zero-traces test below) — this specifically exercises the
    # index-clamping path on a degenerate bbox.
    traces = np.full((300, 500), 7.0)
    frame = _replay_frame(image=np.zeros((640, 640), dtype=np.uint8), traces=traces)
    detection = _detection(bbox=(100.0, 200.0, 100.4, 200.4))  # rounds to ~1px on both axes
    ev = extract_evidence(detection, frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.amplitude_confidence == "calibrated"
    assert ev.amplitude is not None


def test_amplitude_falls_back_to_estimated_when_traces_region_is_empty() -> None:
    # Zero traces -> any bbox slice on it is empty regardless of index
    # clamping, so this must fall through to the image-based estimate
    # rather than crash or silently report a NaN mean as "calibrated".
    traces = np.zeros((0, 500))
    image = np.full((640, 640), 55, dtype=np.uint8)
    frame = _replay_frame(image=image, traces=traces)
    ev = extract_evidence(_detection(), frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.amplitude_confidence == "estimated"
    assert ev.amplitude == pytest.approx(55.0)


def test_amplitude_estimated_samples_correct_image_axis_not_swapped() -> None:
    image = np.zeros((640, 640), dtype=np.uint8)
    image[200:260, 100:150] = 200  # (row=y, col=x) axes, matching the bbox below
    detection = _detection(bbox=(100.0, 200.0, 150.0, 260.0))
    frame = _replay_frame(image=image)
    ev = extract_evidence(detection, frame, _UNCALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.amplitude == pytest.approx(200.0)


def test_amplitude_falls_back_to_estimated_when_capability_true_but_no_traces() -> None:
    frame = _replay_frame(image=np.full((640, 640), 9, dtype=np.uint8), traces=None)
    ev = extract_evidence(_detection(), frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.amplitude_confidence == "estimated"
    assert ev.amplitude == pytest.approx(9.0)


def test_amplitude_unavailable_for_off_frame_bbox_with_no_overlap() -> None:
    # A bbox with zero real overlap with the frame must not be clamped to
    # one real edge pixel and reported as a genuine reading — that would be
    # exactly the kind of fabrication this module exists to prevent.
    detection = _detection(bbox=(-50.0, -50.0, -10.0, -10.0))  # entirely off-frame
    frame = _replay_frame(image=np.full((640, 640), 9, dtype=np.uint8))
    ev = extract_evidence(detection, frame, _UNCALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.amplitude_confidence == "unavailable"
    assert ev.amplitude is None


def test_amplitude_unavailable_for_traces_only_frame_without_image_shape() -> None:
    # No image_shape given -> no known mapping from the bbox onto frame.traces, regardless
    # of has_true_amplitude. Coordinate reconciliation needs image_shape specifically (see
    # the image_shape-provided tests below) — this pins the still-conservative default.
    frame = _replay_frame(image=None, traces=np.zeros((10, 500)))
    ev = extract_evidence(_detection(), frame, _UNCALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.amplitude_confidence == "unavailable"
    assert ev.amplitude is None


def test_amplitude_unavailable_for_traces_only_frame_even_with_true_amplitude_capability() -> None:
    # has_true_amplitude=True alone must NOT be enough to trust bbox-as-native-traces-indices
    # when frame.image is None and no image_shape was given — same "no image_shape" guard as
    # the test above, just also varying has_true_amplitude to prove it isn't the gate here.
    frame = _replay_frame(image=None, traces=np.full((300, 500), 7.0))
    ev = extract_evidence(_detection(), frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.amplitude_confidence == "unavailable"
    assert ev.amplitude is None


def test_amplitude_estimated_for_traces_only_frame_via_box_in_traces_reconciliation() -> None:
    # The coordinate-space fix: image_shape lets a traces-only frame's rendered-image bbox be
    # rescaled onto native (trace, sample) indices (detect.refine.box_in_traces) instead of
    # "unavailable". A uniform array can't catch an axis swap, so the value differs by axis —
    # see the swap-catching variant below for that.
    detection = _detection(bbox=_rendered_box(100, 110, 150, 170))
    traces = np.full((_N_TRACES, _N_SAMPLES), 7.0)
    frame = _replay_frame(image=None, traces=traces)
    ev = extract_evidence(
        detection, frame, _UNCALIBRATED_CAPS, _EVIDENCE_CONFIG, image_shape=_IMAGE_SHAPE
    )
    # has_true_amplitude is False on _UNCALIBRATED_CAPS: a real per-target reading from raw
    # traces, but the source hasn't vouched the units are physically meaningful.
    assert ev.amplitude_confidence == "estimated"
    assert ev.amplitude == pytest.approx(7.0)


def test_amplitude_calibrated_for_traces_only_frame_when_has_true_amplitude() -> None:
    detection = _detection(bbox=_rendered_box(100, 110, 150, 170))
    traces = np.full((_N_TRACES, _N_SAMPLES), 7.0)
    frame = _replay_frame(image=None, traces=traces)
    ev = extract_evidence(
        detection, frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG, image_shape=_IMAGE_SHAPE
    )
    assert ev.amplitude_confidence == "calibrated"
    assert ev.amplitude == pytest.approx(7.0)


def test_amplitude_for_traces_only_frame_samples_correct_axis_not_swapped() -> None:
    # traces is (n_traces, n_samples); box_in_traces returns (sample_slice, trace_slice), so
    # indexing traces.T[rows, cols] is required — traces[rows, cols] directly would silently
    # read the wrong region on a non-square box. Distinct value only where the box should land.
    traces = np.zeros((_N_TRACES, _N_SAMPLES))
    traces[100:110, 150:170] = 9.0  # (trace, sample) axes, matching the bbox below
    detection = _detection(bbox=_rendered_box(100, 110, 150, 170))
    frame = _replay_frame(image=None, traces=traces)
    ev = extract_evidence(
        detection, frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG, image_shape=_IMAGE_SHAPE
    )
    assert ev.amplitude == pytest.approx(9.0)


def test_depth_calibrated_rejects_non_positive_dielectric() -> None:
    detection = _detection(bbox=(0.0, 0.0, 10.0, 640.0))
    frame = _replay_frame(sample_interval_ns=0.1, dielectric_assumed=0.0)
    with pytest.raises(ValueError, match="dielectric_assumed"):
        extract_evidence(detection, frame, _FULLY_CALIBRATED_CAPS, _EVIDENCE_CONFIG)


# --- pass-through fields ---


def test_neighbours_and_prior_passes_pass_through() -> None:
    ev = extract_evidence(
        _detection(),
        _replay_frame(),
        _UNCALIBRATED_CAPS,
        _EVIDENCE_CONFIG,
        neighbours=("elongated_linear_target", "cavities"),
        prior_passes=({"survey_id": "s1"},),
    )
    assert ev.neighbours == ("elongated_linear_target", "cavities")
    assert ev.prior_passes == ({"survey_id": "s1"},)


def test_defaults_to_empty_neighbours_and_prior_passes() -> None:
    ev = extract_evidence(_detection(), _replay_frame(), _UNCALIBRATED_CAPS, _EVIDENCE_CONFIG)
    assert ev.neighbours == ()
    assert ev.prior_passes == ()
