"""Radargram measurements shared by the pipeline, the Studio and the diagnosis scripts.

Moved here from scripts/diagnose_candidates.py once the live pipeline needed them too
(`detect/refine.py`): core pipeline code must not import from `scripts/`, which is not
part of the installed package.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# Normalised-envelope units. A point target at or above this stands well clear of the
# ground around it — the "clear" vs "low-SNR" line, as validated in the diagnosis work.
STRONG_AMPLITUDE_THRESHOLD = 4.0

# How far along the line a point reflector's hyperbola stays diagnostic: the 45-degree
# diffraction aperture, one target depth each side of the apex. Past it the limb has
# flattened towards the straight asymptote every deeper target shares, so it says little
# about *this* target. It lives here because two packages need the same number and must
# not drift: the synthetic generator spaces objects by it and bounds each label prior to
# it (`simulate/scenes.py`, `simulate/labels.py`), and the Studio boxes a picked apex with
# it before measuring that target (`studio/interpret.py`).
APERTURE_DEPTHS = 1.0
MIN_APERTURE_M = 0.15  # a target at the surface still has the antenna's own footprint


# How long the transmitted pulse and its ringing dominate a trace, as a *time*, not a fraction of
# the record. This matters because the three channels sample at 0.1/0.2/0.4 ns: the same 12% of
# samples is 3.07 ns on RAD but 6.14 ns on RA1 and 12.29 ns on RA2, so a fraction-based rule threw
# away everything shallower than 0.61 m on the deep channel while keeping 0.15 m on the shallow one.
# The direct wave is a property of the antenna, not of the record length.
#
# Derived from the instrument constants measured off the four real survey lines
# (simulate/instrument.py): the direct-wave peak sits at sample 20 of the RAD channel at 0.1 ns,
# i.e. 2.0 ns, and half a period at the measured 466 MHz centre frequency is 1.07 ns. Their sum
# reproduces exactly the 3.07 ns that the old RAD-tuned fraction produced — so this keeps the
# behaviour that was actually validated, and only fixes what it did to the other two channels.
DIRECT_WAVE_WINDOW_NS = 3.07

# Nothing ordinary propagates slower than water (eps 81). Above this a fit is not describing a
# material, it is an artefact of a badly constrained curvature, so it is the one permittivity value
# worth rejecting outright. Everything below it is a *reading* about the ground — see
# permittivity_signal() — because the anomalies a utility survey exists to find are exactly the
# values that are not ordinary soil.
PERMITTIVITY_IMPLAUSIBLE_ABOVE = 90.0

# Air is 1. A fit landing near it says the wave crossed a void rather than soil, which is the same
# physics detect/refine.py's polarity rule reasons about from the other direction.
PERMITTIVITY_AIR_LIKE_BELOW = 2.5


@dataclass(frozen=True)
class PermittivitySignal:
    """What a fitted permittivity says about the ground — a reading, never a verdict."""

    label: str  # "air_like" | "fast" | "soil_like" | "wet" | "implausible"
    note: str
    suggests_class: str | None  # a taxonomy class this reading is consistent with, or None


def permittivity_signal(dielectric: float, site_dielectric: float | None) -> PermittivitySignal:
    """Interpret a fit's implied permittivity instead of discarding it for being unusual.

    This replaced a hard filter that accepted only values near the site's, which rejected 42 of 158
    fits across the four real lines — including eps 1.18 at 1.61 m on 40 inlier points, eps 1.29 at
    0.72 m on 36, and eps 1.31 at 1.86 m on 27. Those are well-constrained measurements saying the
    wave travelled at close to the speed of light through that path, which is air: a void, or an
    air-filled duct. Filtering toward "ordinary soil" threw away precisely what a utility survey is
    looking for, and `cavities` is the second-highest-weighted class in the risk model.
    """
    if dielectric >= PERMITTIVITY_IMPLAUSIBLE_ABOVE:
        return PermittivitySignal(
            "implausible",
            f"implied permittivity {dielectric:.0f} is beyond water ({PERMITTIVITY_IMPLAUSIBLE_ABOVE:.0f}) "
            "— the curvature is not constrained, not a material",
            None,
        )
    if dielectric <= PERMITTIVITY_AIR_LIKE_BELOW:
        return PermittivitySignal(
            "air_like",
            f"implied permittivity {dielectric:.2f} is close to air — consistent with a void or an "
            "air-filled duct on the path to this target",
            "cavities",
        )
    if site_dielectric is None:
        return PermittivitySignal(
            "soil_like", f"implied permittivity {dielectric:.1f}; no site value recorded to compare it to", None
        )
    if dielectric < site_dielectric / 2.0:
        return PermittivitySignal(
            "fast",
            f"implied permittivity {dielectric:.1f} is well below the site's {site_dielectric:.1f} — "
            "drier or more voided ground than the site average on the path to this target",
            None,
        )
    if dielectric > site_dielectric * 2.0:
        return PermittivitySignal(
            "wet",
            f"implied permittivity {dielectric:.1f} is well above the site's {site_dielectric:.1f} — "
            "consistent with saturated ground or a water-filled pipe on the path to this target",
            None,
        )
    return PermittivitySignal(
        "soil_like",
        f"implied permittivity {dielectric:.1f} agrees with the site's {site_dielectric:.1f}",
        None,
    )


def hyperbola_half_aperture_m(depth_m: float) -> float:
    """Half-width, along the line, of the diagnostic part of a point reflector's hyperbola."""
    return max(APERTURE_DEPTHS * depth_m, MIN_APERTURE_M)


