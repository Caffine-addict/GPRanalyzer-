"""Hyperbola fitting and per-box shape diagnosis — the measurement half, with no I/O.

Grounded in the standard GPR literature method for hyperbola detection (Hough-transform-style
fitting; see docs/GPR_PATTERN_REFERENCE.md): a point diffractor produces a hyperbola in
(trace, sample) space. Rather than a full 3D Hough accumulator, this uses the equivalent and
much cheaper classical approach: extract one ridge point (the depth of peak energy) per trace
column, then fit those points to the hyperbola model via RANSAC + least squares. The hyperbola
t(x) = sqrt(t0^2 + ((x-x0)/k)^2) becomes an ordinary parabola t^2 = A + Bx + Cx^2, so fitting is
just np.polyfit(x, t**2, 2) on the inlier set — no expensive grid search needed.

A diagnosis is evidence and a suggestion, never a substitute for the company's ground truth,
and deliberately never written into `core.boxes.Box` (see its docstring: "no class field on
purpose"). Every suggested_class carries the raw evidence numbers that produced it so a human
can check the reasoning rather than trust a label.

Why this module holds no file access: the job-level driver needs to load channels, which means
`studio.session`, and `studio.velocity` imports the fitters here — routing both through one
module would make `detect` and `studio` import each other. The pure math therefore stays here
(numpy and `detect.measure` only) and the job-level driver lives in `studio/diagnose.py`. This
is the same split that moved `hyperbola_half_aperture_m` into `detect/measure.py`.

Known limitation, carried over unchanged: this reasons about one box at a time. Distinguishing
multiple_point_reflectors (regularly spaced) from cluttered_multi_target (irregular) needs
cross-box spacing analysis — `detect/refine.py` does that for detections, and
`studio/corroborate.py` does it across channels. Both currently fall out as "point" here with
no distinction. Flagged, not silently pretended away.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from detect.measure import STRONG_AMPLITUDE_THRESHOLD

_SPEED_OF_LIGHT_M_PER_NS = 0.2998
_RIDGE_MIN_PEAK = 1.3  # normalized-envelope units; below this a column has no real signal to pick
_RANSAC_RESIDUAL_TOL = 4.0  # samples
_RANSAC_MIN_INLIERS = 6
_RANSAC_MIN_INLIER_FRACTION = 0.35  # rejects density-driven spurious fits in noise -- see
# test_fit_hyperbola_ransac_returns_none_for_pure_noise: an absolute inlier count alone let
# RANSAC find "fits" explaining just 18-25% of purely random points, while real confirmed
# hyperbolas (Job_0703 RAD, validated by eye) explained 42-81% of their ridge points.
_RANSAC_ITERATIONS = 2000
_HYPERBOLA_R2_THRESHOLD = 0.85  # matches what real confirmed hyperbolas scored in validation
_LINEAR_ASPECT_THRESHOLD = 2.0  # width/height ratio above which a poor-fit box reads as linear
_COHERENCE_DISTURBED_THRESHOLD = 0.3


@dataclass(frozen=True)
class HyperbolaFit:
    x0: float
    t0: float
    k: float
    r2: float
    n_inliers: int
    n_total: int


@dataclass(frozen=True)
class Diagnosis:
    box_id: str
    shape: str  # "point" | "linear" | "disturbed" | "ambiguous"
    suggested_class: str | None  # one of the project's 9 taxonomy classes, or None
    rationale: str
    fit_r2: float | None
    n_ridge_inliers: int | None
    n_ridge_total: int | None
    aspect_ratio: float
    local_coherence: float
    peak_amplitude: float
    velocity_m_per_ns: float | None
    implied_dielectric: float | None


def extract_ridge_points(
    normalized: np.ndarray, x: int, y: int, w: int, h: int, margin: int = 5
) -> tuple[np.ndarray, np.ndarray]:
    """One (trace, sample) ridge point per trace column in [x-margin, x+w+margin), picking the
    column's peak-energy depth within [y, y+h). Columns with no real peak are skipped."""
    n_samples, n_traces = normalized.shape
    xs_range = np.arange(x - margin, x + w + margin)
    xs_range = xs_range[(xs_range >= 0) & (xs_range < n_traces)]
    y_hi = min(y + h, n_samples)

    ridge_x, ridge_t = [], []
    for col_x in xs_range:
        column = normalized[y:y_hi, col_x]
        if column.size == 0 or column.max() < _RIDGE_MIN_PEAK:
            continue
        ridge_t.append(y + int(np.argmax(column)))
        ridge_x.append(int(col_x))
    return np.array(ridge_x, dtype=np.float64), np.array(ridge_t, dtype=np.float64)


