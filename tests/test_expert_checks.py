"""Tests for detect/expert_checks.py — reading polarity at the apex, and the usable depth."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from detect import expert_checks

DT = 0.1  # ns per sample, the shallow channel's


def wavelet(n: int, at: int, sign: float, width: float = 3.0) -> np.ndarray:
    t = np.arange(n) - at
    return sign * (1 - (t / width) ** 2) * np.exp(-0.5 * (t / width) ** 2)  # Ricker-like, main lobe sign


def scene(echo_sign: float, n_traces: int = 60, n_samples: int = 256, apex: int = 30, top: int = 120,
          amp: float = 0.4, noise: float = 0.0, seed: int = 0) -> np.ndarray:
    """A direct wave of positive polarity in every trace, and one hyperbola apexing at `apex`."""
    traces = np.zeros((n_traces, n_samples))
    for i in range(n_traces):
        traces[i] += wavelet(n_samples, 20, 1.0)
        traces[i] += wavelet(n_samples, int(top + 0.02 * (i - apex) ** 2), amp * echo_sign)
    if noise:
        traces = traces + np.random.default_rng(seed).normal(0, noise, traces.shape)
    return traces


@pytest.mark.parametrize(("sign", "expected"), [(1.0, True), (-1.0, False)])
def test_apex_polarity_reads_the_echo_against_the_direct_wave(sign: float, expected: bool) -> None:
    assert expert_checks.apex_polarity(scene(sign), apex_trace=30, top_sample=114, sample_interval_ns=DT) is expected


def test_apex_polarity_with_no_echo_says_so() -> None:
    rng = np.random.default_rng(0)
    traces = np.array([wavelet(256, 20, 1.0) for _ in range(60)]) + rng.normal(0, 0.01, (60, 256))
    assert expert_checks.apex_polarity(traces, apex_trace=30, top_sample=114, sample_interval_ns=DT) is None


def test_apex_polarity_rejects_an_incoherent_echo_even_if_one_trace_is_loud() -> None:
    # Same sign at the direct wave, but the "echo" flips sign at random from trace to trace: it
    # never stacks into a real reflection, it only looks that way on any single noisy trace.
    rng = np.random.default_rng(5)
    n_traces, n_samples = 60, 256
    traces = np.zeros((n_traces, n_samples))
    signs = rng.choice([-1.0, 1.0], n_traces)
    for i in range(n_traces):
        traces[i] += wavelet(n_samples, 20, 1.0)
        traces[i] += wavelet(n_samples, 114, 0.4 * signs[i])
    assert expert_checks.apex_polarity(traces, apex_trace=30, top_sample=114, sample_interval_ns=DT) is None


def test_apex_polarity_needs_the_echo_well_above_the_trace_to_trace_noise() -> None:
    # Same echo shape and noise level; only the echo's own amplitude differs. A real echo this
    # weak against this noise floor is not trustworthy and must read None, not a guessed sign.
    strong = scene(1.0, amp=0.4, noise=0.03, seed=7)
    weak = scene(1.0, amp=0.03, noise=0.03, seed=7)
    assert expert_checks.apex_polarity(strong, apex_trace=30, top_sample=114, sample_interval_ns=DT) is True
    assert expert_checks.apex_polarity(weak, apex_trace=30, top_sample=114, sample_interval_ns=DT) is None


def test_apex_polarity_is_none_when_the_window_falls_outside_the_record() -> None:
    traces = np.array([wavelet(256, 20, 1.0) for _ in range(60)])
    assert expert_checks.apex_polarity(traces, apex_trace=30, top_sample=1000, sample_interval_ns=DT) is None


def test_apex_polarity_is_none_when_the_direct_wave_window_is_exactly_zero() -> None:
    # The direct-wave window (the first ~31 samples at this sample rate) is flat zero, so there
    # is no direct-wave sign to read the echo against — not "same", not "reversed", unreadable.
    traces = scene(1.0, amp=0.4)
    traces[:, :40] = 0.0
    assert expert_checks.apex_polarity(traces, apex_trace=30, top_sample=114, sample_interval_ns=DT) is None


def test_usable_depth_is_where_the_signal_sinks_into_noise() -> None:
    rng = np.random.default_rng(1)
    n_traces, n_samples = 200, 256
    x = np.arange(n_traces)[:, None]
    t = np.arange(n_samples)[None, :]
    # Lateral structure (so background removal leaves it) that decays to nothing by sample 140.
    signal = np.sin(x / 7.0) * np.sin(t / 3.0) * np.exp(-t / 30.0) * (t < 140)
    traces = signal + rng.normal(0, 0.002, (n_traces, n_samples))
    cut = expert_checks.penetration_sample(traces, DT)
    assert cut is not None and 135 <= cut <= 150  # the signal stops at sample 140


def test_penetration_sample_is_exactly_the_direct_wave_skip_when_nothing_else_is_usable() -> None:
    # Incoherent jitter inside the direct-wave window (forced usable regardless of its own
    # ratio), flat zero after it (no signal anywhere past the direct wave). The cut must land
    # exactly at the end of the direct-wave skip, not one sample short or at the record start.
    rng = np.random.default_rng(3)
    n_traces, n_samples = 60, 200
    traces = np.zeros((n_traces, n_samples))
    traces[:, :31] = rng.normal(0, 1.0, (n_traces, 31))
    assert expert_checks.penetration_sample(traces, DT) == 31


def test_signal_to_the_end_of_the_record_means_the_window_is_the_limit() -> None:
    rng = np.random.default_rng(2)
    x = np.arange(200)[:, None]
    t = np.arange(256)[None, :]
    traces = np.sin(x / 7.0) * np.sin(t / 3.0) + rng.normal(0, 0.01, (200, 256))
    assert expert_checks.penetration_sample(traces, DT) is None


def test_signal_to_noise_by_sample_scales_noise_by_root_two() -> None:
    # Pure iid noise, no signal: background-removed std is sigma, and the row-to-row difference
    # of two independent draws has std sigma*sqrt(2) — dividing by sqrt(2) is what makes the
    # ratio read ~1 here. Skipping that division reads noise as ~1.41x too small, so the ratio
    # falsely looks like usable signal everywhere.
    rng = np.random.default_rng(11)
    traces = rng.normal(0, 1.0, (300, 64))
    ratio = expert_checks.signal_to_noise_by_sample(traces)
    assert 0.9 < float(np.median(ratio)) < 1.1


_DATA = Path("Dataset/DSU_GPR_Files/Job_0696")


@pytest.mark.skipif(not _DATA.exists(), reason="SPR dataset not present")
def test_on_a_real_line_the_deep_channel_reaches_noise_and_the_shallow_one_does_not() -> None:
    from studio import session

    deep = session.raw_traces(session.load_frame(_DATA, "RA2"))
    shallow = session.raw_traces(session.load_frame(_DATA, "RAD"))
    deep_cut = expert_checks.penetration_sample(deep, 0.4)
    assert deep_cut is not None and deep_cut < 0.6 * deep.shape[1]
    assert expert_checks.penetration_sample(shallow, 0.1) is None


def test_a_dropout_reads_as_unreadable_never_as_a_polarity() -> None:
    traces = scene(1.0)
    traces[31, 100:110] = np.nan
    assert expert_checks.apex_polarity(traces, apex_trace=30, top_sample=114, sample_interval_ns=DT) is None