_COHERENT_ECHO_FRACTION = 0.25  # a stacked echo weaker than this vs the box's peak is noise, not an echo
_MAIN_LOBE_FRACTION = 0.5  # a Ricker's side lobes reach ~45% of the main lobe; this skips them


def direct_wave_skip_samples(n_samples: int, sample_interval_ns: float) -> int:
    """How many samples DIRECT_WAVE_WINDOW_NS covers, on this channel's own sample rate.

    A *time*, not a fraction of the record — see DIRECT_WAVE_WINDOW_NS's own comment. The
    three channels sample at 0.1/0.2/0.4 ns, so a fixed sample count would be three different
    depths; a fixed fraction of n_samples would be three different times. Only a real time
    converted per-channel is actually "the direct wave and its ringing" on every channel.
    """
    if sample_interval_ns <= 0:
        raise ValueError(f"sample_interval_ns must be positive, got {sample_interval_ns}")
    return min(n_samples, round(DIRECT_WAVE_WINDOW_NS / sample_interval_ns))


def compute_normalized_envelope(traces: np.ndarray, sample_interval_ns: float) -> np.ndarray:
    """Background-removed, depth-wise (AGC) normalized energy envelope — shape (n_samples, n_traces).

    `traces` is (n_traces, n_samples). scripts/detect_candidates.py keeps its own copy on
    purpose: detection tunes for recall, diagnosis for fit quality — but both share
    DIRECT_WAVE_WINDOW_NS rather than each hand-tuning their own skip.
    """
    _n_traces, n_samples = traces.shape
    mean_trace = traces.astype(np.float64).mean(axis=0, keepdims=True)
    residual = (traces.astype(np.float64) - mean_trace).T
    power = (residual**2).astype(np.float32)
    envelope = cv2.blur(power, (3, 17))
    envelope = np.sqrt(np.clip(envelope, 0, None))

    skip = direct_wave_skip_samples(n_samples, sample_interval_ns)
    envelope[:skip, :] = 0
    if not np.any(envelope > 0):
        return envelope

    row_background = np.median(envelope, axis=1, keepdims=True)
    floor = np.median(envelope[skip:]) * 0.15
    row_background = np.maximum(row_background, floor)
    normalized = (envelope / row_background).astype(np.float32)
    normalized[:skip, :] = 0
    return normalized


def echo_matches_direct_wave_polarity(
    traces: np.ndarray, rows: slice, cols: slice, sample_interval_ns: float
) -> bool | None:
    """Does the first strong echo inside a box share the direct wave's polarity?

    A reflection off a *drop* in permittivity — soil into an air gap — keeps the incident
    pulse's polarity; one off a *rise* (soil into water or concrete) or off metal flips
    it. The direct wave is the one pulse in every trace whose polarity is the source's
    own, so it is the reference.

    The box's traces are stacked first: a flat-topped cavity echo reinforces, while the
    scattered echoes of broken-up ground cancel. Returns None when the stack shows no
    coherent echo — the honest answer for chaotic ground, and never a guess.
    """
    radargram = traces.T.astype(np.float64)  # (n_samples, n_traces)
    mean_trace = radargram.mean(axis=1)
    top = max(1, direct_wave_skip_samples(radargram.shape[0], sample_interval_ns))
    direct_sign = np.sign(mean_trace[int(np.argmax(np.abs(mean_trace[:top])))])

    box = (radargram - mean_trace[:, None])[rows, cols]
    if box.size == 0 or direct_sign == 0:
        return None
    stack = box.mean(axis=1)
    strength = float(np.abs(stack).max())
    if strength == 0 or strength < _COHERENT_ECHO_FRACTION * float(np.abs(box).max()):
        return None
    first_main_lobe = int(np.argmax(np.abs(stack) >= _MAIN_LOBE_FRACTION * strength))
    return bool(np.sign(stack[first_main_lobe]) == direct_sign)
