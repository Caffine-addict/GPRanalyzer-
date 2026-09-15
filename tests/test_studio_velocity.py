"""Tests for studio/velocity.py — hyperbola fitting and the depth it implies."""

from __future__ import annotations

import numpy as np
import pytest

from studio.processing import SPEED_OF_LIGHT_M_PER_NS
from studio.velocity import (
    depth_from_time,
    dielectric_from_velocity,
    fit_region,
    hyperbola_curve,
    velocity_from_dielectric,
    velocity_from_limb_point,
)


def test_velocity_and_dielectric_are_inverses_of_each_other() -> None:
    assert velocity_from_dielectric(dielectric_from_velocity(0.1)) == pytest.approx(0.1)
    assert dielectric_from_velocity(velocity_from_dielectric(9.0)) == pytest.approx(9.0)


def test_dielectric_nine_gives_a_third_of_the_speed_of_light() -> None:
    # The header's SPR_MEDIUM_DIELECTRIC 9.00 is the assumption every depth in
    # the system currently rests on; pin the number it turns into.
    assert velocity_from_dielectric(9.0) == pytest.approx(SPEED_OF_LIGHT_M_PER_NS / 3)


def test_dielectric_below_vacuum_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be below 1"):
        velocity_from_dielectric(0.5)


def test_velocity_must_be_positive_to_imply_a_permittivity() -> None:
    with pytest.raises(ValueError, match="velocity must be positive"):
        dielectric_from_velocity(0.0)


def test_depth_is_half_the_two_way_path() -> None:
    assert depth_from_time(20.0, 0.1) == pytest.approx(1.0)


def test_negative_two_way_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        depth_from_time(-1.0, 0.1)


def test_hyperbola_curve_has_its_minimum_at_the_apex() -> None:
    curve = hyperbola_curve(
        apex_trace=100,
        apex_time_ns=10.0,
        velocity_m_per_ns=0.1,
        trace_spacing_m=0.025,
        n_traces=300,
    )
    apex = min(curve, key=lambda point: point["time_ns"])
    assert apex["trace"] == pytest.approx(100)
    assert apex["time_ns"] == pytest.approx(10.0)


def test_hyperbola_curve_is_symmetric_about_the_apex() -> None:
    curve = {
        point["trace"]: point["time_ns"]
        for point in hyperbola_curve(
            apex_trace=100, apex_time_ns=10.0, velocity_m_per_ns=0.1,
            trace_spacing_m=0.025, n_traces=300,
        )
    }
    assert curve[80] == pytest.approx(curve[120])


def test_hyperbola_curve_is_clipped_to_the_line() -> None:
    curve = hyperbola_curve(
        apex_trace=5, apex_time_ns=10.0, velocity_m_per_ns=0.1,
        trace_spacing_m=0.025, n_traces=30,
    )
    assert min(point["trace"] for point in curve) >= 0
    assert max(point["trace"] for point in curve) <= 29


def test_hyperbola_curve_rejects_a_non_positive_velocity() -> None:
    with pytest.raises(ValueError, match="velocity must be positive"):
        hyperbola_curve(
            apex_trace=10, apex_time_ns=5.0, velocity_m_per_ns=0.0,
            trace_spacing_m=0.025, n_traces=50,
        )


def test_limb_point_recovers_the_velocity_that_drew_the_curve() -> None:
    # Round trip: generate a curve at a known velocity, then read a point off
    # its limb and check the velocity comes back.
    velocity, spacing = 0.12, 0.025
    curve = hyperbola_curve(
        apex_trace=100, apex_time_ns=8.0, velocity_m_per_ns=velocity,
        trace_spacing_m=spacing, n_traces=300,
    )
    limb = next(point for point in curve if point["trace"] == 140)
    recovered = velocity_from_limb_point(
        apex_trace=100, apex_time_ns=8.0,
        limb_trace=limb["trace"], limb_time_ns=limb["time_ns"],
        trace_spacing_m=spacing,
    )
    assert recovered == pytest.approx(velocity, rel=1e-9)


