"""Traces -> normalised 640x640 image, for sources that carry raw traces instead of a rendered B-scan.

TODO(uncalibrated): this is a placeholder normalisation (per-frame min-max
scale + resize) only. A real B-scan render needs machine metadata —
antenna_freq_mhz, sample_interval_ns, dielectric_assumed on ScanFrame — to
convert sample index to depth and apply proper gain correction. No current
source populates that metadata (ReplaySource can't; it replays plain image
files). Until a real source provides it, every call here logs a warning
that the output is uncalibrated rather than silently producing a
plausible-looking but physically meaningless image.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from core.contracts import ScanFrame

logger = logging.getLogger(__name__)

TARGET_SIZE = 640  # matches the detector's expected input size (Session 3: detect/model.py), not a tunable


def traces_to_image(traces: np.ndarray) -> np.ndarray:
    """Normalise raw traces (n_traces, n_samples) into a TARGET_SIZE x TARGET_SIZE uint8 image."""
    if traces.ndim != 2:
        raise ValueError(f"traces_to_image() expects shape (n_traces, n_samples), got {traces.shape}")
    if np.isnan(traces).any():
        # NaN survives min-max scaling and np.clip (NaN comparisons are
        # always False) straight through to the uint8 cast, silently
        # producing a plausible-looking but meaningless pixel there — exactly
        # what this module's own "never silently produce wrong output"
        # principle forbids. +-inf are fine (np.clip handles them below);
        # NaN specifically means that sample is unusable, not renderable.
        raise ValueError("traces_to_image() received traces containing NaN — cannot render")

    logger.warning(
        "render.bscan uncalibrated traces_shape=%s — no antenna/dielectric metadata available; "
        "output is a min-max-scaled placeholder, not a calibrated B-scan",
        traces.shape,
    )

    finite = traces[np.isfinite(traces)]
    if finite.size == 0:
        raise ValueError("traces_to_image() received traces with no finite values")

    lo, hi = float(finite.min()), float(finite.max())
    if hi <= lo:
        normalised = np.zeros_like(traces, dtype=np.uint8)
    else:
        scaled = np.clip((traces - lo) / (hi - lo), 0.0, 1.0)
        normalised = (scaled * 255).astype(np.uint8)

    # Radargram orientation: time runs down the image, distance across it — the way a
    # B-scan is read and the way B-scan image files arrive through parsers/image.py.
    # `traces` is (n_traces, n_samples), so it has to be transposed first; resizing it
    # as-is drew time *across* the image, and a detector would have seen hyperbolas
    # opening sideways on trace-rendered frames but downward on image-file frames.
    # Caught and fixed before any detector was trained on either.
    upright = np.ascontiguousarray(normalised.T)
    return cv2.resize(upright, (TARGET_SIZE, TARGET_SIZE), interpolation=cv2.INTER_LINEAR)


# Display gain for display_strip: amplitudes past this percentile of |background-removed signal|
# saturate. 99 keeps the strongest reflectors from flattening everything else to mid-grey.
_DISPLAY_CLIP_PERCENTILE = 99.0


def display_strip(frame: ScanFrame) -> np.ndarray:
    """A frame as a greyscale radargram for a person to look at: (rows=time, cols=distance), uint8.

    For display only — nothing measures off it. Traces get the mean trace subtracted (removes
    the flat direct-wave band that otherwise hides everything) and a symmetric gain around
    mid-grey; an image frame is shown as it arrived. Native resolution, unlike traces_to_image,
    so one column is one trace and a streamed window lines up with its trace_offset exactly.
    """
    if frame.traces is None:
        assert frame.image is not None, "ScanFrame guarantees traces or image"
        return frame.image
    signal = np.nan_to_num(frame.traces.astype(np.float64))
    signal = (signal - signal.mean(axis=0, keepdims=True)).T
    scale = float(np.percentile(np.abs(signal), _DISPLAY_CLIP_PERCENTILE)) or 1.0
    return np.clip(128.0 + 127.0 * signal / scale, 0, 255).astype(np.uint8)
