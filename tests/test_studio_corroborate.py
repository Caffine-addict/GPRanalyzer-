"""Tests for studio/corroborate.py — cross-channel agreement and the run geometry.

The rule this module replaced had two specific bugs, and the first two tests here exist to pin
them shut: it chained corroboration transitively (A~B, B~C therefore one object) and it averaged
depths that disagreed. Clustering must not do either.
"""

from __future__ import annotations

import numpy as np
import pytest

from studio.corroborate import (
    DEFAULT_DEPTH_EPS_M,
    DEFAULT_POSITION_EPS_M,
    MAX_PERMITTIVITY_RATIO,
    RUN_RESIDUAL_TOL_M,
    Apex,
    corroborate,
    dbscan,
)


def _apex(id_: str, channel: str, position_m: float, depth_m: float, **kw) -> Apex:
    return Apex(id=id_, channel=channel, position_m=position_m, depth_m=depth_m, **kw)


# --------------------------------------------------------------------------- the two real bugs


def test_a_chain_of_near_neighbours_does_not_become_one_object() -> None:
    """Three apexes each within tolerance of the next, spanning far more than the tolerance.

    The old pairwise rule merged these into a single "corroborated" target and reported the mean
    depth, which belonged to none of them. With min_samples=2 DBSCAN does link a genuine density
    chain — so the guard that matters is that the reported spread exposes it instead of hiding
    it behind a mean.
    """
    apexes = [
        _apex("a", "RAD", 8.00, 1.20),
        _apex("b", "RA1", 8.28, 1.50),
        _apex("c", "RA2", 8.56, 1.80),
    ]
    [result] = corroborate(apexes)
    # Whatever the clustering does, the disagreement must be visible in the output.
    assert result.position_spread_m == pytest.approx(0.56)
    assert result.depth_spread_m == pytest.approx(0.60)
    assert result.depth_spread_m > DEFAULT_DEPTH_EPS_M  # a reader can see this is not one tight target


def test_two_fits_on_the_same_channel_are_not_corroboration() -> None:
    # Same receiver, same time axis, same systematic error — agreement proves nothing.
    apexes = [_apex("a", "RAD", 8.50, 1.30), _apex("b", "RAD", 8.55, 1.32)]
    [result] = corroborate(apexes)
    assert result.channels == ("RAD",)
    assert result.n_channels == 1
    assert result.corroborated is False


# --------------------------------------------------------------------------- the real target


def test_the_projects_one_confirmed_target_is_corroborated() -> None:
    """Job_0703's real target: RAD at 8.50 m / 1.192 m, RA1 at 8.60 m / 1.375 m.

    These are the actual fitted numbers. A tolerance that rejected them would reject the best
    evidence of a buried object the project has.
    """
    apexes = [
        _apex("750480e8", "RAD", 8.50, 1.192, dielectric=8.96, fit_r2=0.913, n_inliers=35),
        _apex("740364e1", "RA1", 8.60, 1.375, dielectric=8.34, fit_r2=0.992, n_inliers=77),
    ]
    [result] = corroborate(apexes)
    assert result.corroborated is True
    assert result.channels == ("RA1", "RAD")
    assert result.n_channels == 2
    assert result.position_m == pytest.approx(8.55)
    assert result.position_spread_m == pytest.approx(0.10)
    assert result.depth_spread_m == pytest.approx(0.183)


def test_distant_targets_stay_separate() -> None:
    apexes = [
        _apex("a", "RAD", 2.00, 0.80),
        _apex("b", "RA1", 2.10, 0.85),
        _apex("c", "RAD", 9.00, 1.30),
        _apex("d", "RA1", 9.05, 1.28),
    ]
    results = corroborate(apexes)
    assert len(results) == 2
    assert all(r.corroborated for r in results)
    positions = sorted(round(r.position_m, 2) for r in results)
    assert positions == [2.05, 9.03]


def test_results_are_ordered_strongest_first() -> None:
    apexes = [
        _apex("lonely_a", "RAD", 1.00, 0.50),
        _apex("lonely_b", "RAD", 1.05, 0.52),  # same channel -> weaker
        _apex("strong_a", "RAD", 6.00, 1.10),
        _apex("strong_b", "RA1", 6.05, 1.12),
        _apex("strong_c", "RA2", 6.10, 1.14),  # three distinct channels -> strongest
    ]
    results = corroborate(apexes)
    assert results[0].n_channels == 3
    assert results[0].channels == ("RA1", "RA2", "RAD")
    assert results[-1].n_channels == 1


def test_a_lone_apex_is_not_reported_as_an_object() -> None:
    # One apex is a candidate, not a corroborated target — studio/candidates.py reports those.
    assert corroborate([_apex("only", "RAD", 4.0, 1.0)]) == []


