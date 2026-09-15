"""Tests for studio/processing.py — the radargram processing chain."""

from __future__ import annotations

import numpy as np
import pytest

from studio.processing import (
    ProcessingChain,
    apply_gain,
    apply_time_zero,
    bandpass,
    chain_from_params,
    dewow,
    migrate,
    remove_background,
    run_chain,
    stack_traces,
)


def _radargram(n_samples: int = 64, n_traces: int = 40, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).normal(size=(n_samples, n_traces))


def test_every_step_returns_a_new_array_and_leaves_its_input_alone() -> None:
    # The whole chain is re-run from raw on every request; a step that mutated
    # in place would make the second run see different input from the first.
    data = _radargram()
    original = data.copy()
    for output in (
        apply_time_zero(data, 3),
        dewow(data, 9),
        remove_background(data, "mean", 5),
        bandpass(data, 0.1, 100, 1200),
        apply_gain(data, "agc", 9, 2.0),
        stack_traces(data, 2),
    ):
        assert output is not data
    assert np.array_equal(data, original)


def test_time_zero_drops_rows_rather_than_zero_filling() -> None:
    # A zero-filled row is a fabricated measurement at a depth nothing recorded.
    data = _radargram(n_samples=20)
    out = apply_time_zero(data, 5)
    assert out.shape == (15, data.shape[1])
    assert np.array_equal(out, data[5:])


def test_time_zero_of_zero_is_a_no_op() -> None:
    data = _radargram()
    assert np.array_equal(apply_time_zero(data, 0), data)


def test_time_zero_past_the_end_raises() -> None:
    with pytest.raises(ValueError, match="past the end"):
        apply_time_zero(_radargram(n_samples=10), 10)


def test_dewow_removes_a_dc_offset() -> None:
    data = np.ones((40, 5)) * 7.0
    assert np.allclose(dewow(data, 11), 0.0, atol=1e-9)


def test_dewow_keeps_a_fast_oscillation() -> None:
    # Wow is the slow drift under the wavelet; the wavelet itself must survive.
    fast = np.tile(np.sin(np.arange(64) * 1.5)[:, None], (1, 4))
    out = dewow(fast, 9)
    assert out.std() > fast.std() * 0.5


def test_background_mean_removes_a_flat_reflector_shared_by_every_trace() -> None:
    data = _radargram(seed=2)
    banded = data + np.linspace(0, 5, data.shape[0])[:, None]
    out = remove_background(banded, "mean", 5)
    assert np.allclose(out.mean(axis=1), 0.0, atol=1e-9)


def test_background_moving_window_keeps_a_locally_isolated_feature() -> None:
    data = np.zeros((30, 60))
    data[10, 30] = 100.0  # one bright sample, present in only one trace
    out = remove_background(data, "moving", 11)
    assert out[10, 30] > 50.0


def test_background_none_is_a_no_op() -> None:
    data = _radargram()
    assert np.array_equal(remove_background(data, "none", 5), data)


def test_background_rejects_an_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unknown background_removal"):
        remove_background(_radargram(), "subtract-everything", 5)


def test_bandpass_attenuates_out_of_band_energy() -> None:
    n_samples, sample_interval_ns = 256, 0.1
    times = np.arange(n_samples) * sample_interval_ns
    # 4000 MHz sits well above the 1200 MHz high cut.
    high = np.tile(np.sin(2 * np.pi * 4.0 * times)[:, None], (1, 3))
    out = bandpass(high, sample_interval_ns, 100, 1200)
    assert np.abs(out).max() < np.abs(high).max() * 0.1


def test_bandpass_passes_in_band_energy_largely_intact() -> None:
    n_samples, sample_interval_ns = 256, 0.1
    times = np.arange(n_samples) * sample_interval_ns
    in_band = np.tile(np.sin(2 * np.pi * 0.6 * times)[:, None], (1, 3))  # 600 MHz
    out = bandpass(in_band, sample_interval_ns, 100, 1200)
    assert np.abs(out).max() > np.abs(in_band).max() * 0.8


def test_bandpass_rejects_inverted_corners() -> None:
    with pytest.raises(ValueError, match="must be below"):
        bandpass(_radargram(), 0.1, 1200, 100)


def test_bandpass_rejects_a_non_positive_sample_interval() -> None:
    with pytest.raises(ValueError, match="sample_interval_ns must be positive"):
        bandpass(_radargram(), 0.0, 100, 1200)


def test_agc_lifts_a_decayed_deep_reflector_to_match_a_shallow_one() -> None:
    n_samples = 128
    signal = np.sin(np.arange(n_samples) * 1.2)[:, None] * np.exp(-np.arange(n_samples) / 20)[:, None]
    out = apply_gain(np.tile(signal, (1, 4)), "agc", 11, 2.0)
    shallow = np.abs(out[10:30]).mean()
    deep = np.abs(out[90:110]).mean()
    assert deep > shallow * 0.4


def test_agc_does_not_amplify_a_dead_window_to_full_scale() -> None:
    # A window of pure silence divided by its own near-zero mean would paint
    # signal where there is none; the floor exists to stop exactly that.
    data = np.zeros((80, 4))
    data[:20] = np.random.default_rng(3).normal(size=(20, 4)) * 100
    out = apply_gain(data, "agc", 9, 2.0)
    assert np.abs(out[40:]).max() < 1e-6


