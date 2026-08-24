"""Detection + ScanFrame + SourceCapabilities -> Evidence, with an honest calibrated/estimated/unavailable label on every uncertain field.

Design note on scope: depth/amplitude sampling here assumes bbox coordinates
are in frame.image's pixel space (true today — enhance() preserves shape,
and ReplaySource always populates frame.image directly). Session 6's
orchestrator now runs detection against render/bscan.py's rendered output
when frame.image is None (traces-only frames), but frame.image itself stays
None in that case — so _extract_depth and _extract_amplitude's calibrated-
via-traces branch both explicitly require frame.image is not None before
treating bbox coordinates as valid indices into frame.traces. That's a
conservative "fall back to unavailable rather than guess" choice, not a
real reconciliation of render/bscan.py's resized coordinate space with
frame.traces' native shape — no current or stubbed source can reach the
traces-only + has_true_amplitude=True combination this would otherwise
mis-sample, but revisit this note (not just the code) once one can.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from core.config import EvidenceConfig
from core.contracts import ConfidenceLevel, Detection, Evidence, ScanFrame, SourceCapabilities

_SPEED_OF_LIGHT_M_PER_NS = 0.2998  # c in vacuum — standard constant for GPR two-way travel time -> depth


def _bbox_y_center(detection: Detection) -> float:
    _, y1, _, y2 = detection.bbox_xyxy
    return (y1 + y2) / 2.0


def _clip_range(lo: float, hi: float, size: int) -> tuple[int, int] | None:
    """Clip [lo, hi) into valid [0, size) integer indices.

    Returns None if the range has no overlap with [0, size) at all (bbox
    entirely off-frame) — the caller must treat that as "no data", not clamp
    it to one real edge pixel/sample and report that as a genuine reading.
    """
    if size <= 0 or hi <= 0 or lo >= size:
        return None
    lo_i = max(0, min(int(lo), size - 1))
    hi_i = max(lo_i + 1, min(round(hi), size))
    return lo_i, hi_i


def _extract_depth(
    detection: Detection, frame: ScanFrame, capabilities: SourceCapabilities, config: EvidenceConfig
) -> tuple[float | None, ConfidenceLevel]:
    if frame.image is None:
        return None, "unavailable"

    image_height = frame.image.shape[0]
    fraction_of_height = min(max(_bbox_y_center(detection) / image_height, 0.0), 1.0)

    if (
        capabilities.has_calibrated_depth
        and frame.sample_interval_ns is not None
        and frame.dielectric_assumed is not None
    ):
        if frame.dielectric_assumed <= 0:
            raise ValueError(
                f"frame.dielectric_assumed must be positive, got {frame.dielectric_assumed}"
            )
        n_samples = frame.traces.shape[1] if frame.traces is not None else image_height
        sample_index = fraction_of_height * n_samples
        two_way_travel_time_ns = sample_index * frame.sample_interval_ns
        velocity_m_per_ns = _SPEED_OF_LIGHT_M_PER_NS / math.sqrt(frame.dielectric_assumed)
        depth_m = (two_way_travel_time_ns * velocity_m_per_ns) / 2.0
        return depth_m, "calibrated"

    # No calibration data: fall back to a labelled assumption rather than
    # reporting nothing — this is exactly what "estimated" means here.
    depth_m = fraction_of_height * config.assumed_max_depth_m
    return depth_m, "estimated"


def _extract_position(
    frame: ScanFrame, capabilities: SourceCapabilities
) -> tuple[float | None, ConfidenceLevel]:
    # "synthetic"/"unknown" position_source is a placeholder for downstream
    # code, not a real (even approximate) position — reporting it as
    # "estimated" would be fabricating a number, not estimating one.
    if frame.position is None or frame.position_source in {"synthetic", "unknown"}:
        return None, "unavailable"
    if capabilities.has_real_position:
        return frame.position, "calibrated"
    return frame.position, "estimated"


def _extract_amplitude(
    detection: Detection, frame: ScanFrame, capabilities: SourceCapabilities
) -> tuple[float | None, ConfidenceLevel]:
    x1, y1, x2, y2 = detection.bbox_xyxy

    # frame.image is not None is required here too: bbox coordinates are
    # only known to correspond to frame.traces' native (n_traces, n_samples)
    # shape in the scenario this module is actually tested against — a
    # source providing frame.image and frame.traces together. When
    # frame.image is None, detection ran against render/bscan.py's resized
    # output instead, whose coordinate space has no known relationship to
    # frame.traces' shape — see the module docstring.
    if capabilities.has_true_amplitude and frame.traces is not None and frame.image is not None:
        n_traces, n_samples = frame.traces.shape
        trace_range = _clip_range(x1, x2, n_traces)
        sample_range = _clip_range(y1, y2, n_samples)
        if trace_range is not None and sample_range is not None:
            trace_lo, trace_hi = trace_range
            sample_lo, sample_hi = sample_range
            region = frame.traces[trace_lo:trace_hi, sample_lo:sample_hi]
            if region.size:
                return float(np.abs(region).mean()), "calibrated"

    if frame.image is not None:
        height, width = frame.image.shape
        col_range = _clip_range(x1, x2, width)
        row_range = _clip_range(y1, y2, height)
        if col_range is not None and row_range is not None:
            col_lo, col_hi = col_range
            row_lo, row_hi = row_range
            region = frame.image[row_lo:row_hi, col_lo:col_hi]
            if region.size:
                # Pixel intensity from the source image, not a physical
                # amplitude — a relative proxy only, hence "estimated" not
                # "calibrated" even when the region samples cleanly.
                return float(region.mean()), "estimated"

    # Reachable: a traces-only frame (image=None) with has_true_amplitude
    # False, or an off-frame bbox with no overlap on either axis.
    return None, "unavailable"


def _hyperbola_width_px(detection: Detection) -> float:
    x1, _, x2, _ = detection.bbox_xyxy
    return x2 - x1


def extract_evidence(
    detection: Detection,
    frame: ScanFrame,
    capabilities: SourceCapabilities,
    config: EvidenceConfig,
    neighbours: tuple[str, ...] = (),
    prior_passes: tuple[Any, ...] = (),
) -> Evidence:
    """Build Evidence for one Detection.

    `neighbours` (other detection class names in the same frame) and
    `prior_passes` (findings at the same line/position from earlier surveys)
    are passed in rather than computed here: the former needs the full set
    of detections for the frame, the latter needs store/base.py, which
    doesn't exist until Session 6. This function stays a pure, storage-
    ignorant transform — the orchestrator assembles both before calling it.
    """
    depth_m, depth_confidence = _extract_depth(detection, frame, capabilities, config)
    position_m, position_confidence = _extract_position(frame, capabilities)
    amplitude, amplitude_confidence = _extract_amplitude(detection, frame, capabilities)

    return Evidence(
        detection_class=detection.class_name,
        detection_confidence=detection.confidence,
        depth_m=depth_m,
        depth_confidence=depth_confidence,
        position_m=position_m,
        position_confidence=position_confidence,
        amplitude=amplitude,
        amplitude_confidence=amplitude_confidence,
        hyperbola_width_px=_hyperbola_width_px(detection),
        neighbours=neighbours,
        prior_passes=prior_passes,
    )
