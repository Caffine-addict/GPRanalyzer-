"""The vendor's red-circled targets on radargram screenshots, in metres, and how our detector does on them.

The Gandhidham–Mundra report (GeoCarte, GSSI SIR-4000, RADAN 7) ships seven radargram
screenshots with each interpreted target circled in red. They are the first *human-marked*
targets this project has: not labels in the training sense — a circle is a location, not a
class — but somewhere an expert said "there is something here", which our classical detector
can be scored against.

Pixels become metres through `AXES`, a per-image calibration: two major ticks per axis, with
their pixel positions detected from the tick marks and their values read off the axis labels by
eye (there is no OCR on this machine). RADAN drew the depth axis with the report's own velocity
(dielectric 7.3, fitted from hyperbolas), so a depth here is the vendor's depth.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from detect.classical import find_candidate_boxes
from detect.hyperbola import extract_ridge_points, fit_hyperbola_ransac
from detect.measure import PERMITTIVITY_IMPLAUSIBLE_ABOVE

REPORTED_DIELECTRIC = 7.3  # the report: "dielectric constant of the medium is obtained to be around 7.3"
_LIMB_HALF_WIDTH_M = 1.0  # ridge points are taken this far either side of the circle
_LIMB_DEPTH_M = 1.2  # and this far down from the circle's top
_MIN_FIT_R2 = 0.95


@dataclass(frozen=True)
class Axes:
    """Two reference ticks per axis: (pixel, metres) at each end."""

    x: tuple[tuple[float, float], tuple[float, float]]
    depth: tuple[tuple[float, float], tuple[float, float]]
    plot_left: int  # first pixel column inside the plot
    plot_top: int  # first pixel row inside the plot (depth 0)

    def to_metres(self, px: float, py: float) -> tuple[float, float]:
        (x0, m0), (x1, m1) = self.x
        (y0, d0), (y1, d1) = self.depth
        return m0 + (px - x0) * (m1 - m0) / (x1 - x0), d0 + (py - y0) * (d1 - d0) / (y1 - y0)

    def metres_per_px(self) -> tuple[float, float]:
        (x0, m0), (x1, m1) = self.x
        (y0, d0), (y1, d1) = self.depth
        return (m1 - m0) / (x1 - x0), (d1 - d0) / (y1 - y0)


# Pixel positions: major tick marks found by scanning each axis strip for the longest dark runs.
# Metre values: the labels printed beside those ticks, read by eye. Verify any change by drawing
# the grid back onto the image — `scripts/process_vendor_radargrams.py` writes one per image.
AXES = {
    "r1": Axes(x=((65, 0.0), (487, 6.0)), depth=((45, 0.0), (617, 5.0)), plot_left=68, plot_top=46),
    "r2": Axes(x=((62, 0.0), (476, 6.0)), depth=((44, 0.0), (606, 2.5)), plot_left=65, plot_top=45),
    "r3": Axes(x=((56, 0.0), (410, 6.0)), depth=((37, 0.0), (519, 2.5)), plot_left=58, plot_top=38),
    "r4": Axes(x=((56, 2.0), (439, 10.0)), depth=((30, 0.0), (421, 2.5)), plot_left=47, plot_top=31),
    "r5": Axes(x=((63, 0.0), (472, 6.0)), depth=((43, 0.0), (598, 2.5)), plot_left=66, plot_top=44),
    "r7": Axes(x=((156, 8.0), (566, 14.0)), depth=((41, 0.0), (589, 2.5)), plot_left=66, plot_top=42),
    "r8": Axes(x=((85, 8.0), (500, 14.0)), depth=((41, 0.0), (596, 2.5)), plot_left=65, plot_top=42),
}

# Hough ring search on the red mask. Radii span the 43–66 px circles on the delivered images;
# connected components can't be used, because touching circles merge into one blob (r2, r5).
_HOUGH = {"dp": 1, "minDist": 40, "param1": 100, "param2": 25, "minRadius": 30, "maxRadius": 80}


def red_mask(rgb: np.ndarray) -> np.ndarray:
    r, g, b = (rgb[..., i].astype(np.int16) for i in range(3))
    return ((r > 150) & (r - g > 90) & (r - b > 90)).astype(np.uint8)


def red_circles(rgb: np.ndarray) -> list[tuple[float, float, float]]:
    """(centre x, centre y, radius) of every red circle, left to right, in pixels."""
    ring = cv2.GaussianBlur(red_mask(rgb) * 255, (5, 5), 1.5)
    found = cv2.HoughCircles(ring, cv2.HOUGH_GRADIENT, **_HOUGH)  # type: ignore[call-overload]
    return [] if found is None else sorted((float(x), float(y), float(r)) for x, y, r in found[0])


def radargram_only(rgb: np.ndarray, axes: Axes) -> np.ndarray:
    """The plot area as greyscale with the red marks painted out, so a detector can't see them."""
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    mask = cv2.dilate(red_mask(rgb), np.ones((5, 5), np.uint8))
    clean = cv2.inpaint(grey, mask, 3, cv2.INPAINT_TELEA)
    return clean[axes.plot_top:, axes.plot_left:]


