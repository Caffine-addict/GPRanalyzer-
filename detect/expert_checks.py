"""Checks an experienced GPR interpreter makes on every line, as measurements.

Where these come from — the material locators are trained on (docs/EXPERT_INTERPRETATION.md):
the GSSI *Utility Locating with GPR* handbook (MN72-615), NULCA-accredited courses (Radiodetection,
Sensors & Software), ASTM D6432 and the PAS 128 / ASCE 38 survey standards. Each check here is
one of their routine rules, and each was checked against data before it was written down:

- **Read polarity at the apex.** An interpreter reads the wavelet at the top of the hyperbola,
  where the echo is flat, not across its sloping limbs. On simulated scenes with known
  materials this read metal 6/6, concrete 3/3 and voids 5/5 correctly, where averaging the whole
  box read metal 3/6 (3 unreadable) and concrete 0/3. On small, weak targets (thin plastic
  pipes, stones) it is still only ~60% right — the handbook calls empty PVC a weak reflector,
  and that is where polarity stops being trustworthy.
- **Know the usable depth.** Below a certain depth the reflections sink into noise and nothing
  there can be detected ("penetration depth" / noise floor). Measured per channel from the
  data: on the delivered lines the deep channel reaches noise at about 40% of its record.

Conventions: `traces` is (n_traces, n_samples), as the pipeline holds them. Polarity is relative
to the direct wave: kept = the wave sped up (air: a void or an empty duct); reversed = it
slowed down (water, concrete, metal). GSSI's handbook states the same rule against the ground
surface band, which is itself reversed, so its "positive = metal" is this "reversed".
"""

from __future__ import annotations

import numpy as np

from detect.measure import direct_wave_skip_samples

APEX_HALF_TRACES = 2  # read 5 traces at the apex, where the limbs are still flat
APEX_WINDOW_NS = 3.0  # about one wavelet at the shallow channel's 466 MHz
_COHERENT = 0.3  # a stacked echo weaker than this vs the window's peak is noise
_MAIN_LOBE = 0.5  # skip the wavelet's side lobes (~45% of the main lobe)
_ECHO_OVER_NOISE = 3.0  # an apex echo must stand this far above the trace-to-trace noise
NOISE_RATIO = 1.3  # signal within 30% of the noise level is not usable
_SMOOTH_ROWS = 9


def apex_polarity(traces: np.ndarray, apex_trace: float, top_sample: float,
                  sample_interval_ns: float) -> bool | None:
    """Does the echo at a hyperbola's apex keep the direct wave's polarity? None = no clear echo."""
    radargram = traces.T.astype(np.float64)
    if not np.isfinite(radargram).all():
        return None  # a dropout (NaN) must read as "unreadable", never as a confident polarity
    mean_trace = radargram.mean(axis=1)
    top = max(1, direct_wave_skip_samples(radargram.shape[0], sample_interval_ns))
    direct_sign = np.sign(mean_trace[int(np.argmax(np.abs(mean_trace[:top])))])
    centre = round(apex_trace)
    cols = slice(max(0, centre - APEX_HALF_TRACES), centre + APEX_HALF_TRACES + 1)
    start = max(0, int(top_sample))
    rows = slice(start, start + max(1, int(APEX_WINDOW_NS / sample_interval_ns)))
    background_removed = radargram - mean_trace[:, None]
    window = background_removed[rows, cols]
    if window.size == 0 or direct_sign == 0:
        return None
    stack = window.mean(axis=1)
    strength = float(np.abs(stack).max())
    # Noise from the whole record's trace-to-trace differences, robustly (median / 0.6745): read
    # inside the apex window, the echo's own curvature from trace to trace counted as noise and
    # strong metal echoes were rejected (simulation: metal 3/6 unreadable).
    differences = np.diff(background_removed, axis=1)
    noise = float(np.median(np.abs(differences))) / 0.6745 / np.sqrt(2.0) if differences.size else 0.0
    if strength == 0 or strength < _COHERENT * float(np.abs(window).max()) or strength < _ECHO_OVER_NOISE * noise:
        return None
    first = int(np.argmax(np.abs(stack) >= _MAIN_LOBE * strength))
    return bool(np.sign(stack[first]) == direct_sign)


def signal_to_noise_by_sample(traces: np.ndarray) -> np.ndarray:
    """Background-removed signal against incoherent noise, per sample row.

    Coherent reflections are alike from one trace to the next; noise is not, so the difference
    between neighbouring traces (scaled by 1/sqrt 2) measures the noise at each depth.
    """
    radargram = traces.T.astype(np.float64)
    background_removed = radargram - radargram.mean(axis=1, keepdims=True)
    signal = np.abs(background_removed).mean(axis=1)
    noise = np.abs(np.diff(background_removed, axis=1)).mean(axis=1) / np.sqrt(2.0)
    ratio = signal / np.maximum(noise, 1e-12)
    kernel = np.ones(_SMOOTH_ROWS) / _SMOOTH_ROWS
    return np.convolve(ratio, kernel, mode="same")


def penetration_sample(traces: np.ndarray, sample_interval_ns: float) -> int | None:
    """First sample below the direct wave from which the record stays at noise level.

    None when usable signal reaches the bottom of the record: the penetration is deeper than
    the time window, and the window, not the ground, is the limit.
    """
    ratio = signal_to_noise_by_sample(traces)
    start = direct_wave_skip_samples(len(ratio), sample_interval_ns)
    usable = ratio >= NOISE_RATIO
    usable[:start] = True
    above = np.flatnonzero(usable)
    last = int(above[-1]) if above.size else 0
    end_margin = len(ratio) - _SMOOTH_ROWS // 2 - 1  # the smoothing tapers off at the record's end
    return None if last >= end_margin else last + 1
