"""Classical candidate detector: background removal + energy envelope + threshold, no weights.

Finds visible reflector patterns in one channel's raw traces and boxes them, without a class.
Lives here rather than in `scripts/` so the live pipeline can import it (`detect/live.py`);
`scripts/detect_candidates.py` is the batch CLI over the same function. Every parameter was
tuned by rendering boxes over the real B-scans and checking them by eye — see that script's
docstring for the method.
"""

from __future__ import annotations

import cv2
import numpy as np

from detect.measure import direct_wave_skip_samples

_ENVELOPE_BLUR_KSIZE = (3, 17)  # (trace, sample) -- collapses the ~6-sample wavelet cycle
_ROW_BACKGROUND_FLOOR_FRACTION = 0.15  # guards depth-wise normalization against near-zero background rows
_THRESHOLD_PERCENTILE = 82.0
_CLOSE_KERNEL = (9, 5)  # (sample, trace) morphological closing to merge split fragments
_OPEN_KERNEL = (3, 3)  # strips thin single-trace noise streaks
_MIN_BOX_W, _MIN_BOX_H, _MIN_BOX_AREA = 8, 5, 150
_MAX_ASPECT_RATIO = 4.0  # combined with _NOISE_STREAK_MAX_WIDTH below -- see find_candidate_boxes
_NOISE_STREAK_MAX_WIDTH = 15  # a real point reflector's tail can be tall+narrow too (aspect > 4 alone
# isn't noise-specific -- confirmed by a real miss: a genuine strong reflector at trace 311-337 got
# discarded by aspect ratio alone before this width co-condition was added). True single/few-trace
# noise streaks measured earlier topped out around width 10-13; 15 leaves margin without also
# catching wide real features.


def find_candidate_boxes(
    traces: np.ndarray, sample_interval_ns: float | None
) -> list[tuple[int, int, int, int]]:
    """Return candidate (trace, sample, width, height) boxes for one channel's raw traces.

    Depth-wise (AGC-style) normalization is essential here, not optional:
    GPR signal attenuates with depth, so a flat energy threshold is
    systematically biased toward shallow near-surface clutter and misses
    real deeper reflectors entirely — confirmed by testing without it
    first (it caught noise, missed two clearly-visible hyperbolas) before
    adding this.

    `sample_interval_ns=None` means the rows have no known time scale (a radargram image, not
    recorded traces): no direct-wave band can be placed in time, so none is blanked.
    """
    _n_traces, n_samples = traces.shape
    mean_trace = traces.astype(np.float64).mean(axis=0, keepdims=True)
    residual = (traces.astype(np.float64) - mean_trace).T  # (n_samples, n_traces)
    power = (residual**2).astype(np.float32)

    envelope = cv2.blur(power, _ENVELOPE_BLUR_KSIZE)
    envelope = np.sqrt(np.clip(envelope, 0, None))

    # A time (DIRECT_WAVE_WINDOW_NS), not a fraction of the record — RAD/RA1/RA2 sample at
    # 0.1/0.2/0.4 ns, so a fraction-based skip was blanking 0.15/0.31/0.61 m respectively on
    # the same "12%". Shared conversion with detect/measure.py, not just the shared constant.
    skip = 0 if sample_interval_ns is None else direct_wave_skip_samples(n_samples, sample_interval_ns)
    envelope[:skip, :] = 0
    if not np.any(envelope > 0):
        return []

    row_background = np.median(envelope, axis=1, keepdims=True)
    floor = np.median(envelope[skip:]) * _ROW_BACKGROUND_FLOOR_FRACTION
    row_background = np.maximum(row_background, floor)
    normalized = (envelope / row_background).astype(np.float32)
    normalized[:skip, :] = 0

    nonzero = normalized[normalized > 0]
    norm_u8 = np.clip(normalized / np.percentile(nonzero, 99.5) * 255, 0, 255).astype(np.uint8)
    thresh_val = np.percentile(norm_u8[norm_u8 > 0], _THRESHOLD_PERCENTILE)
    _, mask = cv2.threshold(norm_u8, thresh_val, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones(_CLOSE_KERNEL, np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones(_OPEN_KERNEL, np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w < _MIN_BOX_W or h < _MIN_BOX_H or w * h < _MIN_BOX_AREA:
            continue
        if max(w, h) / min(w, h) > _MAX_ASPECT_RATIO and min(w, h) < _NOISE_STREAK_MAX_WIDTH:
            continue
        boxes.append((x, y, w, h))
    return boxes