def test_no_apexes_is_not_an_error() -> None:
    assert corroborate([]) == []


# --------------------------------------------------------------------------- run geometry


def test_a_level_run_has_near_zero_slope() -> None:
    apexes = [
        _apex("a", "RAD", 5.00, 1.000),
        _apex("b", "RA1", 5.10, 1.000),
        _apex("c", "RA2", 5.20, 1.000),
    ]
    [result] = corroborate(apexes)
    assert result.run_slope_m_per_m == pytest.approx(0.0, abs=1e-9)
    assert result.run_residual_m == pytest.approx(0.0, abs=1e-9)


def test_a_sloping_run_recovers_its_gradient() -> None:
    # 0.1 m deeper for every 0.1 m along the line -> slope 1.0
    apexes = [
        _apex("a", "RAD", 5.00, 1.00),
        _apex("b", "RA1", 5.10, 1.10),
        _apex("c", "RA2", 5.20, 1.20),
    ]
    [result] = corroborate(apexes)
    assert result.run_slope_m_per_m == pytest.approx(1.0, rel=1e-6)


def test_ransac_ignores_one_bad_pick_rather_than_averaging_it_in() -> None:
    """The reason the literature prefers RANSAC over total least squares here — and the tie-break.

    Four apexes on a level run plus one bad pick. Least squares through all five tilts the line
    to slope 1.28. But RANSAC alone was not enough either: a line drawn through one good apex and
    the bad one catches exactly as many inliers as the true level line, and while ties were
    broken by whichever pair the RNG drew first, the impostor won — slope 1.65, with a good apex
    thrown out. Ranking on inlier count *then* total residual fixes it, because the true line's
    residual is 0.
    """
    good = [
        _apex("a", "RAD", 5.00, 1.00),
        _apex("b", "RA1", 5.05, 1.00),
        _apex("c", "RA2", 5.10, 1.00),
        _apex("d", "RAD", 5.15, 1.00),
    ]
    outlier = _apex("bad", "RA1", 5.20, 1.32)  # within cluster tolerance, off the run
    [result] = corroborate([*good, outlier])
    assert result.run_slope_m_per_m == pytest.approx(0.0, abs=1e-9)
    assert result.run_residual_m == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------- clustering itself


def test_dbscan_labels_noise_as_minus_one() -> None:
    scaled = np.array([[0.0, 0.0], [0.1, 0.1], [50.0, 50.0]])
    labels = dbscan(scaled, min_samples=2)
    assert labels[0] == labels[1] >= 0
    assert labels[2] == -1


def test_dbscan_separates_two_dense_groups() -> None:
    scaled = np.array([[0.0, 0.0], [0.2, 0.0], [20.0, 0.0], [20.2, 0.0]])
    labels = dbscan(scaled, min_samples=2)
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]


def test_non_positive_tolerances_are_refused() -> None:
    apexes = [_apex("a", "RAD", 1.0, 1.0), _apex("b", "RA1", 1.0, 1.0)]
    for bad in ({"position_eps_m": 0.0}, {"depth_eps_m": -1.0}):
        with pytest.raises(ValueError, match="tolerances must be positive"):
            corroborate(apexes, **bad)


def test_the_default_tolerances_are_the_documented_ones() -> None:
    # Pinned because they were derived from the one real corroborated target, not chosen freely.
    assert DEFAULT_POSITION_EPS_M == 0.30
    assert DEFAULT_DEPTH_EPS_M == 0.35
    # Must stay strictly tighter than the cluster tolerance, or RANSAC cannot reject a member.
    assert RUN_RESIDUAL_TOL_M < DEFAULT_DEPTH_EPS_M


# --- permittivity agreement (added 2026-09-14, after the first real run) -----


def test_channels_that_disagree_about_permittivity_are_not_corroboration() -> None:
    """Agreeing on position and depth while disagreeing on the ground is a coincidence.

    If two receivers really saw the same object, the wave crossed the same ground to reach it. On
    the four real survey lines this single check cut 14 "corroborated" targets to 6 — the ones it
    removed included pairs whose implied permittivities differed by a factor of 20, which cannot
    both describe the same soil.
    """
    apexes = [
        _apex("a", "RAD", 8.50, 1.20, dielectric=2.8),
        _apex("b", "RA1", 8.55, 1.25, dielectric=38.5),
    ]
    [result] = corroborate(apexes)
    assert result.n_channels == 2  # they do cluster on position and depth
    assert result.permittivity_ratio == pytest.approx(38.5 / 2.8)
    assert result.permittivity_agrees is False
    assert result.corroborated is False  # ...and that is still not corroboration


