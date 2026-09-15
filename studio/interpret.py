"""A picked target, turned into evidence a reasoning model can be trusted with.

The Studio's job is measurement. This module is the seam between what an interpreter
measured and the language layer that already exists for the live pipeline
(`reason/engine.py`). It deliberately reuses that seam rather than growing a second one:
a Pick becomes a Detection, `detect/refine.py` gives it a taxonomy class from
measurements, `risk/score.py` scores the line, and the same ReasoningEngine writes the
words.

One thing this path has that the live pipeline does not. A Pick whose velocity was
*fitted* carries a wave velocity measured from that target's own hyperbola, so its depth
is `"calibrated"` — the first calibrated evidence this project can honestly produce. A
pick whose velocity came from the file header stays `"estimated"`, and the prompt says so.

What is deliberately not carried across is the pick's free-text `label`. That is the
interpreter's hypothesis; letting it become the detection class would feed the model its
own conclusion back as evidence and call the result a classification.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from core.config import RiskConfig
from core.contracts import ConfidenceLevel, Detection, Evidence
from detect.measure import hyperbola_half_aperture_m
from detect.refine import refine_detections
from detect.shapes import POINT_REFLECTOR
from risk.score import RiskAssessment, score_detections
from studio.picks import Pick
from studio.session import ChannelInfo

# A pick is a target a human looked at and committed to, so there is no detector score to
# carry. `reason/prompts/v1_pick.txt` says who marked it rather than printing this as if a
# detector had scored it.
HUMAN_PICK_CONFIDENCE = 1.0

# Only a velocity measured from the target's own hyperbola makes its depth a measurement.
# "manual" is an operator's eye, "assumed" is the header's site setting — both estimates.
_DEPTH_CONFIDENCE: dict[str, ConfidenceLevel] = {
    "fitted": "calibrated",
    "manual": "estimated",
    "assumed": "estimated",
}

_APEX_MARGIN_SAMPLES = 2.0  # a little above the apex, so the box opens on the wavelet's start


@dataclass(frozen=True)
class PickEvidence:
    """One picked target as the reasoning layer sees it."""

    pick: Pick
    detection: Detection  # carrying the taxonomy class measured from the radargram
    evidence: Evidence
    class_rule: str  # why it got that class — shown to the operator, not just logged

    @property
    def taxonomy_class(self) -> str:
        return self.detection.class_name


def apex_box(pick: Pick, info: ChannelInfo) -> tuple[float, float, float, float]:
    """The target's own diffraction aperture as (trace0, sample0, trace1, sample1).

    Width is the aperture that `detect/measure.py` defines and the synthetic labeller uses,
    so a real pick and a simulated label describe a target the same way. Height is the
    hyperbola's own descent across that aperture — t(x)/t(0) = sqrt(1 + (x/d)^2) — rather
    than a fixed band, so a deep target gets a taller box because its limbs really do
    fall further.
    """
    half_m = hyperbola_half_aperture_m(pick.depth_m)
    half_traces = half_m / info.trace_spacing_m
    trace0 = max(0.0, pick.trace - half_traces)
    trace1 = min(float(info.n_traces), pick.trace + half_traces)

    descent = math.sqrt(1.0 + (half_m / pick.depth_m) ** 2) if pick.depth_m > 0 else math.sqrt(2.0)
    sample0 = max(0.0, pick.sample - _APEX_MARGIN_SAMPLES)
    sample1 = min(float(info.n_samples), max(pick.sample * descent, sample0 + 1.0))
    # Detection's own invariant is x2>x1 and y2>y1; a pick at the very edge of the record
    # would otherwise build a degenerate box and raise deep inside the refiner.
    return trace0, sample0, max(trace1, trace0 + 1.0), max(sample1, sample0 + 1.0)


def _amplitude(traces: np.ndarray, box: tuple[float, float, float, float]) -> float:
    trace0, sample0, trace1, sample1 = box
    region = traces[int(trace0) : max(int(trace1), int(trace0) + 1), int(sample0) : max(int(sample1), int(sample0) + 1)]
    return float(np.abs(region).mean()) if region.size else 0.0


def evidence_for_line(
    picks: Sequence[Pick], info: ChannelInfo, traces: np.ndarray, taxonomy: tuple[str, ...]
) -> dict[str, PickEvidence]:
    """Every pick on this channel, as evidence, keyed by pick id.

    The whole line is refined in one pass, not target by target: `detect/refine.py`'s
    "regularly spaced" and "crowded" rules exist to see a row of rebar or a duct bank as
    what it is, and neither is visible from one target alone.
    """
    on_channel = [pick for pick in picks if pick.channel == info.extension]
    if not on_channel:
        return {}

    boxes = [apex_box(pick, info) for pick in on_channel]
    detections = [
        Detection(class_name=POINT_REFLECTOR, confidence=HUMAN_PICK_CONFIDENCE, bbox_xyxy=box) for box in boxes
    ]
    # traces are (n_traces, n_samples); refine's box mapping wants the image shape it
    # would have been rendered at, which for native coordinates is the transpose.
    image_shape = (info.n_samples, info.n_traces)
    refinements = refine_detections(
        detections,
        taxonomy=taxonomy,
        image_shape=image_shape,
        traces=traces,
        sample_interval_ns=info.sample_interval_ns,
    )
    classes = [refinement.detection.class_name for refinement in refinements]

    results: dict[str, PickEvidence] = {}
    for index, (pick, box, refinement) in enumerate(zip(on_channel, boxes, refinements, strict=True)):
        evidence = Evidence(
            detection_class=refinement.detection.class_name,
            detection_confidence=HUMAN_PICK_CONFIDENCE,
            depth_m=pick.depth_m,
            depth_confidence=_DEPTH_CONFIDENCE[pick.velocity_source],
            position_m=pick.trace * info.trace_spacing_m,
            # The wheel encoder measured this: SPR_SHAFT_INTERVAL, 0.025 m per trace.
            # It is distance along the line, not a GPS fix — see studio/session.py.
            position_confidence="calibrated",
            amplitude=_amplitude(traces, box),
            # A relative proxy off the traces. No calibrated amplitude exists on this
            # instrument (docs/COMPANY_QUESTIONS.md #2), so it can never be "calibrated".
            amplitude_confidence="estimated",
            # Native traces, not pixels, despite the contract field's name: a pick's box is built
            # in (trace, sample) space. reason/prompt.py therefore prints this without a unit.
            # Multiply by info.trace_spacing_m for metres.
            hyperbola_width_px=box[2] - box[0],
            neighbours=tuple(name for other, name in enumerate(classes) if other != index),
        )
        results[pick.id] = PickEvidence(
            pick=pick, detection=refinement.detection, evidence=evidence, class_rule=refinement.rule
        )
    return results


def risk_for_line(items: Sequence[PickEvidence], config: RiskConfig) -> RiskAssessment:
    """One risk assessment for the whole line, shared by every target on it.

    `risk/score.py`'s escalation rules look for co-occurrence — a cavity alongside
    utilities — which is invisible from a single target. This mirrors the live pipeline,
    where every Finding from one frame shares that frame's assessment.
    """
    return score_detections([item.detection for item in items], config)
