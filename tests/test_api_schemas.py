"""Tests for api/schemas.py."""

from __future__ import annotations

from api.schemas import capabilities_to_dict, evidence_to_dict, finding_to_dict
from core.contracts import Evidence, Finding, SourceCapabilities


def _evidence(**overrides: object) -> Evidence:
    base: dict[str, object] = {
        "detection_class": "cavities",
        "detection_confidence": 0.8,
        "depth_m": 0.5,
        "depth_confidence": "estimated",
        "position_m": None,
        "position_confidence": "unavailable",
        "amplitude": None,
        "amplitude_confidence": "unavailable",
        "hyperbola_width_px": 10.0,
        "neighbours": ("elongated_linear_target",),
    }
    base.update(overrides)
    return Evidence(**base)  # type: ignore[arg-type]


def test_evidence_to_dict_includes_every_field_and_lists_neighbours() -> None:
    d = evidence_to_dict(_evidence())
    assert d["detection_class"] == "cavities"
    assert d["depth_m"] == 0.5
    assert d["depth_confidence"] == "estimated"
    assert d["position_m"] is None
    assert d["position_confidence"] == "unavailable"
    assert d["neighbours"] == ["elongated_linear_target"]  # tuple -> list for JSON


def test_finding_to_dict_nests_evidence_and_includes_risk_and_reasoning_fields() -> None:
    finding = Finding(
        evidence=_evidence(),
        risk_level="HIGH",
        risk_score=0.7,
        risk_rules_fired=("cavities_with_utility",),
        what="a reflector",
        recommended_action="confirm with second pass",
        reasoning_latency_ms=123.4,
    )
    d = finding_to_dict(finding)
    assert d["evidence"]["detection_class"] == "cavities"
    assert d["risk_level"] == "HIGH"
    assert d["risk_score"] == 0.7
    assert d["risk_rules_fired"] == ["cavities_with_utility"]
    assert d["what"] == "a reflector"
    assert d["where"] is None  # no reasoning for this field yet
    assert d["recommended_action"] == "confirm with second pass"
    assert d["reasoning_latency_ms"] == 123.4


def test_capabilities_to_dict() -> None:
    caps = SourceCapabilities(
        has_calibrated_depth=False,
        has_real_position=True,
        has_true_amplitude=False,
        latency_class="realtime",
    )
    d = capabilities_to_dict(caps)
    assert d == {
        "has_calibrated_depth": False,
        "has_real_position": True,
        "has_true_amplitude": False,
        "latency_class": "realtime",
    }
