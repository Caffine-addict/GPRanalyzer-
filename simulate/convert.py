"""gprMax output -> traces that look like what the SPRScan actually records.

Three steps, each matching a measured property of the real instrument
(`simulate/instrument.py`):

1. **Resample** onto the instrument's time grid — 256 samples at 0.1 ns, with the direct
   wave peaking at sample 20 exactly as it does on the real lines.
2. **Gain** so brightness fades with depth the way the real lines do: one exponential
   time gain for the whole synthetic set (`derive_time_gain`), like an instrument's own —
   not a per-frame adjustment, which would flatten away how strong each target really is.
   It is held at unity over the direct wave, whose simulated strength against the echoes
   already matches the real lines.
3. **Noise** at the measured noise-to-signal ratio.

The scattered field (scene minus background) is carried alongside the total. Labels are
measured on it (`simulate/labels.py`), since it is the only place an object's footprint
shows without the direct wave and the layering in the way.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from simulate import instrument

_GAIN_LIMIT = 50.0  # no depth gets more than 50x up or down: beyond that it is shaping noise
_SIGNAL_REFERENCE_ROWS = slice(20, 120)  # the window the real noise ratio was measured against
_VALID_ROW_FRACTION = 0.05  # rows under 5% of the typical peak carry no depth trend to fit
_ECHO_FLOOR = 1e-6  # a scattered field this far below the total is numerical noise: nothing buried
# Below this many frames with echoes the depth gain is not estimated at all. The gain is
# an instrument property, but a median over a handful of sparse synthetic scenes mostly
# reflects those scenes: on the smoke batch, adding one scene to two moved the fitted gain
# at the bottom of the record from 0.02 to 0.29 (15x), and every label box with it. With
# too few frames they are left as simulated — reproducible, and the raw simulation
# already matches the real direct-to-echo ratio.
_MIN_GAIN_FRAMES = 20

# The direct wave peaks at sample 20 and rings for about one period (~21 samples at
# 466 MHz), so echoes start below here. Above this row a synthetic frame's background-
# removed signal is empty by construction — its direct wave is identical in every trace
# and cancels exactly, while on the real lines ground coupling that changes along the line
# leaves it in. Matching the depth profile there divided by almost nothing, boosted the
# direct wave 50x, and buried every echo in the rendered image.
ECHO_ZONE_START = instrument.DIRECT_WAVE_PEAK_SAMPLE + 20


class SimulationOutputError(ValueError):
    """A gprMax output file is missing, malformed, or does not cover the instrument's record."""


@dataclass(frozen=True)
class SimulatedFrame:
    """One scene on the instrument's time grid; both arrays are (n_samples, n_traces)."""

    total: np.ndarray  # what the antenna would record
    scattered: np.ndarray  # total minus the object-free background: the buried objects alone


def read_receiver(path: Path) -> tuple[np.ndarray, float]:
    """Ez at the receiver as (iterations, n_traces), plus the solver's time step in seconds."""
    try:
        import h5py  # optional: only synthetic-dataset building needs it (pip extra "sim")
    except ImportError as exc:
        raise ImportError("reading gprMax output needs h5py — install the 'sim' extra") from exc

    if not path.exists():
        raise FileNotFoundError(f"gprMax output not found: {path}")
    try:
        handle = h5py.File(path, "r")
    except OSError as exc:  # h5py's answer to a truncated or non-HDF5 file
        raise SimulationOutputError(f"{path} is not a readable HDF5 file: {exc}") from exc
    with handle:
        try:
            dt_s = float(handle.attrs["dt"])
            field = np.asarray(handle["rxs/rx1/Ez"], dtype=np.float64)
        except KeyError as exc:
            raise SimulationOutputError(f"{path} is not a gprMax output file (missing {exc})") from exc

    if field.ndim == 1:  # a single run writes one trace, not a (iterations, runs) matrix
        field = field[:, None]
    if field.ndim != 2 or dt_s <= 0:
        raise SimulationOutputError(f"{path}: unexpected field shape {field.shape} or time step {dt_s}")
    if not np.isfinite(field).all():
        raise SimulationOutputError(f"{path}: non-finite field values — the simulation went unstable")
    return field, dt_s


def instrument_times_s(direct_wave_peak_s: float) -> np.ndarray:
    """Sample times of the instrument's record, anchored so the direct wave lands on sample 20."""
    offsets = np.arange(instrument.SAMPLES_PER_TRACE) - instrument.DIRECT_WAVE_PEAK_SAMPLE
    return direct_wave_peak_s + offsets * instrument.SAMPLE_INTERVAL_NS * 1e-9


def resample(field: np.ndarray, dt_s: float, times_s: np.ndarray) -> np.ndarray:
    """Interpolate each trace onto `times_s`, returning (len(times_s), n_traces).

    Linear interpolation is enough: the solver steps every ~14 ps against the
    instrument's 100 ps, and the pulse carries almost nothing above ~1.5 GHz, so the
    simulated field is heavily oversampled already.
    """
    sim_times = np.arange(field.shape[0]) * dt_s
    if times_s[0] < 0 or times_s[-1] > sim_times[-1]:
        raise SimulationOutputError(
            f"simulated window {sim_times[-1] * 1e9:.2f} ns does not cover the instrument record "
            f"({times_s[0] * 1e9:.2f}-{times_s[-1] * 1e9:.2f} ns)"
        )
    return np.stack([np.interp(times_s, sim_times, field[:, trace]) for trace in range(field.shape[1])], axis=1)


