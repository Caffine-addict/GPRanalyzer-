"""Tests for simulate/convert.py — gprMax output onto the instrument's time grid."""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from simulate import convert, instrument

DT_S = 14e-12
ITERATIONS = 2000  # 28 ns: covers a pulse peaking at 3 ns plus the 23.6 ns record


def _write_output(path: Path, field: np.ndarray, dt_s: float = DT_S) -> Path:
    with h5py.File(path, "w") as handle:
        handle.attrs["dt"] = dt_s
        handle.create_dataset("rxs/rx1/Ez", data=field)
    return path


def _pulse(peak_s: float, width_s: float = 0.3e-9) -> np.ndarray:
    t = np.arange(ITERATIONS) * DT_S
    return np.exp(-(((t - peak_s) / width_s) ** 2))


def _synthetic_pair(echo_peak_s: float = 10e-9) -> tuple[np.ndarray, np.ndarray]:
    background = _pulse(3e-9)[:, None]
    scene = np.tile(background, (1, instrument.TRACES_PER_FRAME)) + 0.2 * _pulse(echo_peak_s)[:, None]
    return scene, background


def test_read_receiver_returns_the_field_and_time_step(tmp_path: Path) -> None:
    field = np.random.default_rng(0).normal(size=(50, 3))
    read, dt_s = convert.read_receiver(_write_output(tmp_path / "a.out", field))
    assert dt_s == DT_S
    np.testing.assert_array_equal(read, field)


def test_a_single_run_output_reads_as_one_column(tmp_path: Path) -> None:
    read, _ = convert.read_receiver(_write_output(tmp_path / "b.out", np.arange(10.0)))
    assert read.shape == (10, 1)


def test_a_file_without_a_receiver_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.out"
    with h5py.File(path, "w") as handle:
        handle.attrs["dt"] = DT_S
    with pytest.raises(convert.SimulationOutputError, match="not a gprMax output"):
        convert.read_receiver(path)


def test_an_unstable_simulation_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(convert.SimulationOutputError, match="unstable"):
        convert.read_receiver(_write_output(tmp_path / "d.out", np.array([[1.0], [np.inf]])))


def test_a_missing_output_is_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        convert.read_receiver(tmp_path / "nothing.out")


def test_instrument_times_put_the_direct_wave_on_sample_twenty() -> None:
    times = convert.instrument_times_s(5e-9)
    assert times[instrument.DIRECT_WAVE_PEAK_SAMPLE] == pytest.approx(5e-9)
    assert np.diff(times) == pytest.approx(instrument.SAMPLE_INTERVAL_NS * 1e-9)


def test_resampling_an_oversampled_sine_is_accurate() -> None:
    t = np.arange(ITERATIONS) * DT_S
    field = np.sin(2 * np.pi * 466e6 * t)[:, None]
    targets = np.linspace(2e-9, 20e-9, 50)
    np.testing.assert_allclose(convert.resample(field, DT_S, targets)[:, 0], np.sin(2 * np.pi * 466e6 * targets), atol=2e-3)


def test_resampling_past_the_simulated_window_is_refused() -> None:
    with pytest.raises(convert.SimulationOutputError, match="does not cover"):
        convert.resample(np.zeros((10, 1)), DT_S, np.array([0.0, 1e-6]))


def test_simulated_frame_aligns_the_direct_wave_and_isolates_the_echo() -> None:
    scene, background = _synthetic_pair(echo_peak_s=10e-9)
    frame = convert.simulated_frame(scene, background, DT_S)
    assert frame.total.shape == (instrument.SAMPLES_PER_TRACE, instrument.TRACES_PER_FRAME)
    assert int(np.argmax(frame.total[:, 0])) == instrument.DIRECT_WAVE_PEAK_SAMPLE
    # Echo 7 ns after the direct wave -> 70 samples later; background is gone from the scattered field.
    assert int(np.argmax(frame.scattered[:, 0])) == pytest.approx(instrument.DIRECT_WAVE_PEAK_SAMPLE + 70, abs=1)
    assert np.abs(frame.scattered[instrument.DIRECT_WAVE_PEAK_SAMPLE, :]).max() < 1e-6


def test_a_b_scan_with_the_wrong_trace_count_is_refused() -> None:
    scene, background = _synthetic_pair()
    with pytest.raises(convert.SimulationOutputError, match="merged"):
        convert.simulated_frame(scene[:, :10], background, DT_S)


def _decaying(rate_per_sample: float) -> np.ndarray:
    """The real depth profile, faded faster (rate > 0) or slower (rate < 0) with depth."""
    rows = np.arange(instrument.SAMPLES_PER_TRACE) - convert.ECHO_ZONE_START
    return instrument.depth_profile() * np.exp(-rate_per_sample * rows)


def test_gain_is_flat_when_synthetic_frames_already_fade_like_real_ones() -> None:
    np.testing.assert_allclose(convert.derive_time_gain([instrument.depth_profile()]), 1.0, rtol=1e-6)


def test_gain_direction_amplifies_a_faded_region_and_damps_a_too_bright_one() -> None:
    # An inverted log-ratio (target/typical swapped) flips the sign of the fitted slope,
    # so these directions only hold if the correction is computed the right way round.
    boost = convert.derive_time_gain([_decaying(0.01)])  # fades too fast -> brighten depth
    assert boost[200] > boost[100] > boost[convert.ECHO_ZONE_START + 5]
    assert boost[200] / boost[100] == pytest.approx(np.exp(1.0), rel=1e-6)
    damp = convert.derive_time_gain([_decaying(-0.01)])  # fades too slowly -> dim depth
    assert damp[200] < damp[100] < damp[convert.ECHO_ZONE_START + 5]