def fit_hyperbola_ransac(xs: np.ndarray, ts: np.ndarray, seed: int = 0) -> HyperbolaFit | None:
    """RANSAC + least-squares fit of ridge points to t = sqrt(t0^2 + ((x-x0)/k)^2).

    Returns None if there aren't enough points, or no fit clears the minimum-inlier bar —
    both real, reportable outcomes (not an error), meaning "this box's ridge points don't
    describe a clean hyperbola," which is itself diagnostic information.

    A high r2 is NOT evidence the fit is physically real: it scores agreement with whatever
    points RANSAC retained, so a tight fit on ten points of surface clutter outscores a
    genuine target with eighty. Rank fits by permittivity agreement with the site first, then
    inlier count, and reject apexes past ~90% of the record — see docs/GPR_PATTERN_REFERENCE.md.
    """
    n = len(xs)
    if n < 3:
        return None
    min_inliers = max(_RANSAC_MIN_INLIERS, int(np.ceil(_RANSAC_MIN_INLIER_FRACTION * n)))

    rng = np.random.default_rng(seed)
    best_inliers: np.ndarray | None = None
    for _ in range(_RANSAC_ITERATIONS):
        idx = rng.choice(n, size=3, replace=False)
        xs3, ts3_sq = xs[idx], ts[idx] ** 2
        design = np.stack([xs3**2, xs3, np.ones(3)], axis=1)
        try:
            c_coef, b_coef, a_coef = np.linalg.solve(design, ts3_sq)
        except np.linalg.LinAlgError:
            continue
        if c_coef <= 1e-9:
            continue
        k = 1 / np.sqrt(c_coef)
        x0 = -b_coef / (2 * c_coef)
        t0_sq = a_coef - b_coef**2 / (4 * c_coef)
        if t0_sq < 0:
            continue
        predicted = np.sqrt(t0_sq + ((xs - x0) / k) ** 2)
        inliers = np.abs(ts - predicted) < _RANSAC_RESIDUAL_TOL
        if inliers.sum() >= min_inliers and (
            best_inliers is None or inliers.sum() > best_inliers.sum()
        ):
            best_inliers = inliers

    if best_inliers is None:
        return None

    xin, tin = xs[best_inliers], ts[best_inliers]
    c_coef, b_coef, a_coef = np.polyfit(xin, tin**2, 2)
    if c_coef <= 1e-9:
        return None
    k = 1 / np.sqrt(c_coef)
    x0 = -b_coef / (2 * c_coef)
    t0_sq = a_coef - b_coef**2 / (4 * c_coef)
    if t0_sq < 0:
        return None
    t0 = np.sqrt(t0_sq)

    predicted = np.sqrt(t0**2 + ((xin - x0) / k) ** 2)
    ss_res = np.sum((tin - predicted) ** 2)
    ss_tot = np.sum((tin - tin.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return HyperbolaFit(x0=x0, t0=t0, k=k, r2=r2, n_inliers=int(best_inliers.sum()), n_total=n)


def estimate_velocity(fit: HyperbolaFit, shaft_interval_m: float, sample_interval_ns: float) -> float:
    """Physical propagation velocity (m/ns) implied by a hyperbola's fitted curvature k.

    Derivation: t(x) = sqrt(t0^2 + ((x-x0)/k)^2) in (trace, sample) index units is the same
    curve as the physical two-way-time hyperbola T(X) = sqrt(T0^2 + (2(X-X0)/v)^2) once
    X = x * shaft_interval_m and T = t * sample_interval_ns are substituted:
    t = sqrt(t0^2 + ((x-x0) * 2*shaft_interval_m/(v*sample_interval_ns))^2) — comparing the two
    forms gives 1/k = 2*shaft_interval_m/(v*sample_interval_ns), so v = 2*k*shaft_interval_m /
    sample_interval_ns (k in the numerator — an earlier version of this had it inverted, caught
    by comparing against a direct physical-units refit on a real confirmed hyperbola, which gave
    a physically impossible v > c until fixed; see tests/test_detect_hyperbola.py).

    This is the common-offset hyperbola-fitting method, and it ignores the transmitter-receiver
    separation: the textbook correction is t(x) = [t0(x - xa/2) + t0(x + xa/2)] / 2 for an
    antenna separation xa. Published work on this correction found the *effective* separation
    (the real electromagnetic path) differs from the geometric one and has to be calibrated per
    instrument, which is why it is not applied here — SPRScan 3D's separation is unknown
    (docs/COMPANY_QUESTIONS.md). Uncorrected, the fit slightly overestimates velocity, and so
    depth, for shallow targets where xa is not small against the depth.
    """
    return 2 * fit.k * shaft_interval_m / sample_interval_ns


def local_coherence(traces: np.ndarray, x: int, y: int, w: int, h: int) -> float:
    """Mean normalized cross-correlation between adjacent traces within the box.

    Undisturbed, real reflectors (point or linear) vary smoothly trace-to-trace; disturbed/
    backfilled ground does not — see docs/GPR_PATTERN_REFERENCE.md's disturbed_zone section.
    """
    n_traces, n_samples = traces.shape
    y_hi = min(y + h, n_samples)
    x_lo, x_hi = max(0, x), min(n_traces, x + w)
    if x_hi - x_lo < 2 or y_hi - y <= 1:
        return 0.0

    segment = traces[x_lo:x_hi, y:y_hi].astype(np.float64)
    correlations = []
    for i in range(len(segment) - 1):
        a, b = segment[i], segment[i + 1]
        a_std, b_std = a.std(), b.std()
        if a_std < 1e-9 or b_std < 1e-9:
            continue
        correlations.append(np.corrcoef(a, b)[0, 1])
    return float(np.mean(correlations)) if correlations else 0.0


def diagnose_box(
    traces: np.ndarray,
    normalized: np.ndarray,
    box_id: str,
    x: int,
    y: int,
    w: int,
    h: int,
    shaft_interval_m: float | None,
    sample_interval_ns: float | None,
) -> Diagnosis:
    y_hi = min(y + h, normalized.shape[0])
    x_hi = min(x + w, normalized.shape[1])
    peak_amplitude = float(normalized[y:y_hi, x:x_hi].max()) if y_hi > y and x_hi > x else 0.0
    aspect_ratio = max(w, h) / min(w, h) if min(w, h) > 0 else float("inf")
    coherence = local_coherence(traces, x, y, w, h)

    ridge_x, ridge_t = extract_ridge_points(normalized, x, y, w, h)
    fit = fit_hyperbola_ransac(ridge_x, ridge_t) if len(ridge_x) >= 3 else None

    velocity = implied_eps = None
    if fit is not None and shaft_interval_m is not None and sample_interval_ns is not None:
        candidate_velocity = estimate_velocity(fit, shaft_interval_m, sample_interval_ns)
        # A velocity at or above the speed of light in vacuum is physically impossible --
        # confirmed to happen for ~10% of "point" fits across the real dataset (R² can be high
        # on a handful of inlier points while the curvature k itself is poorly constrained).
        # Report unavailable rather than a number that can't be true, matching this project's
        # confidence discipline elsewhere (core/contracts.py's Evidence: never fabricate).
        if 0 < candidate_velocity < _SPEED_OF_LIGHT_M_PER_NS:
            velocity = candidate_velocity
            implied_eps = (_SPEED_OF_LIGHT_M_PER_NS / velocity) ** 2

    if fit is not None and fit.r2 >= _HYPERBOLA_R2_THRESHOLD:
        shape = "point"
        if peak_amplitude >= STRONG_AMPLITUDE_THRESHOLD:
            suggested_class = "clear_point_reflector"
        else:
            suggested_class = "low_snr_point_reflector"
        rationale = (
            f"hyperbola fit R²={fit.r2:.2f} on {fit.n_inliers}/{fit.n_total} ridge points "
            f"(RANSAC) — matches the classical point-diffractor model well. "
            f"peak_amplitude={peak_amplitude:.2f} (normalized)."
        )
    elif aspect_ratio >= _LINEAR_ASPECT_THRESHOLD and (fit is None or fit.r2 < 0.5):
        shape = "linear"
        suggested_class = "elongated_linear_target"
        fit_note = f"r2={fit.r2:.2f}" if fit else "no hyperbola fit found"
        rationale = (
            f"wide/flat box (aspect ratio {aspect_ratio:.1f}) with a poor hyperbola fit "
            f"({fit_note}) — consistent with a reflector running parallel to the survey line "
            f"rather than a point crossing it."
        )
    elif coherence < _COHERENCE_DISTURBED_THRESHOLD:
        shape = "disturbed"
        suggested_class = "disturbed_zone"
        rationale = (
            f"low trace-to-trace coherence ({coherence:.2f}) and no clean hyperbola fit — "
            f"consistent with chaotic/disturbed reflectivity rather than a discrete object."
        )
    else:
        shape = "ambiguous"
        suggested_class = None
        fit_note = f"r2={fit.r2:.2f}" if fit else "no fit"
        rationale = (
            f"neither a strong hyperbola fit ({fit_note}), a linear shape "
            f"(aspect={aspect_ratio:.1f}), nor low coherence ({coherence:.2f}) — "
            f"needs human review, evidence doesn't clearly point to one class."
        )

    return Diagnosis(
        box_id=box_id,
        shape=shape,
        suggested_class=suggested_class,
        rationale=rationale,
        fit_r2=fit.r2 if fit else None,
        n_ridge_inliers=fit.n_inliers if fit else None,
        n_ridge_total=fit.n_total if fit else None,
        aspect_ratio=aspect_ratio,
        local_coherence=coherence,
        peak_amplitude=peak_amplitude,
        velocity_m_per_ns=velocity,
        implied_dielectric=implied_eps,
    )