def simulated_frame(scene_field: np.ndarray, background_field: np.ndarray, dt_s: float) -> SimulatedFrame:
    """Put a simulated B-scan and its background on the instrument's time grid."""
    if scene_field.shape[1] != instrument.TRACES_PER_FRAME:
        raise SimulationOutputError(
            f"expected {instrument.TRACES_PER_FRAME} traces, got {scene_field.shape[1]} — was the B-scan merged?"
        )
    background = background_field[:, 0]
    # The strongest arrival in an object-free trace is the direct (air + ground) wave.
    direct_wave_peak_s = float(np.argmax(np.abs(background))) * dt_s
    times = instrument_times_s(direct_wave_peak_s)
    total = resample(scene_field, dt_s, times)
    background_trace = resample(background_field[:, :1], dt_s, times)
    return SimulatedFrame(total=total, scattered=total - background_trace)


def _rms_profile(total: np.ndarray) -> np.ndarray:
    """RMS of the background-removed signal at each sample — measured as on the real lines."""
    residual = total - total.mean(axis=1, keepdims=True)
    return np.sqrt((residual**2).mean(axis=1))


def derive_time_gain(profiles: list[np.ndarray]) -> np.ndarray:
    """An exponential time gain that makes the typical synthetic frame fade like the real lines.

    Fitted, not matched row by row. Synthetic ground is clean and sparse and real ground is
    cluttered everywhere, so a row-by-row ratio would chase that difference and boost
    empty rows into noise. A straight line in log-gain against time — the form an
    instrument's own exponential gain takes — captures how fast brightness falls with
    depth and nothing more. Fitted over the echo zone only and held at unity above it.
    """
    if not profiles:
        raise ValueError("need at least one simulated frame to derive a gain from")
    n_samples = len(profiles[0])
    unity = np.ones(n_samples)
    rows = np.arange(ECHO_ZONE_START, n_samples)
    # Each frame scaled to its own peak, so a bright scene doesn't outvote a dim one.
    normalised = [profile[rows] / profile[rows].max() for profile in profiles if profile[rows].max() > 0]
    if not normalised:
        return unity
    typical = np.median(np.stack(normalised), axis=0)
    valid = typical > _VALID_ROW_FRACTION * typical.max()
    if valid.sum() < 2:
        return unity  # nothing to fit a trend to: leave the frames as simulated
    target = instrument.depth_profile(n_samples)[rows]
    slope, _intercept = np.polyfit(rows[valid], np.log(target[valid]) - np.log(typical[valid]), 1)
    log_gain = slope * (np.arange(n_samples) - ECHO_ZONE_START)
    log_gain[:ECHO_ZONE_START] = 0.0  # unity over the direct wave
    limit = np.log(_GAIN_LIMIT)
    return np.exp(np.clip(log_gain, -limit, limit))


def has_echoes(frame: SimulatedFrame) -> bool:
    """Whether anything buried scattered back. An empty scene's scattered field is zero."""
    return float(np.abs(frame.scattered).max()) > _ECHO_FLOOR * float(np.abs(frame.total).max())


def time_gain_for(frames: list[SimulatedFrame]) -> tuple[np.ndarray, int]:
    """The set's time gain, and how many frames it was derived from (0 when not fitted).

    Frames with nothing buried carry no depth trend, so they are left out rather than fed in
    as a flat-zero profile. Fewer than `_MIN_GAIN_FRAMES` usable frames and the gain is
    unity: see that constant for why.
    """
    if not frames:
        raise ValueError("need at least one simulated frame to derive a gain from")
    usable = [frame for frame in frames if has_echoes(frame)]
    if len(usable) < _MIN_GAIN_FRAMES:
        return np.ones(frames[0].total.shape[0]), 0
    return derive_time_gain([_rms_profile(frame.total) for frame in usable]), len(usable)


def apply_gain(frame: SimulatedFrame, gain: np.ndarray) -> SimulatedFrame:
    if gain.shape != (frame.total.shape[0],):
        raise ValueError(f"gain has shape {gain.shape}, frame has {frame.total.shape[0]} samples")
    return SimulatedFrame(total=frame.total * gain[:, None], scattered=frame.scattered * gain[:, None])


def noise_sigma(total: np.ndarray, level_scale: float = 1.0) -> float:
    """Noise standard deviation at the measured ratio to the strongest early reflection."""
    residual = total - total.mean(axis=1, keepdims=True)
    reference = float(np.abs(residual[_SIGNAL_REFERENCE_ROWS]).max())
    total_peak = float(np.abs(total).max())
    # Relative, not `== 0`: identical traces minus their mean leave ~1e-17 of rounding.
    if reference <= _ECHO_FLOOR * total_peak:
        # Nothing buried and nothing to scale by: infer the echo level from the direct wave
        # at the measured direct-to-echo ratio. Otherwise an empty frame gets no noise at
        # all, and "perfectly clean" becomes the detector's cue for "nothing here".
        reference = total_peak / instrument.DIRECT_TO_PEAK_ECHO
    return instrument.NOISE_TO_PEAK_SCATTER * reference * level_scale


def add_noise(total: np.ndarray, sigma: float, rng: np.random.Generator) -> np.ndarray:
    if sigma < 0:
        raise ValueError(f"noise sigma cannot be negative, got {sigma}")
    return total + rng.normal(0.0, sigma, size=total.shape)
