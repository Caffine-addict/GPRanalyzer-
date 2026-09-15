"""Tests for detect.hyperbola — hyperbola fitting and shape-based diagnosis.

Uses synthetic data with known ground truth throughout, not just the real Job_0703 hyperbola
used to validate this approach during development (see conversation/commit history) — a
synthetic fixture lets assertions pin exact expected parameters, matching this project's own
established lesson that only exact-value assertions catch real regressions.
"""

from __future__ import annotations

import numpy as np
import pytest

from detect.hyperbola import (
    diagnose_box,
    estimate_velocity,
    extract_ridge_points,
    fit_hyperbola_ransac,
    local_coherence,
)
from detect.measure import DIRECT_WAVE_WINDOW_NS, compute_normalized_envelope


def _synthetic_hyperbola_envelope(
    x0: float, t0: float, k: float, n_traces: int = 200, n_samples: int = 150, peak_value: float = 6.0
) -> np.ndarray:
    """A normalized-envelope-shaped array with a sharp ridge exactly on the hyperbola
    t(x) = sqrt(t0^2 + ((x-x0)/k)^2), near-zero elsewhere."""
    env = np.zeros((n_samples, n_traces), dtype=np.float32)
    for x in range(n_traces):
        t = np.sqrt(t0**2 + ((x - x0) / k) ** 2)
        ti = round(t)
        if 0 <= ti < n_samples:
            env[ti, x] = peak_value
    return env


def test_extract_ridge_points_finds_the_synthetic_hyperbola_exactly() -> None:
    env = _synthetic_hyperbola_envelope(x0=100, t0=20, k=0.5, n_traces=200, n_samples=150)
    xs, ts = extract_ridge_points(env, x=70, y=0, w=60, h=100)

    assert len(xs) > 20
    for x, t in zip(xs, ts):
        expected_t = np.sqrt(20**2 + ((x - 100) / 0.5) ** 2)
        assert abs(t - expected_t) <= 1.0


def test_extract_ridge_points_skips_columns_with_no_real_peak() -> None:
    env = np.zeros((100, 50), dtype=np.float32)
    env[40, 10] = 6.0  # only one column has a real peak above the threshold
    xs, ts = extract_ridge_points(env, x=0, y=0, w=50, h=100)
    assert list(xs) == [10.0]
    assert list(ts) == [40.0]


def test_compute_normalized_envelope_returns_all_zero_for_silent_traces() -> None:
    traces = np.zeros((20, 50))
    env = compute_normalized_envelope(traces, sample_interval_ns=0.1)
    assert np.all(env == 0)


def test_fit_hyperbola_ransac_recovers_true_parameters() -> None:
    true_x0, true_t0, true_k = 100.0, 20.0, 0.5
    env = _synthetic_hyperbola_envelope(true_x0, true_t0, true_k, n_traces=200, n_samples=150)
    xs, ts = extract_ridge_points(env, x=70, y=0, w=60, h=100)

    fit = fit_hyperbola_ransac(xs, ts)

    assert fit is not None
    assert fit.r2 > 0.99
    assert fit.x0 == pytest.approx(true_x0, abs=1.0)
    assert fit.t0 == pytest.approx(true_t0, abs=1.0)
    assert fit.k == pytest.approx(true_k, rel=0.05)


def test_fit_hyperbola_ransac_returns_none_for_pure_noise() -> None:
    rng = np.random.default_rng(42)
    xs = np.arange(60, dtype=np.float64)
    ts = rng.uniform(0, 100, size=60)  # no hyperbolic structure at all

    fit = fit_hyperbola_ransac(xs, ts)

    assert fit is None


def test_fit_hyperbola_ransac_requires_at_least_three_points() -> None:
    assert fit_hyperbola_ransac(np.array([1.0, 2.0]), np.array([5.0, 6.0])) is None


def test_estimate_velocity_matches_hand_derivation() -> None:
    from detect.hyperbola import HyperbolaFit

    # v = 2 * k * shaft_interval_m / sample_interval_ns (k in the numerator -- verified against
    # a real confirmed hyperbola's independently-computed physical-domain fit, see module notes)
    fit = HyperbolaFit(x0=0, t0=0, k=5.0, r2=1.0, n_inliers=10, n_total=10)
    v = estimate_velocity(fit, shaft_interval_m=0.025, sample_interval_ns=0.1)
    assert v == pytest.approx(2 * 5.0 * 0.025 / 0.1)


