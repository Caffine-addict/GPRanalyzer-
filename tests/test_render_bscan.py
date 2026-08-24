"""Tests for render/bscan.py."""

from __future__ import annotations

import logging
import warnings

import numpy as np
import pytest

from render.bscan import TARGET_SIZE, traces_to_image


def test_traces_to_image_resizes_to_target_size() -> None:
    traces = np.random.default_rng(1).normal(size=(20, 300))
    out = traces_to_image(traces)
    assert out.shape == (TARGET_SIZE, TARGET_SIZE)
    assert out.dtype == np.uint8


def test_traces_to_image_rejects_non_2d() -> None:
    with pytest.raises(ValueError, match="n_traces"):
        traces_to_image(np.zeros((3, 3, 3)))


def test_traces_to_image_handles_constant_traces() -> None:
    traces = np.full((10, 50), 5.0)
    out = traces_to_image(traces)
    assert out.shape == (TARGET_SIZE, TARGET_SIZE)
    assert np.all(out == 0)


def test_traces_to_image_rejects_all_nan() -> None:
    traces = np.full((5, 5), np.nan)
    with pytest.raises(ValueError, match="NaN"):
        traces_to_image(traces)


def test_traces_to_image_rejects_partial_nan() -> None:
    # A few dropped/corrupt samples mixed with otherwise-valid data is the
    # realistic hardware scenario — NaN survives min-max scaling and would
    # silently render a meaningless pixel there rather than erroring.
    traces = np.array([[0.0, 10.0, np.nan], [5.0, 7.0, 3.0]])
    with pytest.raises(ValueError, match="NaN"):
        traces_to_image(traces)


def test_traces_to_image_all_infinite_no_nan_still_rejected_as_no_finite_values() -> None:
    traces = np.full((3, 3), np.inf)
    with pytest.raises(ValueError, match="finite"):
        traces_to_image(traces)


def test_traces_to_image_logs_uncalibrated_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="render.bscan"):
        traces_to_image(np.random.default_rng(2).normal(size=(8, 8)))

    messages = [r.getMessage() for r in caplog.records]
    assert any("uncalibrated" in m for m in messages)


def test_traces_to_image_output_range_is_within_uint8_bounds() -> None:
    traces = np.array([[0.0, 10.0], [5.0, 10.0]])
    out = traces_to_image(traces)
    assert out.min() >= 0
    assert out.max() <= 255
    # min-max scaling should use the full range for a non-degenerate input.
    assert out.max() > out.min()


def test_traces_to_image_reaches_exact_bounds_for_full_range_input() -> None:
    # Catches an off-by-one scale factor (e.g. *254 instead of *255), which
    # a mere "within bounds" check can't.
    traces = np.array([[0.0, 10.0], [10.0, 0.0]])
    out = traces_to_image(traces)
    assert out.min() == 0
    assert out.max() == 255


def test_traces_to_image_preserves_scaling_polarity() -> None:
    # Distinct constant halves so resize interpolation can't blur the check:
    # top half is the global min, bottom half the global max. Catches lo/hi
    # being silently swapped (an inverted, photometrically wrong B-scan).
    traces = np.vstack([np.zeros((5, 20)), np.full((5, 20), 10.0)])
    out = traces_to_image(traces)
    assert out[0, :].max() < out[-1, :].min()


def test_traces_to_image_clips_infinite_values_without_warning() -> None:
    traces = np.array([[0.0, 10.0, np.inf], [5.0, -np.inf, 7.0]])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = traces_to_image(traces)
    assert out.shape == (TARGET_SIZE, TARGET_SIZE)
    assert out.min() >= 0
    assert out.max() <= 255
