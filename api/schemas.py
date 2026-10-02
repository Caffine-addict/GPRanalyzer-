"""JSON-serialization helpers for the API layer. Finding/Evidence are frozen dataclasses with
their own validation already — this deliberately doesn't duplicate that as a parallel pydantic
schema, just converts to plain JSON-safe dicts.
"""

from __future__ import annotations

import base64
from typing import Any

from core.contracts import Evidence, Finding, ScanFrame, SourceCapabilities, TraceBox
from render.bscan import display_strip
from sources.intake import Intake


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
        "corroborating_channels": evidence.corroborating_channels,
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
        "location": location_to_dict(finding.location),
    }


def location_to_dict(location: TraceBox | None) -> dict[str, int] | None:
    if location is None:
        return None
    return {
        "trace_start": location.trace_start,
        "trace_end": location.trace_end,
        "sample_start": location.sample_start,
        "sample_end": location.sample_end,
    }


def frame_to_dict(frame: ScanFrame) -> dict[str, Any]:
    """What the live radargram needs to paint this frame where it belongs on its line.

    `pixels` is the display strip's raw uint8 bytes, row-major (rows = time), base64 — a few KB
    per chunk, drawn straight into a canvas with no decoding step.
    """
    strip = display_strip(frame)
    rows, cols = strip.shape[:2]
    path = frame.provenance.get("path")
    return {
        "line": path,
        "line_name": None if path is None else str(path).rsplit("/", 1)[-1],
        "trace_offset": frame.trace_offset or 0,
        "line_complete": frame.line_complete,
        "width": cols,
        "height": rows,
        "trace_spacing_m": frame.trace_spacing_m,
        "sample_interval_ns": frame.sample_interval_ns,
        "pixels": base64.b64encode(strip.tobytes()).decode("ascii"),
    }


def intake_to_dict(intake: Intake) -> dict[str, Any]:
    return {
        "files": [str(p) for p in intake.files],
        "skipped": [{"path": str(p), "reason": reason} for p, reason in intake.skipped],
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
