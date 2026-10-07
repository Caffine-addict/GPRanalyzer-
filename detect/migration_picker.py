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

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from studio.processing import SPEED_OF_LIGHT_M_PER_NS, migrate, remove_background

logger = logging.getLogger(__name__)
DEFAULT_PICKS = 15
# Migration leaves faint copies ("smiles") beneath a strong target. A pick below a stronger one,
# within this lateral reach and weaker than this fraction of it, is taken as such an echo.
SHADOW_REACH_M = 0.5
SHADOW_RATIO = 0.3
_PEAK_OVER_MEDIAN = 3.0
_SKIP_TOP_NS = 1.0  # from the record start: before the direct wave, nothing is in the ground yet
# The direct wave's own lobe. Measured on the 23 company operator tags: skipping 0.5 ns below time
# zero loses none of them; 1.0 ns loses one, 1.5 ns four (operators tag very shallow targets).
SKIP_BELOW_TIME_ZERO_NS = 0.5
NEAR_SURFACE_NS = 1.5  # shallower than this a pick may still be the direct wave or surface clutter
_TIME_ZERO_SPREAD_NS = 1.0
_SKIP_BOTTOM_FRACTION = 0.1  # migration edge, where the spreading gain blows up


def time_zero_ns(traces: np.ndarray, sample_interval_ns: float) -> float:
    """When the direct wave arrives: the median over traces of each one's strongest early lobe.

    Per-trace, not on the mean trace: the direct wave is in every trace at the same time, while a
    strong shallow target only dominates the few traces above it, so the median ignores it. A wide
    spread means no consistent direct wave was found; it is logged rather than trusted silently.
    """
    # On the envelope, not |amplitude|: the direct wave has several lobes of similar size, and a
    # per-trace argmax hops between them (1.5-2 ns of spread on the company's mid channel).
    data = _envelope(np.asarray(traces, dtype=np.float64).T).T
    early = data[:, : max(1, data.shape[1] // 4)]
    arrivals = np.argmax(early, axis=1) * sample_interval_ns
    q1, q3 = np.percentile(arrivals, [25, 75])
    if q3 - q1 > max(_TIME_ZERO_SPREAD_NS, 5 * sample_interval_ns):  # a lobe's width on coarse channels
        logger.warning("time zero uncertain: direct-wave arrivals spread %.1f ns across traces", q3 - q1)
    return float(np.median(arrivals))



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
    zero = time_zero_ns(traces, sample_interval_ns)
    env[: int(max(_SKIP_TOP_NS, zero + SKIP_BELOW_TIME_ZERO_NS) / sample_interval_ns)] = 0
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
