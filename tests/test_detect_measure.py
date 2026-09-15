"""Tests for detect/measure.py — envelope and echo-polarity measurements."""

from __future__ import annotations

import numpy as np
import pytest

from detect import measure
from detect.measure import (
    STRONG_AMPLITUDE_THRESHOLD,
    compute_normalized_envelope,
    echo_matches_direct_wave_polarity,
)

N_TRACES, N_SAMPLES = 384, 256
SAMPLE_INTERVAL_NS = 0.1  # the RAD channel — direct wave at sample 20, matching _ground() below


def _ground(seed: int = 0) -> np.ndarray:
    """Noisy traces (n_traces, n_samples) with a positive direct wave at sample 20."""
    traces = np.random.default_rng(seed).normal(0.0, 0.2, size=(N_TRACES, N_SAMPLES))
    traces[:, 18:23] += [-3.0, 4.0, 10.0, 4.0, -3.0]
    return traces


def _add_flat_echo(traces: np.ndarray, sample: int, lobes: list[float], first: int = 150, last: int = 200) -> np.ndarray:
    out = traces.copy()
    out[first:last, sample : sample + len(lobes)] += lobes
    return out


def test_envelope_is_time_down_distance_across() -> None:
    assert compute_normalized_envelope(_ground(), SAMPLE_INTERVAL_NS).shape == (N_SAMPLES, N_TRACES)


def test_envelope_blanks_the_direct_wave_rows() -> None:
    envelope = compute_normalized_envelope(_ground(), SAMPLE_INTERVAL_NS)
    skip = round(measure.DIRECT_WAVE_WINDOW_NS / SAMPLE_INTERVAL_NS)
    assert np.all(envelope[:skip] == 0)


def test_the_direct_wave_skip_is_a_time_not_a_record_fraction() -> None:
    # RAD/RA1/RA2 sample at 0.1/0.2/0.4 ns — the same DIRECT_WAVE_WINDOW_NS must blank a
    # different, smaller sample count on a coarser channel, not the same fraction of the
    # record. This is the exact bug the fixed-time constant replaced a 12%-of-samples rule
    # to fix (see detect/measure.py's DIRECT_WAVE_WINDOW_NS comment).
    fine = compute_normalized_envelope(_ground(), 0.1)
    coarse = compute_normalized_envelope(_ground(seed=0), 0.4)
    fine_skip = int(np.argmax(np.any(fine > 0, axis=1)))
    coarse_skip = int(np.argmax(np.any(coarse > 0, axis=1)))
    assert coarse_skip < fine_skip


def test_a_strong_isolated_reflector_clears_the_strong_amplitude_threshold() -> None:
    traces = _ground()
    traces[100:110, 120:126] += 8.0
    envelope = compute_normalized_envelope(traces, SAMPLE_INTERVAL_NS)
    assert envelope[120:126, 100:110].max() >= STRONG_AMPLITUDE_THRESHOLD
    assert np.median(envelope[150:, :]) < STRONG_AMPLITUDE_THRESHOLD


def test_an_echo_with_the_direct_waves_polarity_is_recognised() -> None:
    # Soil into air: a drop in permittivity keeps the pulse's polarity.
    traces = _add_flat_echo(_ground(), 120, [-2.0, 5.0, -2.0])
    assert echo_matches_direct_wave_polarity(traces, slice(110, 140), slice(150, 200), SAMPLE_INTERVAL_NS) is True


def test_a_reversed_echo_is_recognised_as_reversed() -> None:
    # Soil into water or onto metal: a rise in permittivity flips it.
    traces = _add_flat_echo(_ground(), 120, [2.0, -5.0, 2.0])
    assert echo_matches_direct_wave_polarity(traces, slice(110, 140), slice(150, 200), SAMPLE_INTERVAL_NS) is False


def test_side_lobes_do_not_decide_the_polarity() -> None:
    # A Ricker's leading side lobe (~45% of the main lobe) arrives first; reading it
    # would report the opposite polarity from the echo's real one.
    traces = _add_flat_echo(_ground(), 120, [-2.2, 5.0, -2.2])
    assert echo_matches_direct_wave_polarity(traces, slice(110, 140), slice(150, 200), SAMPLE_INTERVAL_NS) is True


def test_incoherent_scatter_has_no_readable_polarity() -> None:
    # Broken-up ground: echoes at random times and signs cancel in the stack, and the
    # honest answer is "cannot tell", not a coin flip.
    traces = _ground()
    rng = np.random.default_rng(5)
    for trace in range(150, 200):
        traces[trace, int(rng.integers(110, 140))] += float(rng.choice([-6.0, 6.0]))
    assert echo_matches_direct_wave_polarity(traces, slice(110, 140), slice(150, 200), SAMPLE_INTERVAL_NS) is None


def test_an_empty_box_has_no_polarity() -> None:
    assert echo_matches_direct_wave_polarity(_ground(), slice(10, 10), slice(0, 5), SAMPLE_INTERVAL_NS) is None


def test_non_positive_sample_interval_is_refused() -> None:
    with pytest.raises(ValueError, match="positive"):
        compute_normalized_envelope(_ground(), 0.0)


def test_hyperbola_half_aperture_grows_with_depth_and_never_drops_below_the_footprint() -> None:
    # Shared by the synthetic generator (object spacing, label priors) and the Studio
    # (boxing a picked apex). One definition, so the two cannot drift apart.
    assert measure.hyperbola_half_aperture_m(1.0) > measure.hyperbola_half_aperture_m(0.5)
    assert measure.hyperbola_half_aperture_m(1.0) == pytest.approx(1.0)
    assert measure.hyperbola_half_aperture_m(0.0) == measure.MIN_APERTURE_M