def test_exponential_gain_grows_with_depth() -> None:
    data = np.ones((50, 3))
    out = apply_gain(data, "exponential", 9, 2.0)
    assert out[-1, 0] > out[0, 0] * 5


def test_gain_none_is_a_no_op() -> None:
    data = _radargram()
    assert np.array_equal(apply_gain(data, "none", 9, 2.0), data)


def test_gain_rejects_an_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unknown gain mode"):
        apply_gain(_radargram(), "turbo", 9, 2.0)


def test_stacking_averages_adjacent_traces() -> None:
    data = np.tile(np.arange(8, dtype=float), (5, 1))
    out = stack_traces(data, 2)
    assert out.shape == (5, 4)
    assert np.allclose(out[0], [0.5, 2.5, 4.5, 6.5])


def test_stacking_keeps_a_partial_final_group_rather_than_discarding_traces() -> None:
    out = stack_traces(np.ones((4, 7)), 2)
    assert out.shape == (4, 4)  # three full pairs plus the leftover trace


def test_stacking_by_one_is_a_no_op() -> None:
    data = _radargram()
    assert np.array_equal(stack_traces(data, 1), data)


def test_migration_collapses_a_synthetic_hyperbola_towards_its_apex() -> None:
    # The defining behaviour: energy spread along a diffraction curve should
    # gather back to the point that produced it.
    n_samples, n_traces = 200, 121
    trace_spacing_m, sample_interval_ns, velocity = 0.025, 0.1, 0.1
    apex_trace, apex_time_ns = 60, 6.0

    data = np.zeros((n_samples, n_traces))
    for trace in range(n_traces):
        lateral = abs(trace - apex_trace) * trace_spacing_m
        travel = np.sqrt(apex_time_ns**2 + (2 * lateral / velocity) ** 2)
        row = round(travel / sample_interval_ns)
        if row < n_samples:
            data[row, trace] = 1.0

    out = migrate(
        data,
        trace_spacing_m=trace_spacing_m,
        sample_interval_ns=sample_interval_ns,
        velocity_m_per_ns=velocity,
        aperture_traces=60,
    )
    apex_row = round(apex_time_ns / sample_interval_ns)
    apex_energy = np.abs(out[apex_row - 2 : apex_row + 3, apex_trace - 2 : apex_trace + 3]).sum()
    limb_energy = np.abs(out[apex_row - 2 : apex_row + 3, :20]).sum()
    assert apex_energy > limb_energy * 5


def test_migration_rejects_a_non_positive_velocity() -> None:
    with pytest.raises(ValueError, match="velocity must be positive"):
        migrate(
            _radargram(),
            trace_spacing_m=0.025,
            sample_interval_ns=0.1,
            velocity_m_per_ns=0.0,
            aperture_traces=10,
        )


def test_run_chain_with_everything_off_only_reorders_nothing() -> None:
    data = _radargram()
    out = run_chain(data, ProcessingChain(), trace_spacing_m=0.025, sample_interval_ns=0.1)
    assert np.array_equal(out, data)


def test_run_chain_with_every_step_on_stays_finite() -> None:
    chain = ProcessingChain(
        time_zero_sample=4,
        dewow=True,
        background_removal="moving",
        bandpass=True,
        migrate=True,
        gain="agc",
        stack_traces=2,
    )
    out = run_chain(_radargram(n_samples=128, n_traces=60), chain, trace_spacing_m=0.025, sample_interval_ns=0.1)
    assert np.isfinite(out).all()
    assert out.shape == (124, 30)


def test_processing_rejects_non_finite_input() -> None:
    data = _radargram()
    data[3, 3] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        dewow(data, 9)


def test_chain_from_params_rejects_an_unknown_key() -> None:
    # Silently ignoring a misspelled key would leave the operator looking at a
    # radargram they did not ask for, indistinguishable from one they did.
    with pytest.raises(ValueError, match="unknown processing parameter"):
        chain_from_params({"gian": "agc"})


def test_chain_from_params_coerces_strings_to_the_declared_types() -> None:
    chain = chain_from_params({"time_zero_sample": "7", "migration_velocity_m_per_ns": "0.12"})
    assert chain.time_zero_sample == 7
    assert chain.migration_velocity_m_per_ns == pytest.approx(0.12)


def test_chain_from_params_rejects_junk_values() -> None:
    with pytest.raises(ValueError, match="invalid value"):
        chain_from_params({"time_zero_sample": "not-a-number"})


def test_chain_from_params_reads_string_switches_correctly() -> None:
    # bool("false") is True in Python — a naive cast would turn every step the
    # operator switched off back on the moment it arrived via a query string.
    chain = chain_from_params({"dewow": "false", "migrate": "TRUE"})
    assert chain.dewow is False
    assert chain.migrate is True


def test_chain_from_params_rejects_an_ambiguous_switch_value() -> None:
    with pytest.raises(ValueError, match="expected true or false"):
        chain_from_params({"dewow": "yes"})
