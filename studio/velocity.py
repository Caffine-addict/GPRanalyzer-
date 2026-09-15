"""Hyperbola fitting and velocity analysis — the interpreter's calibration tool.

This is the one place in the project where a *measured* propagation velocity can
come from, so it deserves stating plainly why that matters.

Every depth this software reports is `depth = velocity * two_way_time / 2`.
Velocity is currently taken from the file header's `SPR_MEDIUM_DIELECTRIC 9.00`
— a value the operator dialled in on site, not something the instrument
measured — and the time axis rests on `SPR_SAMPLING_INTERVAL` being in
picoseconds, which is an inference (`parsers/spr.py`). Both assumptions sit
under every depth figure the system produces.

A hyperbola breaks that circularity. A buried point reflector is recorded from
every antenna position that can still hear it, and the curvature of the
resulting hyperbola is fixed by the velocity of the ground it travelled
through. Fit the curve, and the velocity falls out of the geometry — no header
value involved. Do it on several targets down a line, and either they cluster
near the assumed velocity (the assumption holds) or they don't (it doesn't, and
now that's known rather than suspected).

Fitting itself is not reimplemented here: `detect/hyperbola.py` already does
RANSAC hyperbola fitting, validated against confirmed targets in
`tests/test_detect_hyperbola.py`. This module wraps it for interactive use
and converts the result into the physical quantities an interpreter reads.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from detect.hyperbola import (
    estimate_velocity,
    extract_ridge_points,
    fit_hyperbola_ransac,
)
from detect.measure import (
    DIRECT_WAVE_WINDOW_NS,
    PERMITTIVITY_IMPLAUSIBLE_ABOVE,
    compute_normalized_envelope,
)
from studio.processing import SPEED_OF_LIGHT_M_PER_NS, migrate

# --- credibility of a fit -------------------------------------------------------------------
# A fit can be geometrically excellent and still be nonsense. These are the project's own hard-won
# acceptance rules (docs/GPR_PATTERN_REFERENCE.md), gathered in one place because they were being
# re-derived ad hoc: applied to the four real lines they cut 14 apparently-corroborated targets to
# 6, and every one they removed was traceable to clutter, a truncated hyperbola, or a fit on noise.
RECORD_FLOOR_FRACTION = 0.90  # past this the hyperbola is cut off by the record and its curvature lies
MIN_CREDIBLE_INLIERS = 8  # below this the curvature k is too loosely constrained to imply a velocity


def fit_rejection_reason(
    fit: VelocityFit, *, n_samples: int, sample_interval_ns: float
) -> str | None:
    """Why this fit should not be believed, or None if it stands.

    Deliberately returns the *reason* rather than a bool: a rejected fit is diagnostic information
    — a line where most rejections are "apex inside the direct-wave band" is telling you something
    different from one where they are "faster than light".

    **Only physics rejects here.** An earlier version also required the implied permittivity to sit
    near the site's, and across the four real lines that was the single largest cause of rejection:
    42 of 158 fits, including eps 1.18 on 40 inlier points. Those are voids — the thing a utility
    survey exists to find — discarded for not resembling soil. Unusual permittivity is now a
    *reading* (`detect.measure.permittivity_signal`) and only a value beyond water, where the
    curvature cannot be describing any material, is still refused.
    """
    if not fit.physically_plausible:
        return "implies propagation faster than light — fitted onto noise"
    if fit.apex_time_ns < DIRECT_WAVE_WINDOW_NS:
        return (
            f"apex at {fit.apex_time_ns:.2f} ns is inside the direct-wave and ringing band "
            f"(first {DIRECT_WAVE_WINDOW_NS} ns)"
        )
    if fit.apex_sample > RECORD_FLOOR_FRACTION * n_samples:
        return "apex sits past the usable record, so the limbs are truncated and the depth is unreliable"
    if fit.n_inliers < MIN_CREDIBLE_INLIERS:
        return f"only {fit.n_inliers} inlier ridge points — too few to constrain the curvature"
    if fit.dielectric >= PERMITTIVITY_IMPLAUSIBLE_ABOVE:
        return (
            f"implied permittivity {fit.dielectric:.0f} is beyond water — the curvature is not "
            "constrained enough to describe any material"
        )
    return None


# Above this the "velocity" implies propagation faster than light in vacuum,
# i.e. the fit is geometrically fine but physically impossible — almost always
# a fit onto noise or onto two unrelated targets. Reported, never silently used.
_MAX_PLAUSIBLE_VELOCITY_M_PER_NS = SPEED_OF_LIGHT_M_PER_NS


@dataclass(frozen=True)
class VelocityFit:
    """A fitted hyperbola, in the units an interpreter actually reads.

    `physically_plausible` is False for a fit implying faster-than-light
    propagation. Such a fit is still returned with all its numbers intact —
    seeing *why* it is nonsense is more use than an error message, and hiding
    it would leave the operator wondering where their pick went.
    """

    apex_trace: float
    apex_sample: float
    apex_time_ns: float
    velocity_m_per_ns: float
    dielectric: float
    depth_m: float
    r2: float
    n_inliers: int
    n_total: int
    physically_plausible: bool


def dielectric_from_velocity(velocity_m_per_ns: float) -> float:
    """Relative permittivity implied by a propagation velocity: eps = (c/v)^2."""
    if velocity_m_per_ns <= 0:
        raise ValueError(f"velocity must be positive, got {velocity_m_per_ns}")
    return (SPEED_OF_LIGHT_M_PER_NS / velocity_m_per_ns) ** 2


def velocity_from_dielectric(dielectric: float) -> float:
    """Propagation velocity implied by a relative permittivity: v = c/sqrt(eps)."""
    if dielectric < 1.0:
        raise ValueError(f"relative permittivity cannot be below 1, got {dielectric}")
    return SPEED_OF_LIGHT_M_PER_NS / np.sqrt(dielectric)


def depth_from_time(two_way_time_ns: float, velocity_m_per_ns: float) -> float:
    """Depth to a reflector: half the two-way path at the given velocity."""
    if two_way_time_ns < 0:
        raise ValueError(f"two-way time cannot be negative, got {two_way_time_ns}")
    return velocity_m_per_ns * two_way_time_ns / 2.0


def hyperbola_curve(
    *,
    apex_trace: float,
    apex_time_ns: float,
    velocity_m_per_ns: float,
    trace_spacing_m: float,
    n_traces: int,
    half_width_traces: int = 60,
) -> list[dict[str, float]]:
    """The diffraction hyperbola a point reflector at this apex would draw.

    Used two ways by the viewer: drawn over the data as a live overlay while
    the operator tunes velocity by eye (the classic manual fit — turn the dial
    until the curve lies on the limbs), and drawn from an automatic fit to show
    what was actually fitted.
    """
    if velocity_m_per_ns <= 0:
        raise ValueError(f"velocity must be positive, got {velocity_m_per_ns}")

    lo = max(0, int(apex_trace) - half_width_traces)
    hi = min(n_traces, int(apex_trace) + half_width_traces + 1)
    traces = np.arange(lo, hi, dtype=np.float64)
    lateral_m = (traces - apex_trace) * trace_spacing_m
    times = np.sqrt(apex_time_ns**2 + (2.0 * lateral_m / velocity_m_per_ns) ** 2)
    return [{"trace": float(t), "time_ns": float(v)} for t, v in zip(traces, times, strict=True)]


def velocity_from_limb_point(
    *,
    apex_trace: float,
    apex_time_ns: float,
    limb_trace: float,
    limb_time_ns: float,
    trace_spacing_m: float,
) -> float:
    """Velocity implied by dragging one point onto a hyperbola limb.

    Solves t^2 = t0^2 + (2x/v)^2 for v, given the apex and one point on the
    curve — this is the manual-fit gesture, where the operator drags outward
    from the apex until the overlay sits on the limbs.
    """
    lateral_m = abs(limb_trace - apex_trace) * trace_spacing_m
    time_excess_sq = limb_time_ns**2 - apex_time_ns**2
    if lateral_m <= 0 or time_excess_sq <= 0:
        raise ValueError(
            "limb point must be lateral to, and later than, the apex — "
            f"got dx={lateral_m:.4f} m, dt^2={time_excess_sq:.4f} ns^2"
        )
    return 2.0 * lateral_m / np.sqrt(time_excess_sq)


def fit_region(
    raw_traces: np.ndarray,
    *,
    trace_start: int,
    sample_start: int,
    trace_span: int,
    sample_span: int,
    trace_spacing_m: float,
    sample_interval_ns: float,
    seed: int = 0,
) -> VelocityFit | None:
    """Automatically fit a hyperbola inside an operator-drawn region.

    `raw_traces` is in source orientation, (n_traces, n_samples), matching
    `ScanFrame.traces` — the fitter works off the raw signal deliberately, not
    off whatever the display chain is currently doing, so the measured velocity
    doesn't move when someone changes the gain.

    Returns None when the region's ridge points don't describe a hyperbola.
    That is a real answer ("there's no point reflector here"), not a failure.
    """
    if trace_span < 3 or sample_span < 3:
        raise ValueError(f"fit region too small: {trace_span}x{sample_span}")
    if sample_interval_ns <= 0 or trace_spacing_m <= 0:
        raise ValueError("fit needs positive trace spacing and sample interval")

    envelope = compute_normalized_envelope(raw_traces, sample_interval_ns)
    xs, ts = extract_ridge_points(envelope, trace_start, sample_start, trace_span, sample_span)
    fit = fit_hyperbola_ransac(xs, ts, seed=seed)
    if fit is None:
        return None

    velocity = estimate_velocity(fit, trace_spacing_m, sample_interval_ns)
    apex_time_ns = fit.t0 * sample_interval_ns
    plausible = 0 < velocity <= _MAX_PLAUSIBLE_VELOCITY_M_PER_NS
    return VelocityFit(
        apex_trace=float(fit.x0),
        apex_sample=float(fit.t0),
        apex_time_ns=float(apex_time_ns),
        velocity_m_per_ns=float(velocity),
        # A faster-than-light fit has no meaningful permittivity (it would be
        # below 1); report the velocity as-is and leave eps at the vacuum floor
        # rather than raising out of a display path.
        dielectric=float(dielectric_from_velocity(velocity)) if plausible else 1.0,
        depth_m=float(depth_from_time(apex_time_ns, velocity)) if plausible else 0.0,
        r2=float(fit.r2),
        n_inliers=int(fit.n_inliers),
        n_total=int(fit.n_total),
        physically_plausible=bool(plausible),
    )


# --- velocity by migration focusing -----------------------------------------------------------
# The second, independent way to measure velocity, and the one the literature automates. Migration
# collapses a hyperbola back to its apex, but only at the right velocity: too low and it stays open,
# too high and it over-collapses into a "smile". So sweeping trial velocities and asking which one
# concentrates the most energy at the apex *is* a velocity measurement. `migrate()` already existed
# and its docstring already noted this sensitivity; nothing drove it over a range until now.
#
# Why this is worth having alongside fit_region: the two methods fail differently. RANSAC fits ridge
# points and can be fooled by a tight fit on few inliers; focusing uses every sample in the window
# and cannot be, but it blurs when the ground is heterogeneous. Agreement between them is much
# stronger evidence than either alone — which is the same argument as cross-channel corroboration,
# applied to method rather than receiver.

SWEEP_MIN_VELOCITY_M_PER_NS = 0.03  # eps ~100: wetter than anything ordinary
SWEEP_MAX_VELOCITY_M_PER_NS = 0.15  # eps ~4: dry sand
SWEEP_STEPS = 49
# Measured, not guessed: on pure noise the apex-peak-over-local-rms metric sits at 1.37-1.52
# (10 seeds), on a real hyperbola at 6.3-8.6 across the sweep range. 3.0 sits with wide margin on
# both sides of that gap.
_FLAT_PEAK_RATIO = 3.0


@dataclass(frozen=True)
class FocusingSweep:
    """What a trial-velocity sweep found, including how sure it is.

    `energies` is kept in full rather than reduced to the winner: a sharp peak and a flat curve
    give the same `best_velocity_m_per_ns` but mean completely different things, and collapsing
    them would hide exactly the uncertainty this project reports everywhere else.
    """

    velocities_m_per_ns: tuple[float, ...]
    energies: tuple[float, ...]
    best_velocity_m_per_ns: float
    best_dielectric: float
    peak_ratio: float  # peak energy over median energy; ~1 means the sweep is uninformative

    @property
    def well_constrained(self) -> bool:
        """Did the sweep actually pick a velocity, or is the curve flat?"""
        return self.peak_ratio >= _FLAT_PEAK_RATIO


def estimate_velocity_by_focusing(
    radargram: np.ndarray,
    *,
    apex_trace: float,
    apex_sample: float,
    trace_spacing_m: float,
    sample_interval_ns: float,
    half_width_traces: int = 60,
    half_height_samples: int = 40,
    aperture_traces: int = 60,
    velocities_m_per_ns: Sequence[float] | None = None,
) -> FocusingSweep:
    """Measure velocity by finding which one focuses this target best.

    `radargram` is (n_samples, n_traces) — the orientation `studio.session.load_radargram` returns
    and `migrate` expects. Give it background-removed, ungained data: the processing chain migrates
    before gain deliberately, because gain distorts the relative amplitudes focusing depends on.

    Only the trace range around the target is cropped, not the sample range: `migrate`'s
    travel-time formula measures from row 0 as physical two-way time zero, so cropping rows
    starting at some `apex_sample - half_height_samples` would silently shift the time origin and
    corrupt every travel-time computed against it — a real, previously-shipped bug for any apex not
    near the top of the record. Cropping traces is safe because lateral offset is relative.
    """
    if radargram.ndim != 2:
        raise ValueError(f"radargram must be (n_samples, n_traces), got shape {radargram.shape}")
    if trace_spacing_m <= 0 or sample_interval_ns <= 0:
        raise ValueError("focusing needs positive trace spacing and sample interval")

    trials = (
        np.linspace(SWEEP_MIN_VELOCITY_M_PER_NS, SWEEP_MAX_VELOCITY_M_PER_NS, SWEEP_STEPS)
        if velocities_m_per_ns is None
        else np.asarray(list(velocities_m_per_ns), dtype=np.float64)
    )
    if trials.size == 0 or np.any(trials <= 0):
        raise ValueError("trial velocities must be a non-empty set of positive values")

    n_samples, n_traces = radargram.shape
    t_lo = max(0, int(apex_trace) - half_width_traces)
    t_hi = min(n_traces, int(apex_trace) + half_width_traces + 1)
    if t_hi - t_lo < 3 or n_samples < 3:
        raise ValueError("focusing window is too small — the target sits at the edge of the record")

    window = radargram[:, t_lo:t_hi]
    apex_row = int(apex_sample)
    apex_col = int(apex_trace) - t_lo
    # A small box at the apex: migration concentrates energy there, so that is where to look.
    row_lo, row_hi = max(0, apex_row - 3), min(n_samples, apex_row + 4)
    col_lo, col_hi = max(0, apex_col - 3), min(window.shape[1], apex_col + 4)
    # The RMS reference stays local to the target's depth, not the whole record, so a change far
    # from the apex (different clutter, different gain) can't move the normalisation.
    rms_row_lo = max(0, apex_row - half_height_samples)
    rms_row_hi = min(n_samples, apex_row + half_height_samples + 1)

    energies = []
    for velocity in trials:
        migrated = migrate(
            window,
            trace_spacing_m=trace_spacing_m,
            sample_interval_ns=sample_interval_ns,
            velocity_m_per_ns=float(velocity),
            aperture_traces=aperture_traces,
        )
        patch = migrated[row_lo:row_hi, col_lo:col_hi]
        apex_peak = float(np.max(np.abs(patch))) if patch.size else 0.0
        local = migrated[rms_row_lo:rms_row_hi, :]
        window_rms = float(np.sqrt(np.mean(local**2))) if local.size else 0.0
        energies.append(apex_peak / window_rms if window_rms > 0 else 0.0)

    energy_array = np.asarray(energies, dtype=np.float64)
    best_index = int(np.argmax(energy_array))
    best_velocity = float(trials[best_index])
    median_energy = float(np.median(energy_array))
    peak_ratio = float(energy_array[best_index] / median_energy) if median_energy > 0 else 0.0

    return FocusingSweep(
        velocities_m_per_ns=tuple(float(v) for v in trials),
        energies=tuple(float(e) for e in energy_array),
        best_velocity_m_per_ns=best_velocity,
        best_dielectric=float(dielectric_from_velocity(best_velocity)),
        peak_ratio=peak_ratio,
    )
