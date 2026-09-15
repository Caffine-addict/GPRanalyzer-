"""Tests for studio/velocity.py's fit_rejection_reason — which fits are refused, and which are not.

Rewritten 2026-09-14. The first version rejected any fit whose implied permittivity was not close
to the site's, which across the four real survey lines was the single largest cause of rejection:
42 of 158 fits, including eps 1.18 at 1.61 m on 40 inlier ridge points. Those are voids — the thing
a utility survey exists to find — thrown away for not resembling soil. Only physics refuses a fit
now; an unusual permittivity is a reading (`detect.measure.permittivity_signal`).

The second half of that fix is the direct-wave band. It used to be a fraction of each channel's
sample count, which meant the same antenna excluded 0.15 m on RAD, 0.31 m on RA1 and 0.61 m on RA2
— so the deep channel discarded every shallow utility by construction. It is a time now.
"""

from __future__ import annotations

import pytest

from detect.measure import (
    DIRECT_WAVE_WINDOW_NS,
    PERMITTIVITY_IMPLAUSIBLE_ABOVE,
)
from studio.velocity import (
    MIN_CREDIBLE_INLIERS,
    RECORD_FLOOR_FRACTION,
    VelocityFit,
    fit_rejection_reason,
)

N_SAMPLES = 256
RAD_INTERVAL = 0.1  # ns per sample on the shallow channel


def _fit(**overrides: object) -> VelocityFit:
    base: dict[str, object] = {
        "apex_trace": 340.0,
        "apex_sample": 130.0,  # comfortably inside the usable record
        "apex_time_ns": 13.0,  # and well past the direct wave
        "velocity_m_per_ns": 0.1,
        "dielectric": 9.0,
        "depth_m": 0.65,
        "r2": 0.98,
        "n_inliers": 40,
        "n_total": 60,
        "physically_plausible": True,
    }
    base.update(overrides)
    return VelocityFit(**base)  # type: ignore[arg-type]


def _reason(fit: VelocityFit, *, n_samples: int = N_SAMPLES, interval: float = RAD_INTERVAL):
    return fit_rejection_reason(fit, n_samples=n_samples, sample_interval_ns=interval)


# --------------------------------------------------------------------- what is still refused


def test_a_good_fit_is_not_rejected() -> None:
    assert _reason(_fit()) is None


def test_a_faster_than_light_fit_is_rejected() -> None:
    reason = _reason(_fit(physically_plausible=False))
    assert reason is not None
    assert "faster than light" in reason


def test_an_apex_inside_the_direct_wave_band_is_rejected() -> None:
    reason = _reason(_fit(apex_time_ns=DIRECT_WAVE_WINDOW_NS - 0.01))
    assert reason is not None
    assert "direct-wave" in reason


def test_an_apex_past_the_record_floor_is_rejected() -> None:
    # The project's own established target has exactly this problem on RAD: its apex sits 93% down
    # the record, so the limbs are truncated — the documented reason its depth disagreed with RA1.
    reason = _reason(_fit(apex_sample=0.93 * N_SAMPLES))
    assert reason is not None
    assert "truncated" in reason
    assert 0.93 > RECORD_FLOOR_FRACTION


def test_too_few_inliers_is_rejected_even_with_a_perfect_r2() -> None:
    # A tight fit on a handful of points is the documented trap: R2 0.997 on 10/26 inliers gave
    # eps 16 at 0.106 m, which was surface clutter.
    reason = _reason(_fit(r2=0.997, n_inliers=MIN_CREDIBLE_INLIERS - 1))
    assert reason is not None
    assert "inlier" in reason


def test_a_permittivity_beyond_water_is_rejected() -> None:
    # Nothing ordinary propagates slower than water. Above this the curvature is unconstrained,
    # so the fit is not describing a material at all.
    reason = _reason(_fit(dielectric=PERMITTIVITY_IMPLAUSIBLE_ABOVE + 10))
    assert reason is not None
    assert "beyond water" in reason


# --------------------------------------------------------------------- what must NOT be refused


def test_an_air_like_permittivity_is_kept_because_that_is_a_void() -> None:
    """The regression this rewrite exists for.

    eps 1.18 at 1.61 m on 40 inlier points is a well-constrained measurement saying the wave
    crossed air. That is a void or an air-filled duct, and `cavities` is the second-highest
    weighted class in the risk model. The old rule discarded it for not looking like soil.
    """
    assert _reason(_fit(dielectric=1.18, n_inliers=40)) is None


def test_a_wet_or_water_filled_permittivity_is_kept() -> None:
    # Saturated ground and water-filled pipes are targets, not noise. Water itself is 81.
    for eps in (30.0, 40.0, 80.0):
        assert _reason(_fit(dielectric=eps)) is None, f"eps {eps} should be kept and flagged"


def test_a_shallow_target_outside_the_direct_wave_is_kept() -> None:
    # A service at 0.3 m is ordinary in an urban survey and must survive.
    assert _reason(_fit(apex_time_ns=DIRECT_WAVE_WINDOW_NS + 0.5, apex_sample=36.0)) is None


# --------------------------------------------------------------------- the scaling bug itself


@pytest.mark.parametrize("interval", [0.1, 0.2, 0.4])
def test_the_direct_wave_band_is_the_same_time_on_every_channel(interval: float) -> None:
    """The bug this replaced: the same antenna excluded 0.15 m, 0.31 m and 0.61 m on RAD/RA1/RA2.

    The direct wave is a property of the antenna, so an apex at a given *time* must be judged
    identically no matter which channel recorded it.
    """
    inside = _fit(apex_time_ns=DIRECT_WAVE_WINDOW_NS - 0.5, apex_sample=8.0)
    outside = _fit(apex_time_ns=DIRECT_WAVE_WINDOW_NS + 0.5, apex_sample=40.0)
    assert _reason(inside, interval=interval) is not None
    assert _reason(outside, interval=interval) is None


def test_a_target_at_half_a_metre_survives_on_the_deep_channel() -> None:
    # Under the old fraction rule RA2 rejected everything above 0.61 m, so this exact target —
    # 0.50 m deep, 28 inlier points, on RA2 — was discarded. It is a real one.
    deep_channel = _fit(apex_time_ns=10.0, apex_sample=25.0, depth_m=0.50, n_inliers=28)
    assert _reason(deep_channel, interval=0.4) is None


# --------------------------------------------------------------------- diagnostics, not a bool


def test_each_rejection_is_distinguishable_from_the_others() -> None:
    # A line whose rejections are mostly "direct-wave" is telling you something different from one
    # whose rejections are mostly "faster than light", so the reason is the return value.
    reasons = {
        _reason(_fit(physically_plausible=False)),
        _reason(_fit(apex_time_ns=1.0)),
        _reason(_fit(apex_sample=250.0)),
        _reason(_fit(n_inliers=2)),
        _reason(_fit(dielectric=200.0)),
    }
    assert len(reasons) == 5
    assert all(isinstance(r, str) and r for r in reasons)


def test_the_record_floor_scales_with_the_record_length() -> None:
    for n_samples in (128, 256, 512):
        assert _reason(_fit(apex_sample=0.5 * n_samples), n_samples=n_samples) is None
        assert _reason(_fit(apex_sample=0.95 * n_samples), n_samples=n_samples) is not None
