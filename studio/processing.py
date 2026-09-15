"""The radargram processing chain — the operations a GPR interpreter expects to have.

Every function here takes a radargram in **(n_samples, n_traces)** orientation
(rows = two-way time, columns = distance along the line — the way a radargram
is actually looked at) and returns a *new* array. Nothing mutates its input,
so the chain can be re-run from raw with different parameters on every request
without any cache-invalidation dance.

Two rules this module holds to, both inherited from the rest of the project:

1. **Nothing here invents data.** Every operation is a documented, standard GPR
   processing step (dewow, background removal, band-pass, gain, stacking,
   Kirchhoff migration) — the same chain RADAN/ReflexW/Radar Studio expose.
   None of them is a cosmetic smoothing pass applied to make output look
   cleaner than the signal is.

2. **Depth is a derived, uncertain quantity.** Anything needing a velocity
   takes it as an explicit argument rather than reaching for a default, because
   the velocity comes from `SPR_MEDIUM_DIELECTRIC` and a sampling interval
   whose unit is still unconfirmed (see `parsers/spr.py`,
   `docs/COMPANY_QUESTIONS.md` #2). Callers are forced to be deliberate about
   it; `studio/velocity.py` is where a *measured* velocity can come from.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np

SPEED_OF_LIGHT_M_PER_NS = 0.2998


@dataclass(frozen=True)
class ProcessingChain:
    """Which steps run, and with what parameters. Frozen — `replace()` to change one."""

    time_zero_sample: int = 0
    dewow: bool = False
    dewow_window_samples: int = 25
    background_removal: str = "none"  # "none" | "mean" | "moving"
    background_window_traces: int = 51
    bandpass: bool = False
    bandpass_low_mhz: float = 100.0
    bandpass_high_mhz: float = 1200.0
    gain: str = "none"  # "none" | "agc" | "exponential" | "linear"
    agc_window_samples: int = 40
    gain_exponent: float = 2.0
    stack_traces: int = 1
    migrate: bool = False
    migration_velocity_m_per_ns: float = 0.0999  # eps=9 -> c/3; overridden by a measured fit
    migration_aperture_traces: int = 40


def _as_float(data: np.ndarray) -> np.ndarray:
    if data.ndim != 2:
        raise ValueError(f"radargram must be 2-D (n_samples, n_traces), got shape {data.shape}")
    if not np.isfinite(data).all():
        raise ValueError("radargram contains non-finite samples — cannot process")
    return data.astype(np.float64, copy=True)


def apply_time_zero(data: np.ndarray, first_sample: int) -> np.ndarray:
    """Drop the samples above the first-break so row 0 is the ground surface.

    Rows are removed, not shifted-and-zero-filled: a zero-filled row is a
    fabricated measurement at a depth nothing was recorded at.
    """
    data = _as_float(data)
    if first_sample <= 0:
        return data
    if first_sample >= data.shape[0]:
        raise ValueError(f"time_zero_sample {first_sample} is at or past the end of the trace")
    return data[first_sample:, :]


def dewow(data: np.ndarray, window_samples: int) -> np.ndarray:
    """Remove the low-frequency 'wow' by subtracting a running mean down each trace.

    Wow is the slow DC drift/saturation recovery riding under the real
    wavelet; left in, it dominates any gain applied afterwards.
    """
    data = _as_float(data)
    window = max(3, int(window_samples) | 1)  # odd, so the mean is centred
    if window >= data.shape[0]:
        return data - data.mean(axis=0, keepdims=True)

    kernel = np.ones(window) / window
    padded = np.pad(data, ((window // 2, window // 2), (0, 0)), mode="edge")
    running = np.apply_along_axis(lambda col: np.convolve(col, kernel, mode="valid"), 0, padded)
    return data - running


def remove_background(data: np.ndarray, mode: str, window_traces: int) -> np.ndarray:
    """Strip the horizontal banding (direct wave, antenna ringing, flat layering).

    "mean" subtracts one average trace computed over the whole line — the
    classic move, but it also erases any genuinely flat reflector.
    "moving" subtracts a locally-averaged trace instead, which keeps flat
    features that don't persist across the full window.
    """
    data = _as_float(data)
    if mode == "none":
        return data
    if mode == "mean":
        return data - data.mean(axis=1, keepdims=True)
    if mode != "moving":
        raise ValueError(f"unknown background_removal mode: {mode!r}")

    window = max(3, int(window_traces) | 1)
    if window >= data.shape[1]:
        return data - data.mean(axis=1, keepdims=True)
    kernel = np.ones(window) / window
    padded = np.pad(data, ((0, 0), (window // 2, window // 2)), mode="edge")
    running = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode="valid"), 1, padded)
    return data - running


def bandpass(data: np.ndarray, sample_interval_ns: float, low_mhz: float, high_mhz: float) -> np.ndarray:
    """Zero-phase frequency filter down each trace, via FFT with a cosine taper.

    Implemented on the FFT rather than as an IIR filter so it is exactly
    zero-phase — an IIR pass would shift arrival times, which is precisely the
    quantity everything downstream turns into depth. The taper (rather than a
    brick wall) keeps the filter from ringing the wavelet.
    """
    data = _as_float(data)
    if sample_interval_ns <= 0:
        raise ValueError(f"sample_interval_ns must be positive, got {sample_interval_ns}")
    if low_mhz >= high_mhz:
        raise ValueError(f"bandpass low ({low_mhz}) must be below high ({high_mhz}) MHz")

    n_samples = data.shape[0]
    freqs_mhz = np.fft.rfftfreq(n_samples, d=sample_interval_ns * 1e-9) / 1e6
    taper_lo, taper_hi = low_mhz * 0.5, high_mhz * 1.5

    response = np.ones_like(freqs_mhz)
    rising = (freqs_mhz >= taper_lo) & (freqs_mhz < low_mhz)
    response[freqs_mhz < taper_lo] = 0.0
    response[rising] = 0.5 * (1 - np.cos(np.pi * (freqs_mhz[rising] - taper_lo) / (low_mhz - taper_lo)))
    falling = (freqs_mhz > high_mhz) & (freqs_mhz <= taper_hi)
    response[falling] = 0.5 * (1 + np.cos(np.pi * (freqs_mhz[falling] - high_mhz) / (taper_hi - high_mhz)))
    response[freqs_mhz > taper_hi] = 0.0

    return np.fft.irfft(np.fft.rfft(data, axis=0) * response[:, None], n=n_samples, axis=0)


def apply_gain(data: np.ndarray, mode: str, agc_window_samples: int, exponent: float) -> np.ndarray:
    """Compensate for depth-dependent signal loss.

    Radar amplitude falls off with depth from spreading plus absorption, so a
    raw radargram shows almost nothing below the first metre. Gain is a
    *display* correction: it makes deep reflectors visible, and in doing so
    destroys relative amplitude. Anything reading amplitude as evidence must
    read it before this step — which is why `evidence/extract.py` works off
    raw traces, not off what the screen shows.
    """
    data = _as_float(data)
    if mode == "none":
        return data

    n_samples = data.shape[0]
    if mode == "agc":
        window = max(3, int(agc_window_samples) | 1)
        envelope = np.abs(data)
        kernel = np.ones(window) / window
        padded = np.pad(envelope, ((window // 2, window // 2), (0, 0)), mode="edge")
        local = np.apply_along_axis(lambda col: np.convolve(col, kernel, mode="valid"), 0, padded)
        # Floor at a fraction of the global level, not at epsilon: dividing a
        # dead-quiet window by its own near-zero mean amplifies pure noise to
        # full scale and paints signal where there is none.
        floor = max(float(np.mean(envelope)) * 0.05, 1e-12)
        return data / np.maximum(local, floor)

    depth_fraction = np.arange(n_samples) / max(n_samples - 1, 1)
    if mode == "linear":
        curve = 1.0 + depth_fraction * float(exponent)
    elif mode == "exponential":
        curve = np.exp(depth_fraction * float(exponent))
    else:
        raise ValueError(f"unknown gain mode: {mode!r}")
    return data * curve[:, None]


def stack_traces(data: np.ndarray, count: int) -> np.ndarray:
    """Average every `count` adjacent traces — raises SNR, costs lateral resolution."""
    data = _as_float(data)
    count = int(count)
    if count <= 1:
        return data
    n_traces = data.shape[1]
    usable = (n_traces // count) * count
    if usable == 0:
        return data
    folded = data[:, :usable].reshape(data.shape[0], usable // count, count)
    stacked = folded.mean(axis=2)
    if usable == n_traces:
        return stacked
    remainder = data[:, usable:].mean(axis=1, keepdims=True)
    return np.hstack([stacked, remainder])


def migrate(
    data: np.ndarray,
    *,
    trace_spacing_m: float,
    sample_interval_ns: float,
    velocity_m_per_ns: float,
    aperture_traces: int,
) -> np.ndarray:
    """Kirchhoff (diffraction-stack) migration: collapse hyperbolas back to points.

    A buried point reflector is seen from every position the antenna can still
    hear it from, so it draws a hyperbola whose apex sits over the object.
    Migration sums each output sample along the diffraction curve it would have
    produced, so energy that really belongs to one point collects back at that
    point and everything else averages down.

    The whole result hinges on `velocity_m_per_ns` being right — migrate with a
    velocity that is too high and hyperbolas over-collapse into 'smiles', too
    low and they stay open. That sensitivity is a feature: it makes migration a
    velocity check as well as an image, and it is exactly why the velocity
    argument is required rather than defaulted here.
    """
    data = _as_float(data)
    if velocity_m_per_ns <= 0:
        raise ValueError(f"migration velocity must be positive, got {velocity_m_per_ns}")
    if sample_interval_ns <= 0 or trace_spacing_m <= 0:
        raise ValueError("migration needs positive trace spacing and sample interval")

    n_samples, n_traces = data.shape
    aperture = max(1, min(int(aperture_traces), n_traces - 1))
    times_ns = np.arange(n_samples) * sample_interval_ns

    migrated = np.zeros_like(data)
    contributions = np.zeros_like(data)

    for offset in range(-aperture, aperture + 1):
        lateral_m = abs(offset) * trace_spacing_m
        # Two-way traveltime to the same point from a trace `lateral_m` away.
        travel_ns = np.sqrt(times_ns**2 + (2.0 * lateral_m / velocity_m_per_ns) ** 2)
        source_rows = np.rint(travel_ns / sample_interval_ns).astype(np.int64)
        in_window = source_rows < n_samples
        if not in_window.any():
            # This offset is far enough away that its whole diffraction curve
            # falls past the end of the record. `continue`, not `break`: the
            # loop runs -aperture..+aperture, so the very first iteration is
            # the *furthest* one — breaking there would skip every offset.
            continue
        rows = source_rows[in_window]

        # Column ranges: output column x reads input column x + offset.
        out_lo, out_hi = max(0, -offset), min(n_traces, n_traces - offset)
        if out_lo >= out_hi:
            continue
        src_lo, src_hi = out_lo + offset, out_hi + offset

        # Obliquity weight: energy arriving at a steep angle contributes less.
        weight = (times_ns[in_window] / np.maximum(travel_ns[in_window], 1e-12))[:, None]
        migrated[np.ix_(np.flatnonzero(in_window), np.arange(out_lo, out_hi))] += (
            data[np.ix_(rows, np.arange(src_lo, src_hi))] * weight
        )
        contributions[np.ix_(np.flatnonzero(in_window), np.arange(out_lo, out_hi))] += weight

    return migrated / np.maximum(contributions, 1e-12)


def run_chain(
    raw: np.ndarray,
    chain: ProcessingChain,
    *,
    trace_spacing_m: float,
    sample_interval_ns: float,
) -> np.ndarray:
    """Run the full chain in the conventional order, raw traces in, radargram out.

    Order is not arbitrary and is not user-reorderable: time-zero before
    anything that reasons about depth; dewow before background removal so the
    DC drift doesn't pollute the average trace; filtering before gain so gain
    isn't amplifying out-of-band noise; migration on true relative amplitudes,
    i.e. before gain; stacking last, once every per-trace operation is done.
    """
    data = apply_time_zero(raw, chain.time_zero_sample)
    if chain.dewow:
        data = dewow(data, chain.dewow_window_samples)
    data = remove_background(data, chain.background_removal, chain.background_window_traces)
    if chain.bandpass:
        data = bandpass(data, sample_interval_ns, chain.bandpass_low_mhz, chain.bandpass_high_mhz)
    if chain.migrate:
        data = migrate(
            data,
            trace_spacing_m=trace_spacing_m,
            sample_interval_ns=sample_interval_ns,
            velocity_m_per_ns=chain.migration_velocity_m_per_ns,
            aperture_traces=chain.migration_aperture_traces,
        )
    data = apply_gain(data, chain.gain, chain.agc_window_samples, chain.gain_exponent)
    return stack_traces(data, chain.stack_traces)


def chain_from_params(params: dict) -> ProcessingChain:
    """Build a chain from an untrusted request payload, rejecting unknown keys.

    Silently ignoring a misspelled key would leave the operator looking at a
    radargram they did not ask for and have no way to tell apart from one they
    did — the display would be quietly lying about what was applied.
    """
    defaults = ProcessingChain()
    unknown = set(params) - set(vars(defaults))
    if unknown:
        raise ValueError(f"unknown processing parameter(s): {sorted(unknown)}")

    coerced: dict[str, Any] = {}
    for key, value in params.items():
        current = getattr(defaults, key)
        try:
            coerced[key] = _coerce_bool(value) if isinstance(current, bool) else type(current)(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid value for {key!r}: {value!r} ({exc})") from exc
    return replace(defaults, **coerced)


def _coerce_bool(value: object) -> bool:
    """Read a switch from a request payload.

    `bool("false")` is True in Python, so a naive cast would switch *on* every
    step an operator switched off the moment the value arrived as a string —
    which every query-string value does. Only unambiguous spellings are
    accepted; anything else is rejected rather than guessed at.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ValueError("expected true or false")
