"""The instrument the synthetic data has to imitate: the SPRScan 3D shallow channel, as measured.

There is no spec sheet. Every number here was measured off the four real survey lines in
`Dataset/DSU_GPR_Files/` (RAD channel), and `tests/test_simulate_instrument.py`
re-measures them from the data so they cannot drift away from it unnoticed. A synthetic
frame that misses these numbers teaches the detector about some other radar.

One value is *not* measured and is named as an assumption: the transmitter-receiver
offset. Nothing in a B-scan pins it down and no spec gives it.
"""

from __future__ import annotations

import numpy as np

TRACE_SPACING_M = 0.025  # SPR_SHAFT_INTERVAL, identical on all four lines
SAMPLE_INTERVAL_NS = 0.1  # RAD channel: SPR_SAMPLING_INTERVAL 100, read as picoseconds
SAMPLES_PER_TRACE = 256  # SPR_SAMPLES_PER_SCAN
TRACES_PER_FRAME = 384  # 9.6 m; the real lines are 381-393 traces long

# Spectral peak of the RAD channel on all four lines (-6 dB band roughly 210-550 MHz): a
# standard utility-locating antenna. It is also a check on reading the sampling interval
# as picoseconds — in nanoseconds the same spectrum would peak at 0.47 MHz.
CENTRE_FREQUENCY_MHZ = 466.0

DIRECT_WAVE_PEAK_SAMPLE = 20  # measured at samples 19-22 across the four lines
NOISE_TO_PEAK_SCATTER = 0.009  # measured 0.007-0.011: deepest 4 ns vs the strongest reflection
# Direct wave against the strongest echo, measured the same way on the four lines: 1.18,
# 0.40, 0.75, 0.53. The median stands in for a frame with no echo to scale noise against.
DIRECT_TO_PEAK_ECHO = 0.64

# Brightness of the background-removed signal at each sample, relative to its peak: the
# median across the four lines. Synthetic frames are gained to follow it, so targets fade
# with depth the way this instrument actually shows them.
DEPTH_PROFILE_KNOTS: tuple[tuple[int, float], ...] = (
    (10, 0.552),
    (20, 0.799),
    (40, 0.680),
    (60, 0.423),
    (80, 0.317),
    (120, 0.140),
    (160, 0.074),
    (200, 0.053),
    (250, 0.030),
)

# Assumption, not a measurement: typical of shielded 400-500 MHz utility antennas. The
# scene generator varies it around this value so the detector cannot learn one geometry.
ASSUMED_ANTENNA_OFFSET_M = 0.1


def depth_profile(n_samples: int = SAMPLES_PER_TRACE) -> np.ndarray:
    """The measured depth profile at every sample, log-linear between the knots."""
    samples, values = zip(*DEPTH_PROFILE_KNOTS, strict=True)
    return np.exp(np.interp(np.arange(n_samples), samples, np.log(values)))
