"""Processed radargram -> displayable RGB image.

Kept separate from `studio/processing.py` on purpose: everything in processing
changes the *signal*, everything here changes only how it is *drawn*. That
split is what lets the viewer honestly label which controls alter the data
(gain, filters, migration) and which are pure display (palette, contrast).

Two deliberate choices:

**Nearest-neighbour resampling only.** Every pixel shown is a real sample drawn
larger, never a blend of two samples. Bilinear resizing invents intermediate
values that read as smooth continuous reflectors and would make a noisy
radargram look better resolved than it is — the same reasoning as in
`scripts/view_spr_scan.py`.

**Symmetric normalisation for diverging palettes.** With a palette where zero
means something, the mid-tone must actually land on zero, or a positive and a
negative reflection of equal strength get drawn as different-looking features.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from studio import palette as palette_module


@dataclass(frozen=True)
class DisplaySettings:
    """Pure display controls — none of these touch the underlying samples."""

    palette: str = palette_module.DEFAULT_PALETTE
    contrast_percentile: float = 98.0  # clip point; lower = harder clipping, more contrast
    brightness: float = 0.0  # -1..1, shifts the mid-tone
    width_px: int | None = None  # None = native trace count
    height_px: int | None = None


def normalise(data: np.ndarray, settings: DisplaySettings) -> np.ndarray:
    """Map a processed radargram onto 0-255 palette indices."""
    if data.size == 0:
        raise ValueError("cannot render an empty radargram")
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        raise ValueError("radargram has no finite samples to render")

    percentile = float(np.clip(settings.contrast_percentile, 50.0, 100.0))
    diverging = palette_module.get(settings.palette).is_diverging

    if diverging:
        limit = float(np.percentile(np.abs(finite), percentile))
        if limit <= 0:
            limit = float(np.max(np.abs(finite))) or 1.0
        scaled = (data / (2.0 * limit)) + 0.5
    else:
        lo = float(np.percentile(finite, 100.0 - percentile))
        hi = float(np.percentile(finite, percentile))
        scaled = (data - lo) / max(hi - lo, 1e-12)

    scaled = scaled + float(np.clip(settings.brightness, -1.0, 1.0)) * 0.5
    return np.rint(np.clip(scaled, 0.0, 1.0) * 255).astype(np.uint8)


def to_rgb(data: np.ndarray, settings: DisplaySettings) -> np.ndarray:
    """Render a processed radargram as an RGB image at the requested size."""
    indices = normalise(data, settings)
    coloured = palette_module.lut(settings.palette)[indices]  # (h, w, 3) RGB

    # `is None` rather than a falsy check: width_px=0 is a client asking for a
    # zero-width image, which is a mistake worth reporting, not a request for
    # the native size.
    target_w = coloured.shape[1] if settings.width_px is None else settings.width_px
    target_h = coloured.shape[0] if settings.height_px is None else settings.height_px
    if target_w < 1 or target_h < 1:
        raise ValueError(f"invalid render size {target_w}x{target_h}")
    if (target_w, target_h) == (coloured.shape[1], coloured.shape[0]):
        return coloured
    return cv2.resize(coloured, (target_w, target_h), interpolation=cv2.INTER_NEAREST)


def encode_png(rgb: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise RuntimeError("failed to PNG-encode radargram")
    return encoded.tobytes()


def trace_waveform(data: np.ndarray, trace_index: int) -> list[float]:
    """One A-scan (a single vertical trace) as a plain list, for the wiggle plot."""
    if not 0 <= trace_index < data.shape[1]:
        raise IndexError(f"trace {trace_index} out of range (0..{data.shape[1] - 1})")
    return [float(v) for v in data[:, trace_index]]
