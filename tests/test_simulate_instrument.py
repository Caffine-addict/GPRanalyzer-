"""Tests for simulate/instrument.py — including re-measuring its constants from the real lines.

The constants are only worth anything if they describe this radar. These tests measure
them again from Dataset/DSU_GPR_Files the same way they were measured the first time,
so a change to the parser, the dataset, or the constants that pulls them apart fails
here instead of quietly training the detector on some other instrument.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from simulate import instrument
from studio import session

_DATASET = Path("Dataset/DSU_GPR_Files")
needs_dataset = pytest.mark.skipif(not _DATASET.exists(), reason="SPR dataset not present")


def _real_rad_lines() -> list[np.ndarray]:
    """Every real RAD line as (n_samples, n_traces)."""
    return [session.load_radargram(session.resolve_job(j, _DATASET), "RAD")[0].astype(float) for j in session.list_jobs(_DATASET)]


def test_depth_profile_passes_through_its_knots() -> None:
    profile = instrument.depth_profile()
    for sample, value in instrument.DEPTH_PROFILE_KNOTS:
        assert profile[sample] == pytest.approx(value, rel=1e-9)


def test_depth_profile_fades_with_depth_below_the_direct_wave() -> None:
    profile = instrument.depth_profile()
    assert profile[40] > profile[80] > profile[160] > profile[250]


def test_depth_profile_has_one_value_per_sample() -> None:
    assert instrument.depth_profile().shape == (instrument.SAMPLES_PER_TRACE,)
    assert instrument.depth_profile(100).shape == (100,)


@needs_dataset
def test_line_geometry_matches_the_real_headers() -> None:
    for job in session.list_jobs(_DATASET):
        info = session.describe_channel(session.load_frame(session.resolve_job(job, _DATASET), "RAD"), "RAD")
        assert info.trace_spacing_m == instrument.TRACE_SPACING_M
        assert info.sample_interval_ns == instrument.SAMPLE_INTERVAL_NS
        assert info.n_samples == instrument.SAMPLES_PER_TRACE
        assert abs(info.n_traces - instrument.TRACES_PER_FRAME) <= 12  # real lines: 381-393


@needs_dataset
def test_direct_wave_peak_sample_matches_the_real_lines() -> None:
    for radargram in _real_rad_lines():
        peak = int(np.argmax(np.abs(radargram.mean(axis=1))))
        assert abs(peak - instrument.DIRECT_WAVE_PEAK_SAMPLE) <= 3


@needs_dataset
def test_centre_frequency_matches_the_real_spectrum() -> None:
    for radargram in _real_rad_lines():
        residual = radargram - radargram.mean(axis=1, keepdims=True)
        skip = int(0.08 * residual.shape[0])
        spectrum = np.abs(np.fft.rfft(residual[skip:], axis=0)).mean(axis=1)
        freqs_mhz = np.fft.rfftfreq(residual.shape[0] - skip, d=instrument.SAMPLE_INTERVAL_NS * 1e-9) / 1e6
        spectrum[freqs_mhz < 50] = 0
        assert freqs_mhz[np.argmax(spectrum)] == pytest.approx(instrument.CENTRE_FREQUENCY_MHZ, rel=0.10)


@needs_dataset
def test_noise_ratio_matches_the_real_lines() -> None:
    for radargram in _real_rad_lines():
        residual = radargram - radargram.mean(axis=1, keepdims=True)
        ratio = residual[-40:].std() / np.abs(residual[20:120]).max()
        assert instrument.NOISE_TO_PEAK_SCATTER / 2 <= ratio <= instrument.NOISE_TO_PEAK_SCATTER * 2


@needs_dataset
def test_depth_profile_matches_the_real_lines() -> None:
    profiles = []
    for radargram in _real_rad_lines():
        residual = radargram - radargram.mean(axis=1, keepdims=True)
        rms = np.sqrt((residual**2).mean(axis=1))
        profiles.append(rms / rms[20:60].max())
    median = np.median(np.stack(profiles), axis=0)
    for sample, value in instrument.DEPTH_PROFILE_KNOTS:
        assert median[sample] == pytest.approx(value, rel=0.05)


@needs_dataset
def test_direct_to_echo_ratio_matches_the_real_lines() -> None:
    ratios = []
    for radargram in _real_rad_lines():
        mean_trace = radargram.mean(axis=1)
        ratios.append(np.abs(mean_trace).max() / np.abs(radargram - mean_trace[:, None]).max())
    assert float(np.median(ratios)) == pytest.approx(instrument.DIRECT_TO_PEAK_ECHO, rel=0.05)
