"""Validation tests for core/contracts.py.

Coverage tools reported 100% line/branch here before this file grew — but a
mutation-testing pass (deliberately breaking each check, one at a time, and
confirming the suite catches it) found five undetected regressions: missing
exact-boundary cases, only one of Evidence's three structurally-identical
fields ever being exercised, and no test pinning down the `or` in the bbox
check to both its operands independently. The tests below close those gaps
directly — see the mutation this test guards against in each docstring-like
comment where it isn't obvious from the name.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.contracts import Detection, Evidence, Finding, ScanFrame, SourceCapabilities


def test_scan_frame_requires_traces_or_image() -> None:
    with pytest.raises(ValueError, match="neither"):
        ScanFrame(source_type="replay", provenance={})


def test_scan_frame_valid_with_image_only() -> None:
    frame = ScanFrame(source_type="replay", provenance={}, image=np.zeros((640, 640)))
    assert frame.traces is None
    assert frame.image is not None


def test_scan_frame_valid_with_traces_only() -> None:
    frame = ScanFrame(source_type="replay", provenance={}, traces=np.zeros((10, 500)))
    assert frame.image is None


def test_scan_frame_valid_with_both_traces_and_image() -> None:
    frame = ScanFrame(
        source_type="replay",
        provenance={},
        traces=np.zeros((10, 500)),
        image=np.zeros((640, 640)),
    )
    assert frame.traces is not None
    assert frame.image is not None


def test_scan_frame_position_requires_position_source() -> None:
    with pytest.raises(ValueError, match="position_source"):
        ScanFrame(source_type="replay", provenance={}, image=np.zeros((1, 1)), position=1.0)


def test_scan_frame_position_with_source_is_valid() -> None:
    frame = ScanFrame(
        source_type="replay",
        provenance={},
        image=np.zeros((1, 1)),
        position=1.0,
        position_source="synthetic",
    )
    assert frame.position_source == "synthetic"


def test_scan_frame_position_source_without_position_is_valid() -> None:
    # Intentional, not a gap: parsers/image.py uses exactly this combination
    # (no coordinate available, position_source="unknown") to record an
    # honest provenance statement even when there's no position to attribute.
    frame = ScanFrame(
        source_type="image_file",
        provenance={},
        image=np.zeros((1, 1)),
        position=None,
        position_source="unknown",
    )
    assert frame.position is None
    assert frame.position_source == "unknown"


def test_detection_confidence_upper_bound_out_of_range_raises() -> None:
    with pytest.raises(ValueError, match="confidence"):
        Detection(class_name="cavities", confidence=1.5, bbox_xyxy=(0, 0, 1, 1))


def test_detection_confidence_lower_bound_out_of_range_raises() -> None:
    # Mutation guard: `0.0 <= confidence` -> `0.0 < confidence` would only be
    # caught by a case exercising the lower bound specifically.
    with pytest.raises(ValueError, match="confidence"):
        Detection(class_name="cavities", confidence=-0.1, bbox_xyxy=(0, 0, 1, 1))


def test_detection_confidence_exact_zero_is_valid() -> None:
    d = Detection(class_name="cavities", confidence=0.0, bbox_xyxy=(0, 0, 1, 1))
    assert d.confidence == 0.0


def test_detection_confidence_exact_one_is_valid() -> None:
    d = Detection(class_name="cavities", confidence=1.0, bbox_xyxy=(0, 0, 1, 1))
    assert d.confidence == 1.0


def test_detection_bad_bbox_both_degenerate_raises() -> None:
    with pytest.raises(ValueError, match="bbox_xyxy"):
        Detection(class_name="cavities", confidence=0.5, bbox_xyxy=(1, 1, 0, 0))


def test_detection_bad_bbox_x_degenerate_only_raises() -> None:
    # Mutation guard: `x2<=x1 or y2<=y1` -> `... and ...` would miss this,
    # since y2>y1 holds here but x2<=x1 alone must still raise.
    with pytest.raises(ValueError, match="bbox_xyxy"):
        Detection(class_name="cavities", confidence=0.5, bbox_xyxy=(5, 0, 5, 10))


def test_detection_bad_bbox_y_degenerate_only_raises() -> None:
    # Mirror of the x-only case, needed together with it to pin down `or`.
    with pytest.raises(ValueError, match="bbox_xyxy"):
        Detection(class_name="cavities", confidence=0.5, bbox_xyxy=(0, 5, 10, 5))


def test_detection_valid() -> None:
    d = Detection(class_name="cavities", confidence=0.9, bbox_xyxy=(0, 0, 10, 10))
    assert d.class_name == "cavities"


def _base_evidence_kwargs() -> dict:
    return {
        "detection_class": "cavities",
        "detection_confidence": 0.9,
        "depth_m": None,
        "depth_confidence": "unavailable",
        "position_m": None,
        "position_confidence": "unavailable",
        "amplitude": None,
        "amplitude_confidence": "unavailable",
        "hyperbola_width_px": 12.0,
    }


def test_evidence_depth_unavailable_with_value_raises() -> None:
    kwargs = _base_evidence_kwargs()
    kwargs["depth_m"] = 1.2
    with pytest.raises(ValueError, match="unavailable"):
        Evidence(**kwargs)


def test_evidence_depth_calibrated_without_value_raises() -> None:
    kwargs = _base_evidence_kwargs()
    kwargs["depth_confidence"] = "calibrated"
    with pytest.raises(ValueError, match="depth_confidence"):
        Evidence(**kwargs)


def test_evidence_all_unavailable_is_valid() -> None:
    ev = Evidence(**_base_evidence_kwargs())
    assert ev.depth_m is None
    assert ev.neighbours == ()


def test_evidence_rejects_none_hyperbola_width_px() -> None:
    # hyperbola_width_px has no confidence tier (it's a raw bbox
    # measurement, not an uncertain physical quantity) but must still never
    # be None — the `float` type hint alone doesn't stop a caller that
    # doesn't respect mypy from passing None at runtime.
    kwargs = _base_evidence_kwargs()
    kwargs["hyperbola_width_px"] = None
    with pytest.raises(ValueError, match="hyperbola_width_px"):
        Evidence(**kwargs)


def test_evidence_depth_estimated_with_value_is_valid() -> None:
    kwargs = _base_evidence_kwargs()
    kwargs["depth_m"] = 0.8
    kwargs["depth_confidence"] = "estimated"
    ev = Evidence(**kwargs)
    assert ev.depth_m == 0.8


# Mutation guard: __post_init__ loops over (depth, position, amplitude) as
# three structurally identical tuples. Every prior test only ever varied
# depth_*, leaving position_* and amplitude_* validated in name only (a bug
# that swapped which field a check applies to went undetected). These pin
# each field down independently.


def test_evidence_position_calibrated_without_value_raises() -> None:
    kwargs = _base_evidence_kwargs()
    kwargs["position_confidence"] = "calibrated"
    with pytest.raises(ValueError, match="position_confidence"):
        Evidence(**kwargs)


def test_evidence_position_unavailable_with_value_raises() -> None:
    kwargs = _base_evidence_kwargs()
    kwargs["position_confidence"] = "unavailable"
    kwargs["position_m"] = 5.0
    with pytest.raises(ValueError, match="unavailable"):
        Evidence(**kwargs)


def test_evidence_position_estimated_with_value_is_valid() -> None:
    kwargs = _base_evidence_kwargs()
    kwargs["position_confidence"] = "estimated"
    kwargs["position_m"] = 3.5
    ev = Evidence(**kwargs)
    assert ev.position_m == 3.5


def test_evidence_amplitude_calibrated_without_value_raises() -> None:
    kwargs = _base_evidence_kwargs()
    kwargs["amplitude_confidence"] = "calibrated"
    with pytest.raises(ValueError, match="amplitude_confidence"):
        Evidence(**kwargs)


def test_evidence_amplitude_unavailable_with_value_raises() -> None:
    kwargs = _base_evidence_kwargs()
    kwargs["amplitude_confidence"] = "unavailable"
    kwargs["amplitude"] = 10.0
    with pytest.raises(ValueError, match="unavailable"):
        Evidence(**kwargs)


def test_evidence_amplitude_estimated_with_value_is_valid() -> None:
    kwargs = _base_evidence_kwargs()
    kwargs["amplitude_confidence"] = "estimated"
    kwargs["amplitude"] = 10.0
    ev = Evidence(**kwargs)
    assert ev.amplitude == 10.0


def test_evidence_rejects_unrecognized_confidence_label() -> None:
    kwargs = _base_evidence_kwargs()
    kwargs["depth_confidence"] = "high"  # not one of calibrated/estimated/unavailable
    with pytest.raises(ValueError, match="depth_confidence"):
        Evidence(**kwargs)


def _finding_with_score(score: float, risk_level: str = "LOW") -> Finding:
    return Finding(
        evidence=Evidence(**_base_evidence_kwargs()),
        risk_level=risk_level,
        risk_score=score,
    )


def test_finding_risk_score_upper_bound_out_of_range_raises() -> None:
    with pytest.raises(ValueError, match="risk_score"):
        _finding_with_score(1.1)


def test_finding_risk_score_lower_bound_out_of_range_raises() -> None:
    # Mutation guard: the lower-bound clause was deletable without any
    # existing test noticing.
    with pytest.raises(ValueError, match="risk_score"):
        _finding_with_score(-0.1)


def test_finding_risk_score_exact_zero_is_valid() -> None:
    finding = _finding_with_score(0.0)
    assert finding.risk_score == 0.0


def test_finding_risk_score_exact_one_is_valid() -> None:
    finding = _finding_with_score(1.0)
    assert finding.risk_score == 1.0


def test_finding_rejects_unrecognized_risk_level() -> None:
    with pytest.raises(ValueError, match="risk_level"):
        _finding_with_score(0.2, risk_level="SEVERE")


def test_finding_valid_defaults_reasoning_fields_to_none() -> None:
    finding = _finding_with_score(0.2)
    assert finding.what is None
    assert finding.recommended_action is None


def test_source_capabilities_construction() -> None:
    caps = SourceCapabilities(
        has_calibrated_depth=False,
        has_real_position=False,
        has_true_amplitude=False,
        latency_class="batch",
    )
    assert caps.has_calibrated_depth is False


def test_source_capabilities_rejects_unrecognized_latency_class() -> None:
    with pytest.raises(ValueError, match="latency_class"):
        SourceCapabilities(
            has_calibrated_depth=False,
            has_real_position=False,
            has_true_amplitude=False,
            latency_class="instant",
        )


# --- corroborating_channels (2026-09-14) ------------------------------------


def test_evidence_defaults_to_one_corroborating_channel() -> None:
    # One receiver saw it. That is the ordinary case, and it is not corroboration — so the default
    # must be the weakest honest value, never an optimistic one.
    ev = Evidence(**_base_evidence_kwargs())
    assert ev.corroborating_channels == 1


def test_evidence_accepts_a_real_corroboration_count() -> None:
    kwargs = _base_evidence_kwargs()
    kwargs["corroborating_channels"] = 3
    assert Evidence(**kwargs).corroborating_channels == 3


def test_evidence_rejects_fewer_than_one_corroborating_channel() -> None:
    # A target was recorded at least once by definition. Zero is not a weaker claim, it is an
    # impossible one — and it would understate the survey grade, which is the direction that
    # hides real evidence rather than overstating it.
    for impossible in (0, -1):
        kwargs = _base_evidence_kwargs()
        kwargs["corroborating_channels"] = impossible
        with pytest.raises(ValueError, match="at least 1"):
            Evidence(**kwargs)
