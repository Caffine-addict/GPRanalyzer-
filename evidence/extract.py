"""Detection + ScanFrame + SourceCapabilities -> Evidence, with an honest calibrated/estimated/unavailable label on every uncertain field.

Design note on scope: bbox coordinates are in whatever image detection actually ran against —
`frame.image` when it exists, or the `render.bscan.traces_to_image` render when it doesn't
(Session 6's orchestrator falls back to that for traces-only frames, but leaves `frame.image`
itself None). Reconciling the second case needs to know that render's shape, which no field on
`ScanFrame` carries — so `_extract_depth`/`_extract_amplitude` both take `image_shape` as an
explicit parameter and use `detect.refine.box_in_traces` (the same rescale `detect/refine.py`
already uses for its own amplitude/polarity measurements) to map the box back onto samples/traces
when `frame.image is None`. `image_shape=None` with `frame.traces is not None` — a source that
supplies traces without ever having rendered an image from them — still falls back to
"unavailable" rather than guess.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from core.config import EvidenceConfig
from core.contracts import ConfidenceLevel, Detection, Evidence, ScanFrame, SourceCapabilities
from detect.refine import box_in_traces

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


def _sample_position(
    detection: Detection, frame: ScanFrame, image_shape: tuple[int, ...] | None
) -> tuple[float, int] | None:
    """Where this detection's box centres, in native sample units — (sample_index, n_samples).

    None means "no known mapping onto samples" — a bbox in some image whose relationship to
    frame.traces isn't known (frame.image is None and image_shape wasn't given).
    """
    if frame.image is not None:
        image_height = frame.image.shape[0]
        n_samples = frame.traces.shape[1] if frame.traces is not None else image_height
        fraction = min(max(_bbox_y_center(detection) / image_height, 0.0), 1.0)
        return fraction * n_samples, n_samples
    if frame.traces is not None and image_shape is not None:
        n_samples = frame.traces.shape[1]
        rows, _cols = box_in_traces(detection.bbox_xyxy, image_shape, frame.traces.shape)
        return (rows.start + rows.stop) / 2.0, n_samples
    return None


def _extract_depth(
    detection: Detection,
    frame: ScanFrame,
    capabilities: SourceCapabilities,
    config: EvidenceConfig,
    image_shape: tuple[int, ...] | None,
) -> tuple[float | None, ConfidenceLevel]:
    position = _sample_position(detection, frame, image_shape)
    if position is None:
        return None, "unavailable"
    sample_index, n_samples = position
    fraction_of_height = min(max(sample_index / n_samples, 0.0), 1.0) if n_samples > 0 else 0.0

    if frame.sample_interval_ns is not None and frame.dielectric_assumed is not None:
        if frame.dielectric_assumed <= 0:
            raise ValueError(
                f"frame.dielectric_assumed must be positive, got {frame.dielectric_assumed}"
            )
        two_way_travel_time_ns = sample_index * frame.sample_interval_ns
        velocity_m_per_ns = _SPEED_OF_LIGHT_M_PER_NS / math.sqrt(frame.dielectric_assumed)
        depth_m = (two_way_travel_time_ns * velocity_m_per_ns) / 2.0
        # The formula is the same regardless of source; what makes a depth "calibrated"
        # rather than merely "estimated from a real formula" is whether the velocity that
        # went into it was actually measured (a Studio hyperbola fit) rather than assumed
        # (SPR_MEDIUM_DIELECTRIC, an operator-dialled header value) — capabilities says which.
        return depth_m, "calibrated" if capabilities.has_calibrated_depth else "estimated"

    # No dielectric/sample-rate data at all: fall back to a labelled assumption rather than
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
    detection: Detection, frame: ScanFrame, capabilities: SourceCapabilities, image_shape: tuple[int, ...] | None
) -> tuple[float | None, ConfidenceLevel]:
    x1, y1, x2, y2 = detection.bbox_xyxy

    # Bbox coordinates are only known to correspond directly to frame.traces' native
    # (n_traces, n_samples) shape when frame.image is *also* populated from the same
    # source — the scenario this branch was written for (no current source actually
    # reaches it: image sources don't carry traces, and traces-only sources go through
    # the box_in_traces branch below instead).
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

    # frame.image is None: detection ran against render.bscan.traces_to_image's render, whose
    # box coordinates map onto frame.traces only through box_in_traces (see module docstring) —
    # same rescale detect/refine.py already uses for this exact frame.image-is-None situation.
    if frame.traces is not None and frame.image is None and image_shape is not None:
        rows, cols = box_in_traces(detection.bbox_xyxy, image_shape, frame.traces.shape)
        region = frame.traces.T[rows, cols]  # traces.T is (n_samples, n_traces), matching rows/cols
        if region.size:
            # Real per-target amplitude in the instrument's own units, not a pixel-intensity
            # proxy — but only "calibrated" when the source vouches those units are physically
            # meaningful (has_true_amplitude); otherwise it's still a better number than the
            # pixel path would give, just not one the source has promised is in real units.
            confidence: ConfidenceLevel = "calibrated" if capabilities.has_true_amplitude else "estimated"
            return float(np.abs(region).mean()), confidence

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

    # Reachable: a traces-only frame with no image_shape (no render happened), or an
    # off-frame bbox with no overlap on either axis.
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
    corroborating_channels: int = 1,
    image_shape: tuple[int, ...] | None = None,
) -> Evidence:
    """Build Evidence for one Detection.

    `neighbours` (other detection class names in the same frame) and
    `prior_passes` (findings at the same line/position from earlier surveys)
    are passed in rather than computed here: the former needs the full set
    of detections for the frame, the latter needs store/base.py, which
    doesn't exist until Session 6. This function stays a pure, storage-
    ignorant transform — the orchestrator assembles both before calling it.

    `image_shape` is the shape of whatever image `detection.bbox_xyxy` was measured against —
    required (for a real depth/amplitude reading rather than "unavailable") whenever
    `frame.image is None`, exactly as `detect.refine.refine_detections` already requires it.
    """
    depth_m, depth_confidence = _extract_depth(detection, frame, capabilities, config, image_shape)
    position_m, position_confidence = _extract_position(frame, capabilities)
    amplitude, amplitude_confidence = _extract_amplitude(detection, frame, capabilities, image_shape)

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
        # Passed in, like neighbours and prior_passes, and for the same reason: it needs every
        # channel's fits together, which this storage-ignorant transform does not have.
        corroborating_channels=corroborating_channels,
    )