def test_channels_that_agree_about_permittivity_are_corroboration() -> None:
    # The project's one real target: RAD eps 8.96, RA1 eps 8.34.
    apexes = [
        _apex("a", "RAD", 8.50, 1.192, dielectric=8.96),
        _apex("b", "RA1", 8.60, 1.375, dielectric=8.34),
    ]
    [result] = corroborate(apexes)
    assert result.permittivity_ratio == pytest.approx(8.96 / 8.34)
    assert result.permittivity_agrees is True
    assert result.corroborated is True


def test_unknown_permittivity_does_not_block_corroboration() -> None:
    # Absence of the measurement is not evidence against the target. Counting it as disagreement
    # would silently discard every cluster built from fits that could not imply a velocity.
    apexes = [_apex("a", "RAD", 5.00, 1.00), _apex("b", "RA1", 5.05, 1.02)]
    [result] = corroborate(apexes)
    assert result.dielectric_range is None
    assert result.permittivity_ratio is None
    assert result.permittivity_agrees is True
    assert result.corroborated is True


def test_a_partially_known_permittivity_is_judged_on_what_is_known() -> None:
    apexes = [
        _apex("a", "RAD", 5.00, 1.00, dielectric=9.0),
        _apex("b", "RA1", 5.05, 1.02),  # no dielectric
    ]
    [result] = corroborate(apexes)
    assert result.dielectric_range == (9.0, 9.0)
    assert result.corroborated is True


def test_the_dielectric_range_exposes_the_spread_rather_than_a_mean() -> None:
    # A mean would hide exactly the disagreement this field exists to show.
    apexes = [
        _apex("a", "RAD", 5.00, 1.00, dielectric=8.0),
        _apex("b", "RA1", 5.05, 1.02, dielectric=11.0),
        _apex("c", "RA2", 5.10, 1.01, dielectric=9.0),
    ]
    [result] = corroborate(apexes)
    assert result.dielectric_range == (8.0, 11.0)


def test_the_permittivity_threshold_is_the_documented_one() -> None:
    assert MAX_PERMITTIVITY_RATIO == 2.0


def test_one_weak_outlier_cannot_veto_two_strong_agreeing_receivers() -> None:
    """Job_0703 at 8.54 m, the project's known target, exactly as the real data produces it.

    RA1 at eps 8.34 on 77 inlier points and RA2 at eps 11.30 on 74 agree to a factor of 1.36. A
    third RA1 apex 0.3 m shallower, eps 2.14 on only 17 points, stretched a min/max range to 5.3
    and blocked the whole cluster — one weak fit overruling the two that carry the evidence.
    """
    apexes = [
        _apex("strong_ra1", "RA1", 8.60, 1.37, dielectric=8.34, n_inliers=77),
        _apex("strong_ra2", "RA2", 8.51, 1.34, dielectric=11.30, n_inliers=74),
        _apex("weak_ra1", "RA1", 8.51, 1.07, dielectric=2.14, n_inliers=17),
    ]
    [result] = corroborate(apexes)
    assert result.dielectric_range == (8.34, 11.30)  # the weak RA1 fit is not the RA1 reading
    assert result.permittivity_ratio == pytest.approx(11.30 / 8.34)
    assert result.corroborated is True


def test_two_strong_receivers_that_really_disagree_are_still_blocked() -> None:
    # Job_0720 at 8.91 m: eps 1.29 on 36 inliers against eps 4.79 on 33. Both well constrained,
    # genuinely different ground — the check must still refuse this one.
    apexes = [
        _apex("rad", "RAD", 8.82, 0.72, dielectric=1.29, n_inliers=36),
        _apex("ra1", "RA1", 9.01, 0.61, dielectric=4.79, n_inliers=33),
    ]
    [result] = corroborate(apexes)
    assert result.n_channels == 2
    assert result.corroborated is False


def test_a_channel_contributes_its_best_look_not_its_first() -> None:
    apexes = [
        _apex("rad_poor", "RAD", 5.00, 1.00, dielectric=30.0, n_inliers=9),
        _apex("rad_good", "RAD", 5.05, 1.02, dielectric=9.1, n_inliers=50),
        _apex("ra1", "RA1", 5.02, 1.01, dielectric=8.8, n_inliers=44),
    ]
    [result] = corroborate(apexes)
    assert result.dielectric_range == (8.8, 9.1)
    assert result.corroborated is True


def test_without_inlier_counts_every_reading_still_counts() -> None:
    # Apexes that carry no inlier count (older callers) must not silently drop out of the check.
    apexes = [
        _apex("a", "RAD", 5.00, 1.00, dielectric=9.0),
        _apex("b", "RA1", 5.05, 1.02, dielectric=30.0),
    ]
    [result] = corroborate(apexes)
    assert result.dielectric_range == (9.0, 30.0)
    assert result.corroborated is False
