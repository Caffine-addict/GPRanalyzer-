"""JSON-serialization helpers for the API layer. Finding/Evidence are frozen dataclasses with
their own validation already — this deliberately doesn't duplicate that as a parallel pydantic
schema, just converts to plain JSON-safe dicts.
"""

from __future__ import annotations

from typing import Any

from core.contracts import Evidence, Finding, SourceCapabilities


def evidence_to_dict(evidence: Evidence) -> dict[str, Any]:
    return {
        "detection_class": evidence.detection_class,
        "detection_confidence": evidence.detection_confidence,
        "depth_m": evidence.depth_m,
        "depth_confidence": evidence.depth_confidence,
        "position_m": evidence.position_m,
        "position_confidence": evidence.position_confidence,
        "amplitude": evidence.amplitude,
        "amplitude_confidence": evidence.amplitude_confidence,
        "hyperbola_width_px": evidence.hyperbola_width_px,
        "neighbours": list(evidence.neighbours),
    }


def finding_to_dict(finding: Finding) -> dict[str, Any]:
    return {
        "evidence": evidence_to_dict(finding.evidence),
        "risk_level": finding.risk_level,
        "risk_score": finding.risk_score,
        "risk_rules_fired": list(finding.risk_rules_fired),
        "what": finding.what,
        "where": finding.where,
        "why": finding.why,
        "how": finding.how,
        "recommended_action": finding.recommended_action,
        "reasoning_latency_ms": finding.reasoning_latency_ms,
    }


def capabilities_to_dict(capabilities: SourceCapabilities) -> dict[str, Any]:
    # The dashboard needs this to render honest confidence labels — never
    # show a bare number that implies measurement when the source can't
    # actually back it up.
    return {
        "has_calibrated_depth": capabilities.has_calibrated_depth,
        "has_real_position": capabilities.has_real_position,
        "has_true_amplitude": capabilities.has_true_amplitude,
        "latency_class": capabilities.latency_class,
    }
