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
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from core.config import Config, RiskConfig
from core.contracts import ConfidenceLevel, Detection, Evidence
from detect.measure import hyperbola_half_aperture_m
from detect.refine import refine_detections
from detect.shapes import POINT_REFLECTOR
from evidence.quality import quality_level
from risk.score import RiskAssessment, score_detections
from studio import corroborate as corroboration
from studio import picks as pick_store
from studio import session
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


def corroborating_channels(picks: Sequence[Pick], target_id: str, trace_spacing_m: float) -> int:
    """How many distinct channels independently saw this pick's target.

    Built from every pick on the job, not just the one channel being interpreted —
    corroboration across receivers is the entire point, so it cannot be computed from one
    channel's picks. A pick that clusters with nothing returns 1: itself, seen once.

    `trace_spacing_m` comes from the channel header (`SPR_SHAFT_INTERVAL`) rather than a
    constant: it is 0.025 m on all four delivered lines, but hardcoding it here would
    silently produce wrong positions for any job recorded with a different encoder setting.
    """
    apexes = [
        corroboration.Apex(
            id=pick.id, channel=pick.channel, position_m=pick.trace * trace_spacing_m, depth_m=pick.depth_m
        )
        for pick in picks
    ]
    for cluster in corroboration.corroborate(apexes):
        if target_id in cluster.apex_ids:
            return cluster.n_channels
    return 1


_EXPORT_HEADER = [
    "id", "channel", "trace", "sample", "chainage_m", "depth_m", "depth_confidence",
    "taxonomy_class", "class_rule", "corroborating_channels",
    "risk_level", "risk_score", "quality_level", "quality_rationale",
    "velocity_m_per_ns", "velocity_source", "dielectric", "fit_r2",
    "label", "note", "created_at",
]


def export_target_list(job_name: str, job_dir: Path, config: Config) -> list[list[str]]:
    """Every picked target in a job as CSV-ready rows — the measured half only.

    Deliberately carries no map coordinate. The onboard GPS in this project's delivered
    data cannot be trusted to place a target (see the 2026-09-15 GPS diagnostic: 3 of 4
    lines' receivers reported a healthy fix while the logged position never moved, and the
    one line that did move disagreed with the wheel-encoder line length by 12%, growing to
    over a metre of drift by the far end). `chainage_m` — distance along the line from a
    wheel encoder — is the only position measurement here that has been checked and holds
    up; putting anything past it in this file would be reporting a number nobody has
    verified as if it were fit to dig by.

    Runs no reasoning model: every value here is exact and reproducible from stored data
    alone, unlike `/picks/{id}/interpret`'s written half.

    A channel whose frame no longer parses (a moved/deleted file) drops just that
    channel's picks from the export rather than failing the whole job's list — the same
    "skip what can't be measured" choice `studio/diagnose.py` already makes.
    """
    picks = pick_store.load_picks(job_name)
    if not picks:
        return [_EXPORT_HEADER]

    picks_by_channel: dict[str, list[Pick]] = {}
    for pick in picks:
        picks_by_channel.setdefault(pick.channel, []).append(pick)

    rows_by_id: dict[str, list[str]] = {}
    for channel, channel_picks in picks_by_channel.items():
        try:
            frame = session.load_frame(job_dir, channel)
            info = session.describe_channel(frame, channel)
            traces = session.raw_traces(frame)
        except (session.JobNotFoundError, ValueError):
            continue

        by_id = evidence_for_line(channel_picks, info, traces, config.detection.taxonomy)
        risk = risk_for_line(list(by_id.values()), config.risk)
        for pick in channel_picks:
            item = by_id.get(pick.id)
            if item is None:
                continue
            channels = corroborating_channels(picks, pick.id, info.trace_spacing_m)
            evidence = replace(item.evidence, corroborating_channels=channels)
            # post_processed=False: this export runs on raw traces, same as /interpret.
            grade = quality_level(evidence, corroborating_channels=channels, post_processed=False)
            rows_by_id[pick.id] = [
                pick.id,
                pick.channel,
                f"{pick.trace:.2f}",
                f"{pick.sample:.2f}",
                f"{pick.trace * info.trace_spacing_m:.3f}",
                f"{pick.depth_m:.3f}",
                item.evidence.depth_confidence,
                item.taxonomy_class,
                item.class_rule,
                str(channels),
                risk.level,
                f"{risk.score:.3f}",
                grade.label,
                grade.rationale,
                f"{pick.velocity_m_per_ns:.5f}",
                pick.velocity_source,
                f"{pick.dielectric:.2f}",
                "" if pick.fit_r2 is None else f"{pick.fit_r2:.3f}",
                pick.label,
                pick.note,
                pick.created_at,
            ]

    # Original pick order, not grouped by channel — a stable, predictable file regardless of
    # which channel happened to be processed first.
    ordered = [rows_by_id[pick.id] for pick in picks if pick.id in rows_by_id]
    return [_EXPORT_HEADER, *ordered]
