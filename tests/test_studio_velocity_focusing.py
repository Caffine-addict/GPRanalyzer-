"""Tests for estimate_velocity_by_focusing — velocity measured by how well migration collapses it.

This is the second independent route to a velocity. `fit_region` fits ridge points with RANSAC and
can be fooled by a tight fit on few inliers; focusing uses every sample in the window and cannot be,
but it blurs in heterogeneous ground. The point of having both is that they fail differently, so
agreement between them means considerably more than either alone.
"""

from __future__ import annotations

import numpy as np
import pytest

from studio.velocity import (
    SWEEP_MAX_VELOCITY_M_PER_NS,
    SWEEP_MIN_VELOCITY_M_PER_NS,
    estimate_velocity_by_focusing,
)

TRACE_SPACING_M = 0.025  # SPR_SHAFT_INTERVAL on all four delivered lines
SAMPLE_INTERVAL_NS = 0.1  # the RAD channel


_WAVELET = np.array([0.25, 1.0, 0.25])  # a 3-sample pulse, not a single-sample delta spike


def _synthetic_hyperbola(
    velocity_m_per_ns: float,
    *,
    apex_trace: int = 100,
    apex_sample: int = 150,
    n_traces: int = 200,
    n_samples: int = 256,
    amplitude: float = 1.0,
    noise: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    """A radargram (n_samples, n_traces) containing one point reflector's hyperbola.

    t(x) = sqrt(t0^2 + (2x/v)^2) in physical units, written back into sample indices — the same
    curve `migrate` assumes, so a sweep must recover the velocity it was drawn with. The apex sits
    deep in the record (sample 150 of 256) and each arrival is a 3-sample wavelet rather than a
    single-sample spike, matching real reflection data. The record is deep enough that the apex
    itself and its near limbs are never clipped; the far ends of the aperture can still run off
    the bottom of the record at the slowest trial velocities, same as on real data — that's fine,
    those offsets just contribute nothing rather than biasing the result.
    """
    rng = np.random.default_rng(seed)
    data = rng.normal(0.0, noise, size=(n_samples, n_traces)) if noise else np.zeros((n_samples, n_traces))
    t0_ns = apex_sample * SAMPLE_INTERVAL_NS
    for trace in range(n_traces):
        lateral_m = abs(trace - apex_trace) * TRACE_SPACING_M
        travel_ns = np.sqrt(t0_ns**2 + (2.0 * lateral_m / velocity_m_per_ns) ** 2)
        row = round(travel_ns / SAMPLE_INTERVAL_NS)
        for k, w in enumerate(_WAVELET, start=-1):
            r = row + k
            if 0 <= r < n_samples:
                data[r, trace] += amplitude * w
    return data


def _sweep(data: np.ndarray, **overrides):
    kwargs = {
        "apex_trace": 100,
        "apex_sample": 150,
        "trace_spacing_m": TRACE_SPACING_M,
        "sample_interval_ns": SAMPLE_INTERVAL_NS,
    }
    kwargs.update(overrides)
    return estimate_velocity_by_focusing(data, **kwargs)


# --------------------------------------------------------------- it recovers a known velocity


@pytest.mark.parametrize("true_velocity", [0.06, 0.10, 0.13])
def test_the_sweep_recovers_the_velocity_a_hyperbola_was_drawn_with(true_velocity: float) -> None:
    data = _synthetic_hyperbola(true_velocity)
    result = _sweep(data)
    # The sweep is discrete (49 steps over 0.03-0.15, so ~0.0025 m/ns apart); one step of error is
    # the resolution floor, not a failure.
    assert result.best_velocity_m_per_ns == pytest.approx(true_velocity, abs=0.006)
    assert result.well_constrained is True


def test_the_recovered_permittivity_matches_the_recovered_velocity() -> None:
    # eps = (c/v)^2 — the reported dielectric must be derived from the winning velocity, not
    # separately computed and left free to drift from it.
    data = _synthetic_hyperbola(0.10)
    result = _sweep(data)
    expected = (0.2998 / result.best_velocity_m_per_ns) ** 2
    assert result.best_dielectric == pytest.approx(expected, rel=1e-9)


def test_a_site_like_velocity_reads_back_as_a_site_like_permittivity() -> None:
    # v = 0.0999 m/ns is eps 9, the value in the SPR file headers.
    data = _synthetic_hyperbola(0.0999)
    result = _sweep(data)
    assert 7.0 <= result.best_dielectric <= 11.5


# --------------------------------------------------------------- it says when it does not know


def test_pure_noise_produces_a_flat_curve_rather_than_a_confident_answer() -> None:
    # The important failure mode: a sweep always has an argmax, so without the peak ratio it would
    # report a velocity for noise with exactly the same confidence as for a real hyperbola.
    rng = np.random.default_rng(1)
    data = rng.normal(0.0, 1.0, size=(200, 200))
    result = _sweep(data)
    assert result.well_constrained is False


def test_a_real_hyperbola_is_far_better_constrained_than_noise() -> None:
    signal = _sweep(_synthetic_hyperbola(0.10, noise=0.05))
    rng = np.random.default_rng(2)
    noise = _sweep(rng.normal(0.0, 1.0, size=(200, 200)))
    assert signal.peak_ratio > noise.peak_ratio


def test_the_whole_energy_curve_is_returned_not_just_the_winner() -> None:
    # A sharp peak and a flat curve give the same best velocity and mean different things.
    result = _sweep(_synthetic_hyperbola(0.10))
    assert len(result.energies) == len(result.velocities_m_per_ns)
    assert len(result.energies) > 10
    assert result.energies[int(np.argmax(result.energies))] == max(result.energies)


def test_the_winning_velocity_is_the_one_with_the_most_focused_energy() -> None:
    result = _sweep(_synthetic_hyperbola(0.10))
    best_index = result.velocities_m_per_ns.index(result.best_velocity_m_per_ns)
    assert result.energies[best_index] == max(result.energies)


# --------------------------------------------------------------- guards


def test_the_sweep_range_brackets_ordinary_ground() -> None:
    # eps 4 (dry sand) to eps ~100 (wetter than anything ordinary). The site's eps 9 sits inside.
    assert SWEEP_MIN_VELOCITY_M_PER_NS < 0.0999 < SWEEP_MAX_VELOCITY_M_PER_NS


def test_a_custom_velocity_set_is_honoured() -> None:
    data = _synthetic_hyperbola(0.10)
    result = _sweep(data, velocities_m_per_ns=[0.08, 0.10, 0.12])
    assert result.velocities_m_per_ns == (0.08, 0.10, 0.12)
    assert result.best_velocity_m_per_ns == 0.10


def test_non_positive_trial_velocities_are_refused() -> None:
    data = _synthetic_hyperbola(0.10)
    with pytest.raises(ValueError, match="positive values"):
        _sweep(data, velocities_m_per_ns=[0.05, 0.0, 0.1])


def test_an_empty_velocity_set_is_refused() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        _sweep(_synthetic_hyperbola(0.10), velocities_m_per_ns=[])


def test_a_one_dimensional_input_is_refused() -> None:
    with pytest.raises(ValueError, match="n_samples, n_traces"):
        _sweep(np.zeros(100))


def test_non_positive_geometry_is_refused() -> None:
    data = _synthetic_hyperbola(0.10)
    for bad in ({"trace_spacing_m": 0.0}, {"sample_interval_ns": -0.1}):
        with pytest.raises(ValueError, match="positive"):
            _sweep(data, **bad)


def test_a_target_at_the_very_edge_is_refused_rather_than_silently_cropped() -> None:
    # A one-column window would "focus" trivially and report nonsense.
    data = _synthetic_hyperbola(0.10)
    with pytest.raises(ValueError, match="too small"):
        _sweep(data, apex_trace=0, apex_sample=0, half_width_traces=1, half_height_samples=1)
