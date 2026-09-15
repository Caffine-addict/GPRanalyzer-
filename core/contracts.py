"""ScanFrame, Detection, Evidence, Finding, SourceCapabilities — the shared vocabulary every package depends on."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

ConfidenceLevel = Literal["calibrated", "estimated", "unavailable"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]
LatencyClass = Literal["realtime", "near_realtime", "batch"]

# Literal[...] is a static-only annotation — dataclasses don't check membership
# at runtime, so these mirror the Literal values for __post_init__ checks.
_CONFIDENCE_LEVELS: frozenset[str] = frozenset({"calibrated", "estimated", "unavailable"})
_RISK_LEVELS: frozenset[str] = frozenset({"LOW", "MEDIUM", "HIGH"})
_LATENCY_CLASSES: frozenset[str] = frozenset({"realtime", "near_realtime", "batch"})


@dataclass(frozen=True, eq=False)
class ScanFrame:
    """One frame/line of GPR data as it comes off a source, before any processing.

    eq=False: frames carry numpy arrays, whose truthy-comparison under the
    dataclass-generated __eq__ would raise. Identity equality is what callers
    actually want here.
    """

    source_type: str
    provenance: dict[str, Any]

    traces: np.ndarray | None = None  # shape (n_traces, n_samples)
    image: np.ndarray | None = None

    position: float | None = None
    position_source: str | None = None

    antenna_freq_mhz: float | None = None
    sample_interval_ns: float | None = None
    dielectric_assumed: float | None = None

    def __post_init__(self) -> None:
        if self.traces is None and self.image is None:
            raise ValueError("ScanFrame requires traces, an image, or both — got neither")
        if self.position is not None and self.position_source is None:
            raise ValueError("position is set but position_source is missing")
        # The reverse (position_source set, position=None) is intentionally
        # allowed: parsers that have no coordinate at all still record why,
        # e.g. parsers/image.py sets position_source="unknown" with no
        # position — that's a real, honest provenance statement, not a gap.


@dataclass(frozen=True)
class Detection:
    """A single YOLO detection in image space."""

    class_name: str
    confidence: float
    bbox_xyxy: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
        x1, y1, x2, y2 = self.bbox_xyxy
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"bbox_xyxy must satisfy x2>x1 and y2>y1, got {self.bbox_xyxy}")


@dataclass(frozen=True)
class Evidence:
    """What the reasoning layer receives — never pixels.

    Every uncertain field pairs with a confidence label. A value must be
    None when its confidence is "unavailable", and must be present
    otherwise — fabricating a plausible number to fill a field is exactly
    what this invariant exists to prevent.
    """

    detection_class: str
    detection_confidence: float

    depth_m: float | None
    depth_confidence: ConfidenceLevel

    position_m: float | None
    position_confidence: ConfidenceLevel

    amplitude: float | None
    amplitude_confidence: ConfidenceLevel

    # Unlike depth/position/amplitude, this has no confidence tier — it's a
    # raw bbox pixel measurement, always derivable from any valid Detection
    # (whose __post_init__ guarantees x2>x1), not a calibration-dependent
    # physical quantity. Non-Optional deliberately: the one real producer
    # (evidence/extract.py) never yields None, and reason/prompt.py formats
    # it unconditionally with no None-guard — allowing None here would be
    # representable-but-unhandled state, not a real uncertainty case.
    hyperbola_width_px: float
    neighbours: tuple[str, ...] = ()
    prior_passes: tuple[Any, ...] = ()

    # How many *independent* receivers recorded this target and agreed it is there. 1 means one
    # channel saw it, which is the ordinary case and is not corroboration. This exists because the
    # strongest evidence this project can produce — the same reflector on two receivers with
    # different time axes, fitted separately, agreeing on position and depth — was being computed
    # and then hidden from every consumer that matters. `evidence/quality.py` needs it to reach
    # QL-B1, and the reasoning prompt needs it or the model writes "no prior passes, so this is the
    # first observation" about a target two receivers just confirmed.
    corroborating_channels: int = 1

    def __post_init__(self) -> None:
        for value, confidence, name in (
            (self.depth_m, self.depth_confidence, "depth"),
            (self.position_m, self.position_confidence, "position"),
            (self.amplitude, self.amplitude_confidence, "amplitude"),
        ):
            if confidence not in _CONFIDENCE_LEVELS:
                raise ValueError(
                    f"{name}_confidence must be one of {sorted(_CONFIDENCE_LEVELS)}, got {confidence!r}"
                )
            if confidence == "unavailable" and value is not None:
                raise ValueError(f"{name} is 'unavailable' but a value was supplied: {value!r}")
            if confidence != "unavailable" and value is None:
                raise ValueError(f"{name}_confidence is {confidence!r} but no value was supplied")

        # The `float` type hint alone is a static-only guarantee — nothing
        # stops a caller that doesn't respect mypy (a DuckDB row, an API
        # request body, hand-built test data) from passing None at runtime,
        # and reason/prompt.py formats this field with no None-guard.
        if self.hyperbola_width_px is None:
            raise ValueError("hyperbola_width_px must not be None")

        # A target was recorded at least once, by definition — 0 or negative is not a weaker
        # claim, it is an impossible one, and it would understate the grade rather than overstate
        # it, which is the direction that hides real evidence.
        if self.corroborating_channels < 1:
            raise ValueError(
                f"corroborating_channels must be at least 1, got {self.corroborating_channels}"
            )


@dataclass(frozen=True)
class Finding:
    """A risk-scored detection. Reasoning fields populate on a second, async pass.

    risk_level/risk_score/risk_rules_fired are deliberately frame-wide, not
    computed from this Finding's own Evidence alone: risk/score.py's
    escalation rules (e.g. "cavities + utility co-occurrence") need to see
    every detection in the frame together, which is impossible from a
    single detection's evidence in isolation. Every Finding built from the
    same frame shares the same risk assessment by design — see
    pipeline/orchestrator.py, which calls risk.score.score_detections() once
    per frame and applies the result to each of that frame's Findings.

    Frozen, so an "update" after reasoning completes is a new Finding built
    via dataclasses.replace(), not a mutation — consistent with treating
    findings as an immutable audit trail.
    """

    evidence: Evidence
    risk_level: RiskLevel
    risk_score: float
    risk_rules_fired: tuple[str, ...] = ()

    what: str | None = None
    where: str | None = None
    why: str | None = None
    how: str | None = None
    recommended_action: str | None = None
    reasoning_latency_ms: float | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.risk_score <= 1.0:
            raise ValueError(f"risk_score must be in [0, 1], got {self.risk_score}")
        if self.risk_level not in _RISK_LEVELS:
            raise ValueError(f"risk_level must be one of {sorted(_RISK_LEVELS)}, got {self.risk_level!r}")


@dataclass(frozen=True)
class SourceCapabilities:
    """What a ScanSource honestly claims about the data it produces."""

    has_calibrated_depth: bool
    has_real_position: bool
    has_true_amplitude: bool
    latency_class: LatencyClass

    def __post_init__(self) -> None:
        if self.latency_class not in _LATENCY_CLASSES:
            raise ValueError(
                f"latency_class must be one of {sorted(_LATENCY_CLASSES)}, got {self.latency_class!r}"
            )