def test_limb_point_directly_above_the_apex_is_rejected() -> None:
    with pytest.raises(ValueError, match="lateral to"):
        velocity_from_limb_point(
            apex_trace=100, apex_time_ns=8.0,
            limb_trace=100, limb_time_ns=12.0, trace_spacing_m=0.025,
        )


def test_limb_point_earlier_than_the_apex_is_rejected() -> None:
    with pytest.raises(ValueError, match="later than"):
        velocity_from_limb_point(
            apex_trace=100, apex_time_ns=8.0,
            limb_trace=140, limb_time_ns=4.0, trace_spacing_m=0.025,
        )


def _synthetic_hyperbola_traces(
    *, velocity: float, apex_trace: int, apex_time_ns: float,
    n_traces: int = 200, n_samples: int = 256,
    trace_spacing_m: float = 0.025, sample_interval_ns: float = 0.1,
) -> np.ndarray:
    """A radargram in source orientation (n_traces, n_samples) holding one hyperbola."""
    traces = np.random.default_rng(0).normal(size=(n_traces, n_samples)) * 0.5
    for trace in range(n_traces):
        lateral = abs(trace - apex_trace) * trace_spacing_m
        travel = np.sqrt(apex_time_ns**2 + (2 * lateral / velocity) ** 2)
        row = round(travel / sample_interval_ns)
        if row + 3 < n_samples:
            # A few samples wide, so the energy envelope has a ridge to find.
            traces[trace, row : row + 4] = [60.0, -60.0, 40.0, -20.0]
    return traces


def test_fit_region_recovers_the_velocity_of_a_synthetic_hyperbola() -> None:
    velocity = 0.1
    traces = _synthetic_hyperbola_traces(velocity=velocity, apex_trace=100, apex_time_ns=8.0)
    fit = fit_region(
        traces,
        trace_start=70, sample_start=60, trace_span=60, sample_span=120,
        trace_spacing_m=0.025, sample_interval_ns=0.1,
    )
    assert fit is not None
    assert fit.physically_plausible
    assert fit.velocity_m_per_ns == pytest.approx(velocity, rel=0.2)
    assert fit.r2 > 0.85


def test_fit_region_reports_no_fit_for_pure_noise_rather_than_inventing_one() -> None:
    noise = np.random.default_rng(7).normal(size=(200, 256))
    fit = fit_region(
        noise,
        trace_start=50, sample_start=60, trace_span=40, sample_span=80,
        trace_spacing_m=0.025, sample_interval_ns=0.1,
    )
    # None is a real answer — "no point reflector here" — not a failure.
    assert fit is None or fit.r2 < 0.85


def test_fit_region_flags_a_faster_than_light_result_instead_of_hiding_it() -> None:
    # A hyperbola far too flat for the ground implies superluminal propagation.
    # It must come back visible and labelled, so the operator can see why it is
    # nonsense rather than watch their pick silently vanish.
    traces = _synthetic_hyperbola_traces(velocity=0.9, apex_trace=100, apex_time_ns=8.0)
    fit = fit_region(
        traces,
        trace_start=70, sample_start=60, trace_span=60, sample_span=140,
        trace_spacing_m=0.025, sample_interval_ns=0.1,
    )
    if fit is not None and fit.velocity_m_per_ns > SPEED_OF_LIGHT_M_PER_NS:
        assert not fit.physically_plausible
        assert fit.dielectric == 1.0
        assert fit.depth_m == 0.0


def test_fit_region_rejects_a_region_too_small_to_describe_a_curve() -> None:
    with pytest.raises(ValueError, match="too small"):
        fit_region(
            np.zeros((50, 50)),
            trace_start=10, sample_start=10, trace_span=2, sample_span=2,
            trace_spacing_m=0.025, sample_interval_ns=0.1,
        )


def test_fit_region_rejects_a_non_positive_sample_interval() -> None:
    with pytest.raises(ValueError, match="positive trace spacing"):
        fit_region(
            np.zeros((50, 50)),
            trace_start=5, sample_start=5, trace_span=20, sample_span=20,
            trace_spacing_m=0.025, sample_interval_ns=0.0,
        )