def test_the_direct_wave_rows_are_never_boosted() -> None:
    # The regression that made every synthetic image a bright band over grey: the gain at
    # the direct wave reached its 50x cap.
    for rate in (0.05, -0.05, 0.0):
        np.testing.assert_array_equal(convert.derive_time_gain([_decaying(rate)])[: convert.ECHO_ZONE_START], 1.0)


def test_gain_is_capped_so_it_never_just_amplifies_noise() -> None:
    gain = convert.derive_time_gain([_decaying(0.1)])  # needs e^21 by the last row
    assert gain.max() == pytest.approx(convert._GAIN_LIMIT)


def test_gain_needs_at_least_one_frame() -> None:
    with pytest.raises(ValueError):
        convert.derive_time_gain([])


def _frame(direct: float, echoes: list[tuple[int, float]]) -> convert.SimulatedFrame:
    """A frame with a direct wave identical in every trace plus flat echo segments."""
    background = np.zeros((instrument.SAMPLES_PER_TRACE, 1))
    background[18:23, 0] = np.array([-0.3, 0.5, 1.0, 0.5, -0.3]) * direct
    scattered = np.zeros((instrument.SAMPLES_PER_TRACE, instrument.TRACES_PER_FRAME))
    for index, (row, amplitude) in enumerate(echoes):
        first = 40 + index * 60
        scattered[row : row + 3, first : first + 40] = np.array([[1.0], [-1.0], [0.5]]) * amplitude
    return convert.SimulatedFrame(total=background + scattered, scattered=scattered)


def _direct_to_echo(total: np.ndarray) -> float:
    mean_trace = total.mean(axis=1)
    return float(np.abs(mean_trace).max() / np.abs(total - mean_trace[:, None]).max())


def test_a_frame_matching_the_real_direct_to_echo_ratio_still_matches_after_gain() -> None:
    frame = _frame(0.6, [(60, 1.0), (120, 0.5), (180, 0.25)])
    gain = convert.derive_time_gain([convert._rms_profile(frame.total)])
    # Real lines measure 0.40-1.18; before the fix this came out near 17.
    assert 0.3 < _direct_to_echo(convert.apply_gain(frame, gain).total) < 1.5


def test_frames_with_nothing_buried_are_left_out_of_the_gain() -> None:
    empty = _frame(0.6, [])
    assert not convert.has_echoes(empty)
    echoing = [_frame(0.6, [(60, 1.0), (120, 0.5)]) for _ in range(convert._MIN_GAIN_FRAMES)]
    _gain, used = convert.time_gain_for([empty, *echoing, empty])
    assert used == convert._MIN_GAIN_FRAMES
    gain, used = convert.time_gain_for([empty])
    assert used == 0
    np.testing.assert_array_equal(gain, 1.0)


def test_too_few_frames_leave_the_gain_at_unity() -> None:
    # A median over a handful of sparse scenes mostly reflects those scenes: on the smoke
    # batch, one extra scene moved the fitted gain 15x at depth, and every label box with it.
    few = [_frame(0.6, [(60, 1.0), (200, 0.01)]) for _ in range(convert._MIN_GAIN_FRAMES - 1)]
    gain, used = convert.time_gain_for(few)
    assert used == 0
    np.testing.assert_array_equal(gain, 1.0)


def test_time_gain_needs_at_least_one_frame() -> None:
    with pytest.raises(ValueError):
        convert.time_gain_for([])


def test_an_empty_frame_still_gets_noise() -> None:
    # Otherwise "perfectly clean" becomes the detector's cue for "nothing here".
    empty = _frame(0.6, []).total
    sigma = convert.noise_sigma(empty)
    assert sigma == pytest.approx(instrument.NOISE_TO_PEAK_SCATTER * 0.6 / instrument.DIRECT_TO_PEAK_ECHO)


def test_apply_gain_scales_total_and_scattered_alike() -> None:
    frame = convert.SimulatedFrame(total=np.ones((4, 3)), scattered=np.full((4, 3), 2.0))
    gained = convert.apply_gain(frame, np.array([1.0, 2.0, 3.0, 4.0]))
    np.testing.assert_array_equal(gained.total[:, 0], [1, 2, 3, 4])
    np.testing.assert_array_equal(gained.scattered[:, 0], [2, 4, 6, 8])


def test_apply_gain_rejects_a_curve_of_the_wrong_length() -> None:
    frame = convert.SimulatedFrame(total=np.ones((4, 3)), scattered=np.ones((4, 3)))
    with pytest.raises(ValueError):
        convert.apply_gain(frame, np.ones(5))


def test_noise_is_set_by_the_measured_ratio_and_scaled() -> None:
    total = np.zeros((256, 384))
    total[50, 10] = 100.0
    sigma = convert.noise_sigma(total, level_scale=2.0)
    reference = np.abs((total - total.mean(axis=1, keepdims=True))[20:120]).max()
    assert sigma == pytest.approx(instrument.NOISE_TO_PEAK_SCATTER * reference * 2.0)
    noisy = convert.add_noise(np.zeros((256, 384)), sigma, np.random.default_rng(0))
    assert noisy.std() == pytest.approx(sigma, rel=0.02)


def test_negative_noise_is_refused() -> None:
    with pytest.raises(ValueError):
        convert.add_noise(np.zeros((2, 2)), -1.0, np.random.default_rng(0))
