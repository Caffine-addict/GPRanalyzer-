"""Tests for reference/radargram_marks.py — the vendor's circled radargram targets."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from detect.hyperbola import HyperbolaFit
from reference import radargram_marks as rm


def test_axes_convert_pixels_to_metres_from_two_ticks_each() -> None:
    axes = rm.Axes(x=((100, 2.0), (300, 6.0)), depth=((50, 0.0), (250, 2.0)), plot_left=100, plot_top=50)
    assert axes.to_metres(200, 150) == (4.0, 1.0)
    assert axes.metres_per_px() == (0.02, 0.01)


def _two_touching_circles() -> np.ndarray:
    image = np.full((300, 400, 3), 128, np.uint8)
    cv2.circle(image, (150, 150), 44, (255, 0, 0), 2)  # RGB red
    cv2.circle(image, (225, 170), 44, (255, 0, 0), 2)
    return image


def test_touching_circles_are_found_as_two_marks_not_one_blob() -> None:
    found = rm.red_circles(_two_touching_circles())
    assert [v for x, y, _ in found for v in (x, y)] == pytest.approx([150, 150, 225, 170], abs=3)


def test_the_marks_are_painted_out_before_the_detector_sees_the_image() -> None:
    axes = rm.Axes(x=((0, 0.0), (400, 4.0)), depth=((0, 0.0), (300, 3.0)), plot_left=0, plot_top=0)
    plot = rm.radargram_only(_two_touching_circles(), axes)
    assert plot.ndim == 2 and np.ptp(plot) < 20  # a flat grey field again


def test_a_box_counts_only_when_centred_inside_the_circle() -> None:
    circle = [(100.0, 100.0, 40.0)]
    assert rm.hits(circle, [(90, 90, 20, 20)], (0, 0)) == [True]
    # A huge box overlapping the circle, centred well outside it, is not a hit.
    assert rm.hits(circle, [(0, 90, 400, 300)], (0, 0)) == [False]
    # Plot boxes are shifted by the plot offset before the test.
    assert rm.hits(circle, [(40, 40, 20, 20)], (50, 50)) == [True]


_R2 = Path("Dataset/DSU_GPR_Files/GPR_24AUG2026/Gandhidham-Mundra (extracted from Sample Docs-DPR rar)/Radargrams/r2.PNG")


@pytest.mark.skipif(not _R2.exists(), reason="vendor radargrams not present")
def test_the_real_r2_screenshot_has_its_four_circled_targets_at_their_labelled_depths() -> None:
    rgb = cv2.cvtColor(cv2.imread(str(_R2)), cv2.COLOR_BGR2RGB)
    targets = [rm.AXES["r2"].to_metres(x, y) for x, y, _ in rm.red_circles(rgb)]
    # Read off the image by eye against its axis labels.
    assert [(round(x, 1), round(d, 1)) for x, d in targets] == [(0.6, 0.6), (2.7, 0.6), (3.8, 1.0), (4.5, 0.7)]


@pytest.mark.parametrize("true_dielectric", [5.0, 7.3, 11.0])
def test_a_hyperbola_drawn_with_the_wrong_velocity_reveals_the_true_dielectric(true_dielectric: float) -> None:
    # A depth axis drawn with dielectric 7.3 over ground of `true_dielectric`: displayed depth is
    # sqrt(d0^2 + r^2 x^2) with r = sqrt(true / 7.3). Draw that curve as a bright ridge.
    axes = rm.Axes(x=((0, 0.0), (400, 4.0)), depth=((0, 0.0), (400, 2.0)), plot_left=0, plot_top=0)
    noise = np.random.default_rng(0).normal(0, 6, (400, 400))
    image = np.clip(128 + noise, 0, 255).astype(np.uint8)
    r = np.sqrt(true_dielectric / rm.REPORTED_DIELECTRIC)
    for col in range(400):
        depth = np.hypot(0.6, r * (col - 200) * 0.01)
        row = round(depth / 0.005)
        if row < 398:
            image[row:row + 3, col] = 255
    check = rm.check_velocity(rm.envelope(image), axes, (200.0, 140.0, 40.0))
    assert check is not None and check.rejected is None
    assert check.implied_dielectric == pytest.approx(true_dielectric, rel=0.05)
    assert check.apex_depth_m == pytest.approx(0.6, abs=0.02)


def test_check_velocity_rejects_a_fit_below_the_r2_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    axes = rm.Axes(x=((0, 0.0), (400, 4.0)), depth=((0, 0.0), (400, 2.0)), plot_left=0, plot_top=0)
    # k = per_d / per_x gives ratio 1.0, i.e. a plausible dielectric equal to REPORTED_DIELECTRIC
    # — isolates the low-R² rejection from the separate plausibility rejection below.
    fit = HyperbolaFit(x0=200.0, t0=120.0, k=0.005 / 0.01, r2=0.5, n_inliers=10, n_total=20)
    monkeypatch.setattr(rm, "extract_ridge_points", lambda *a, **k: (np.array([1.0]), np.array([1.0])))
    monkeypatch.setattr(rm, "fit_hyperbola_ransac", lambda *a, **k: fit)
    check = rm.check_velocity(np.zeros((400, 400)), axes, (200.0, 140.0, 40.0))
    assert check is not None
    assert check.rejected is not None and "R²" in check.rejected


def test_check_velocity_rejects_an_implied_dielectric_below_one(monkeypatch: pytest.MonkeyPatch) -> None:
    axes = rm.Axes(x=((0, 0.0), (400, 4.0)), depth=((0, 0.0), (400, 2.0)), plot_left=0, plot_top=0)
    # k = 10 * per_d / per_x gives ratio 0.1, implied dielectric 7.3 * 0.1^2 = 0.073 — below the
    # physically-possible floor of 1.0, with a high R² so the R² branch can't be what rejects it.
    fit = HyperbolaFit(x0=200.0, t0=120.0, k=10 * 0.005 / 0.01, r2=0.99, n_inliers=10, n_total=20)
    monkeypatch.setattr(rm, "extract_ridge_points", lambda *a, **k: (np.array([1.0]), np.array([1.0])))
    monkeypatch.setattr(rm, "fit_hyperbola_ransac", lambda *a, **k: fit)
    check = rm.check_velocity(np.zeros((400, 400)), axes, (200.0, 140.0, 40.0))
    assert check is not None
    assert check.rejected is not None and "impossible" in check.rejected
    assert check.implied_dielectric < 1.0


def test_hyperbola_response_masks_out_the_shallowest_rows() -> None:
    axes = rm.Axes(x=((0, 0.0), (400, 4.0)), depth=((0, 0.0), (200, 2.0)), plot_left=0, plot_top=0)
    image = np.clip(128 + np.random.default_rng(3).normal(0, 6, (200, 400)), 0, 255).astype(np.uint8)
    response = rm.hyperbola_response(rm.envelope(image), axes)
    per_d = axes.metres_per_px()[1]
    cutoff = round(rm._MIN_APEX_DEPTH_M / per_d)
    assert np.all(response[:cutoff] == 0)


def test_close_peaks_are_suppressed_within_the_separation_radius(monkeypatch: pytest.MonkeyPatch) -> None:
    # axes give 0.01 m/px in x, so the 0.5 m separation radius is 50 px. Three synthetic peaks:
    # a strongest one at column 100, a second 40 px away (inside the radius — should be
    # suppressed once the first is taken), and a third 70 px away (outside — should survive).
    axes = rm.Axes(x=((0, 0.0), (400, 4.0)), depth=((0, 0.0), (200, 2.0)), plot_left=0, plot_top=0)
    response = np.zeros((200, 400), np.float64)
    response[50, 100] = 10.0
    response[50, 140] = 8.0
    response[50, 170] = 6.0
    monkeypatch.setattr(rm, "hyperbola_response", lambda energy, axes: response)
    found = rm.hyperbola_candidates(np.zeros((200, 400)), axes, top=3)
    assert [(col, row) for col, row, _ in found] == [(100, 50), (170, 50)]


def _scene(with_hyperbola: bool) -> tuple[np.ndarray, rm.Axes]:
    """A 4 m x 2 m noisy screenshot at 1 cm/px, with a slab at 1.3 m and maybe a target at 0.6 m."""
    axes = rm.Axes(x=((0, 0.0), (400, 4.0)), depth=((0, 0.0), (200, 2.0)), plot_left=0, plot_top=0)
    image = np.clip(128 + np.random.default_rng(1).normal(0, 6, (200, 400)), 0, 255).astype(np.uint8)
    # A flat slab over part of the line: strong, but not a point target. (A band across the whole
    # width would vanish in envelope()'s per-row background removal before the filter saw it.)
    image[130:136, :160] = 255
    image[142:148, :160] = 0
    if with_hyperbola:
        for col in range(400):
            row = round(np.hypot(0.6, (col - 250) * 0.01) / 0.01)
            if row < 198:
                image[row:row + 3, col] = 255
    return image, axes


def test_the_matched_filter_puts_its_strongest_peak_on_the_hyperbolas_apex() -> None:
    image, axes = _scene(with_hyperbola=True)
    col, row, _ = rm.hyperbola_candidates(rm.envelope(image), axes, top=1)[0]
    assert (col, row) == (pytest.approx(250, abs=5), pytest.approx(60, abs=4))


def test_a_flat_slab_alone_scores_well_below_a_real_target() -> None:
    # Two thirds, not zero: a thick slab is this detector's known weakness (see the module).
    with_target = rm.hyperbola_candidates(rm.envelope(_scene(True)[0]), _scene(True)[1], top=1)[0][2]
    slab_only = rm.hyperbola_candidates(rm.envelope(_scene(False)[0]), _scene(False)[1], top=1)
    assert not slab_only or slab_only[0][2] < 0.7 * with_target