def test_estimate_velocity_is_physically_plausible_on_a_real_confirmed_hyperbola() -> None:
    """Regression test for a real inverted-formula bug: the fix was verified by comparing
    against a direct physical-units refit of Job_0703 RAD's known real hyperbola
    (trace ~311-337), which independently gave v=0.094 m/ns (implied dielectric ~10.1,
    close to the vendor's stated SPR_MEDIUM_DIELECTRIC=9.0). The buggy inverted formula gave
    2.65 m/ns here -- faster than light in vacuum (0.2998 m/ns), physically impossible."""
    from detect.hyperbola import HyperbolaFit

    fit = HyperbolaFit(x0=325.2, t0=45.1, k=0.189, r2=0.989, n_inliers=14, n_total=33)
    v = estimate_velocity(fit, shaft_interval_m=0.025, sample_interval_ns=0.1)
    assert v == pytest.approx(0.0945, rel=0.02)
    assert v < 0.2998  # must not exceed the speed of light in vacuum


def test_local_coherence_is_high_for_similar_adjacent_traces() -> None:
    base = np.sin(np.linspace(0, 4 * np.pi, 100))
    traces = np.tile(base, (30, 1)) + np.random.default_rng(0).normal(0, 0.01, (30, 100))
    assert local_coherence(traces, x=0, y=0, w=30, h=100) > 0.9


def test_local_coherence_is_zero_for_a_region_too_narrow_to_measure() -> None:
    traces = np.random.default_rng(9).normal(0, 1, (30, 100))
    assert local_coherence(traces, x=0, y=0, w=1, h=100) == 0.0
    assert local_coherence(traces, x=0, y=0, w=30, h=1) == 0.0


def test_local_coherence_skips_zero_variance_traces() -> None:
    traces = np.zeros((5, 50))
    traces[2] = np.sin(np.linspace(0, 4 * np.pi, 50))  # only one non-flat trace
    assert local_coherence(traces, x=0, y=0, w=5, h=50) == 0.0


def test_local_coherence_is_low_for_independent_random_traces() -> None:
    rng = np.random.default_rng(1)
    traces = rng.normal(0, 1, (30, 100))
    assert local_coherence(traces, x=0, y=0, w=30, h=100) < 0.3


def test_diagnose_box_identifies_a_clean_synthetic_hyperbola_as_point() -> None:
    n_traces, n_samples = 200, 150
    normalized = _synthetic_hyperbola_envelope(100, 20, 0.5, n_traces, n_samples, peak_value=6.0)
    traces = np.random.default_rng(2).normal(0, 1, (n_traces, n_samples))

    diagnosis = diagnose_box(
        traces, normalized, box_id="b1", x=70, y=0, w=60, h=100,
        shaft_interval_m=0.025, sample_interval_ns=0.1,
    )

    assert diagnosis.shape == "point"
    assert diagnosis.suggested_class == "clear_point_reflector"
    assert diagnosis.fit_r2 is not None and diagnosis.fit_r2 > 0.99
    assert diagnosis.velocity_m_per_ns is not None
    assert diagnosis.velocity_m_per_ns == pytest.approx(2 * 0.5 * 0.025 / 0.1, rel=1e-3)


def test_diagnose_box_identifies_low_amplitude_hyperbola_as_low_snr() -> None:
    n_traces, n_samples = 200, 150
    normalized = _synthetic_hyperbola_envelope(100, 20, 0.5, n_traces, n_samples, peak_value=2.0)
    traces = np.random.default_rng(3).normal(0, 1, (n_traces, n_samples))

    diagnosis = diagnose_box(
        traces, normalized, box_id="b2", x=70, y=0, w=60, h=100,
        shaft_interval_m=0.025, sample_interval_ns=0.1,
    )

    assert diagnosis.shape == "point"
    assert diagnosis.suggested_class == "low_snr_point_reflector"


