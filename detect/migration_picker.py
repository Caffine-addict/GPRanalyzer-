"""Find buried targets the way the physics does: migrate, then pick the focused points.

A point reflector draws a hyperbola; Kirchhoff migration at the ground's velocity collapses it back
to a bright point at the apex. The picks are the strongest local maxima of the migrated envelope,
after a spreading gain (deeper echoes are weaker for geometric reasons alone).

Measured, not assumed (docs/EXTERNAL_DATA.md):
- company SPR lines with operator tags (25 tags, 2026-10-04): with 15 picks per line, a pick lies
  within 0.3 m and 1.5 ns of 52% of the tags on the mid channel, against 19% for the same picks at
  random positions; the earlier energy-envelope candidates found 35% (chance 17%) at best;
- Twente excavated surveys, picks on the GNSS map traced across lines: 17-18% of utilities found
  against 10-11% for scrambled positions.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from studio.processing import SPEED_OF_LIGHT_M_PER_NS, migrate, remove_background

DEFAULT_PICKS = 15
# Migration leaves faint copies ("smiles") beneath a strong target. A pick below a stronger one,
# within this lateral reach and weaker than this fraction of it, is taken as such an echo.
SHADOW_REACH_M = 0.5
SHADOW_RATIO = 0.3
_PEAK_OVER_MEDIAN = 3.0
_SKIP_TOP_NS = 1.0  # direct wave and antenna ringing
_SKIP_BOTTOM_FRACTION = 0.1  # migration edge, where the spreading gain blows up


@dataclass(frozen=True)
class Pick:
    trace: int
    sample: int
    strength: float  # migrated envelope over its median: how far above the background


def _envelope(data: np.ndarray) -> np.ndarray:
    """Instantaneous amplitude down each trace (axis 0), via the analytic signal."""
    n = data.shape[0]
    weights = np.zeros(n)
    weights[0] = 1
    weights[1:(n + 1) // 2] = 2
    if n % 2 == 0:
        weights[n // 2] = 1
    return np.abs(np.fft.ifft(np.fft.fft(data, axis=0) * weights[:, None], axis=0))


def pick_targets(
    traces: np.ndarray,
    *,
    sample_interval_ns: float,
    trace_spacing_m: float,
    dielectric: float = 9.0,
    aperture_m: float = 1.0,
    max_picks: int = DEFAULT_PICKS,
) -> list[Pick]:
    """The strongest focused points of a line, strongest first.

    `traces` is (n_traces, n_samples) with row 0 at time zero, as the file readers return it.
    `dielectric` sets the migration velocity; a wrong one still focuses hyperbolas partly, and
    the header's value or the Studio's measured one should be passed when known.
    """
    if traces.ndim != 2 or traces.shape[0] < 3 or traces.shape[1] < 8:
        raise ValueError(f"need a 2-D line of at least 3 traces x 8 samples, got {traces.shape}")
    if sample_interval_ns <= 0 or trace_spacing_m <= 0 or dielectric < 1:
        raise ValueError("sample interval and trace spacing must be positive, dielectric at least 1")
    data = remove_background(np.asarray(traces, dtype=np.float64).T, "mean", 0)
    velocity = SPEED_OF_LIGHT_M_PER_NS / np.sqrt(dielectric)
    migrated = migrate(data, trace_spacing_m=trace_spacing_m, sample_interval_ns=sample_interval_ns,
                       velocity_m_per_ns=velocity, aperture_traces=max(1, int(aperture_m / trace_spacing_m)))
    env = (_envelope(migrated) * (np.arange(migrated.shape[0])[:, None] + 1)).astype(np.float32)
    env[: int(_SKIP_TOP_NS / sample_interval_ns)] = 0
    env[int((1 - _SKIP_BOTTOM_FRACTION) * len(env)):] = 0
    positive = env[env > 0]
    if positive.size == 0:
        return []
    median = float(np.median(positive))
    window = (max(3, int(2.0 / sample_interval_ns)), max(3, int(0.4 / trace_spacing_m)))
    is_peak = (env == cv2.dilate(env, np.ones(window, np.uint8))) & (env > _PEAK_OVER_MEDIAN * median)
    rows, cols = np.nonzero(is_peak)
    kept: list[Pick] = []
    reach = SHADOW_REACH_M / trace_spacing_m
    for i in np.argsort(-env[rows, cols]):
        pick = Pick(int(cols[i]), int(rows[i]), float(env[rows[i], cols[i]] / median))
        if any(abs(k.trace - pick.trace) <= reach and k.sample < pick.sample and pick.strength < SHADOW_RATIO * k.strength
               for k in kept):
            continue  # an echo beneath a stronger target, not a target of its own
        kept.append(pick)
        if len(kept) == max_picks:
            break
    return kept