def detector_boxes(plot: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Our classical detector on an image: columns are traces, rows are samples of unknown time."""
    return find_candidate_boxes(plot.T.astype(np.float64), sample_interval_ns=None)


def hits(circles: list[tuple[float, float, float]],
         boxes: list[tuple[int, int, int, int]], offset: tuple[int, int]) -> list[bool]:
    """For each circle, whether a detector box is centred inside it. `offset` places plot boxes on the image.

    Centre-inside, not overlap: a large box brushing a circle's edge would otherwise count, and
    with dozens of boxes per image nearly every circle would be "found".
    """
    ox, oy = offset
    centres = [(bx + ox + bw / 2, by + oy + bh / 2) for bx, by, bw, bh in boxes]
    return [any((x - cx) ** 2 + (y - cy) ** 2 <= r**2 for x, y in centres) for cx, cy, r in circles]


@dataclass(frozen=True)
class VelocityCheck:
    """What one circled target's hyperbola says about the velocity RADAN drew the depths with."""

    apex_depth_m: float
    fit_r2: float
    velocity_ratio: float  # velocity the depth axis used / velocity the curvature implies
    implied_dielectric: float
    rejected: str | None  # why the fit should not be believed, or None


def envelope(plot: np.ndarray) -> np.ndarray:
    """Row-normalised reflection energy of a radargram image, (rows, columns) like the image."""
    residual = plot.astype(np.float64) - np.median(plot, axis=1, keepdims=True)
    energy = np.sqrt(cv2.blur((residual**2).astype(np.float32), (3, 9)))
    floor = max(float(np.median(energy)) * 0.15, 1e-6)  # a blank row must not divide by zero
    return energy / np.maximum(np.median(energy, axis=1, keepdims=True), floor)


def check_velocity(energy: np.ndarray, axes: Axes, circle: tuple[float, float, float]) -> VelocityCheck | None:
    """Fit the hyperbola under one circle and compare its curvature with the depth axis.

    RADAN converted time to depth with the report's velocity v_used. A point target then plots
    as depth(x) = sqrt(d0^2 + r^2 x^2) with r = v_used / v_true, so the fitted curvature gives r
    directly, and the true dielectric is REPORTED_DIELECTRIC * r^2. None when no hyperbola fits.
    """
    per_x, per_d = axes.metres_per_px()
    cx, cy, radius = circle[0] - axes.plot_left, circle[1] - axes.plot_top, circle[2]
    half = round(_LIMB_HALF_WIDTH_M / per_x)
    x, y = max(0, round(cx) - half), max(0, round(cy - radius))
    xs, ts = extract_ridge_points(energy, x, y, 2 * half, round(_LIMB_DEPTH_M / per_d))
    fit = fit_hyperbola_ransac(xs, ts)
    if fit is None:
        return None
    ratio = per_d / (per_x * fit.k)
    dielectric = REPORTED_DIELECTRIC * ratio**2
    rejected = None
    if fit.r2 < _MIN_FIT_R2:
        rejected = f"fit R² {fit.r2:.2f} below {_MIN_FIT_R2}"
    elif not 1.0 <= dielectric <= PERMITTIVITY_IMPLAUSIBLE_ABOVE:
        rejected = f"implied dielectric {dielectric:.1f} is physically impossible"
    return VelocityCheck(apex_depth_m=axes.to_metres(0, fit.t0 + axes.plot_top)[1], fit_r2=fit.r2,
                         velocity_ratio=ratio, implied_dielectric=dielectric, rejected=rejected)


# --- a detector for depth-converted screenshots ---------------------------------------------
# On these images RADAN already converted time to depth with a velocity the targets' own
# hyperbolas confirm (check_velocity: median dielectric 7.2 against the report's 7.3), so a point
# target must plot as depth(x) = sqrt(d0^2 + x^2) in metres — a curve with no free parameter but
# its apex. The response at each apex is a low percentile of the reflection energy along that
# curve, minus the same along a flat line at the apex depth: a true hyperbola is bright along its
# whole length, while a curve that merely cuts through a flat layer is bright only where it
# crosses it. Only valid on an axis drawn with the right velocity.
#
# Known weakness, pinned in the tests: a thick flat slab still scores about two thirds of a
# real target just above itself. And the two knobs below were chosen looking at the same 20
# vendor targets they are scored on — there is no held-out set, so read the score as a ceiling.
_CURVE_HALF_WIDTH_M = 0.8
_MIN_APEX_DEPTH_M = 0.25  # above this the direct wave dominates every column
_PEAK_SEPARATION_M = 0.5
_ALONG_CURVE_PERCENTILE = 30  # 50: 12/20 at top 5, 13/20 at top 10; 30: 10/20 and 14/20.
# Leave-one-image-out (tune on 6 radargrams, score the 7th; scripts/score_matched_filter_loo.py):
# still 14/20 at top 10, so that figure is not an artefact of tuning on the targets it is scored on.
TOP_CANDIDATES = 10  # per image


def hyperbola_response(energy: np.ndarray, axes: Axes) -> np.ndarray:
    """How consistently a point-target hyperbola, apex at each pixel, stands out from a flat line."""
    per_x, per_d = axes.metres_per_px()
    rows, cols = energy.shape
    depth_rows = np.arange(rows)
    along_curve, along_flat = [], []
    half = round(_CURVE_HALF_WIDTH_M / per_x)
    for dx in range(-half, half + 1, 2):
        curve_rows = np.minimum(np.round(np.hypot(depth_rows * per_d, dx * per_x) / per_d).astype(int), rows - 1)
        shifted = np.clip(np.arange(cols) + dx, 0, cols - 1)
        # float32: the stacks hold one image per curve offset (~80), so width matters (~0.2 GB)
        along_curve.append(energy[np.ix_(curve_rows, shifted)].astype(np.float32))
        along_flat.append(energy[np.ix_(depth_rows, shifted)].astype(np.float32))
    response = (np.percentile(np.stack(along_curve), _ALONG_CURVE_PERCENTILE, axis=0)
                - np.percentile(np.stack(along_flat), _ALONG_CURVE_PERCENTILE, axis=0))
    response[: round(_MIN_APEX_DEPTH_M / per_d)] = 0
    return response


def hyperbola_candidates(energy: np.ndarray, axes: Axes, top: int = TOP_CANDIDATES) -> list[tuple[int, int, float]]:
    """The `top` strongest apexes as (column, row, response) in plot pixels, at least 0.5 m apart."""
    response = hyperbola_response(energy, axes)
    radius = round(_PEAK_SEPARATION_M / axes.metres_per_px()[0])
    found = []
    for _ in range(top):
        row, col = np.unravel_index(int(np.argmax(response)), response.shape)
        value = float(response[row, col])
        if value <= 0:
            break
        found.append((int(col), int(row), value))
        cv2.circle(response, (int(col), int(row)), radius, -np.inf, -1)
    return found