def test_diagnose_box_identifies_a_flat_wide_band_as_linear() -> None:
    n_traces, n_samples = 200, 60
    normalized = np.zeros((n_samples, n_traces), dtype=np.float32)
    normalized[30, :] = 6.0  # constant depth across all traces -> no curvature at all
    traces = np.random.default_rng(4).normal(0, 1, (n_traces, n_samples))

    diagnosis = diagnose_box(
        traces, normalized, box_id="b3", x=0, y=0, w=180, h=40,
        shaft_interval_m=0.025, sample_interval_ns=0.1,
    )

    assert diagnosis.shape == "linear"
    assert diagnosis.suggested_class == "elongated_linear_target"


def test_diagnose_box_identifies_incoherent_scatter_as_disturbed() -> None:
    n_traces, n_samples = 60, 60
    rng = np.random.default_rng(5)
    normalized = np.zeros((n_samples, n_traces), dtype=np.float32)
    # scattered random peaks at random depths per column -- no coherent ridge, low box aspect ratio
    for x in range(n_traces):
        normalized[rng.integers(0, n_samples), x] = 6.0
    traces = rng.normal(0, 1, (n_traces, n_samples))  # independent noise -> low coherence

    diagnosis = diagnose_box(
        traces, normalized, box_id="b4", x=0, y=0, w=60, h=60,
        shaft_interval_m=0.025, sample_interval_ns=0.1,
    )

    assert diagnosis.shape == "disturbed"
    assert diagnosis.suggested_class == "disturbed_zone"


def test_compute_normalized_envelope_shape_and_zeroing() -> None:
    n_traces, n_samples = 50, 100
    rng = np.random.default_rng(7)
    traces = rng.normal(0, 1, (n_traces, n_samples))
    sample_interval_ns = 0.1

    env = compute_normalized_envelope(traces, sample_interval_ns)

    assert env.shape == (n_samples, n_traces)  # transposed: (sample, trace)
    skip = round(DIRECT_WAVE_WINDOW_NS / sample_interval_ns)
    assert np.all(env[:skip, :] == 0)  # near-surface direct-wave band excluded


def test_compute_normalized_envelope_highlights_a_real_anomaly_over_background() -> None:
    n_traces, n_samples = 50, 100
    rng = np.random.default_rng(8)
    traces = rng.normal(0, 0.1, (n_traces, n_samples))
    traces[20:25, 60] += 20.0  # a strong localized anomaly well below the skip zone

    env = compute_normalized_envelope(traces, sample_interval_ns=0.1)

    anomaly_region = env[55:65, 20:25]
    background_region = env[55:65, 0:5]
    assert anomaly_region.max() > background_region.max() * 3


def test_diagnose_box_nulls_out_a_physically_impossible_velocity() -> None:
    """Real dataset check found ~10% of well-scored (R²>=0.85) fits still imply v > c --
    a poorly-constrained curvature despite a decent-looking fit on few points. Must report
    unavailable, not an impossible number."""
    n_traces, n_samples = 200, 150
    # a broad (large k) synthetic hyperbola -- still fits cleanly (high R²) but implies an
    # unrealistically high velocity once shaft_interval_m/sample_interval_ns are applied
    normalized = _synthetic_hyperbola_envelope(100, 20, 1.0, n_traces, n_samples, peak_value=6.0)
    traces = np.random.default_rng(10).normal(0, 1, (n_traces, n_samples))

    diagnosis = diagnose_box(
        traces, normalized, box_id="b6", x=70, y=0, w=60, h=100,
        shaft_interval_m=0.025, sample_interval_ns=0.1,
    )

    assert diagnosis.shape == "point"  # the shape fit itself is still real and valid
    assert diagnosis.velocity_m_per_ns is None  # but the implied physical velocity is not
    assert diagnosis.implied_dielectric is None


def test_diagnose_box_handles_missing_calibration_gracefully() -> None:
    n_traces, n_samples = 200, 150
    normalized = _synthetic_hyperbola_envelope(100, 20, 0.5, n_traces, n_samples, peak_value=6.0)
    traces = np.random.default_rng(6).normal(0, 1, (n_traces, n_samples))

    diagnosis = diagnose_box(
        traces, normalized, box_id="b5", x=70, y=0, w=60, h=100,
        shaft_interval_m=None, sample_interval_ns=None,
    )

    assert diagnosis.shape == "point"
    assert diagnosis.velocity_m_per_ns is None
    assert diagnosis.implied_dielectric is None
