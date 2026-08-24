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

    return cv2.resize(normalised, (TARGET_SIZE, TARGET_SIZE), interpolation=cv2.INTER_LINEAR)
