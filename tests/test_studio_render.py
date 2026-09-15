"""Tests for studio/palette.py and studio/render.py — how a radargram is drawn."""

from __future__ import annotations

import numpy as np
import pytest

from studio import palette
from studio.render import DisplaySettings, encode_png, normalise, to_rgb, trace_waveform


def test_every_palette_builds_a_full_256_entry_rgb_table() -> None:
    for entry in palette.available():
        table = palette.lut(entry.name)
        assert table.shape == (256, 3)
        assert table.dtype == np.uint8


def test_palettes_run_from_their_first_stop_to_their_last() -> None:
    for entry in palette.available():
        table = palette.lut(entry.name)
        assert tuple(table[0]) == entry.stops[0][1]
        assert tuple(table[-1]) == entry.stops[-1][1]


def test_an_unknown_palette_names_the_ones_that_exist() -> None:
    with pytest.raises(KeyError, match="unknown palette"):
        palette.get("chartreuse")


def test_the_default_palette_exists() -> None:
    assert palette.get(palette.DEFAULT_PALETTE)


def test_diverging_palettes_put_their_neutral_tone_in_the_middle() -> None:
    # Zero is meaningful on a radargram — a reflection off metal and one off a
    # void differ by polarity — so mid-scale must be the neutral tone.
    table = palette.lut("seismic")
    mid = table[128].astype(int)
    assert abs(int(mid[0]) - int(mid[2])) < 30  # roughly neutral, not blue or red


def test_diverging_normalisation_maps_zero_to_mid_scale() -> None:
    data = np.linspace(-10, 10, 256).reshape(16, 16)
    out = normalise(data, DisplaySettings(palette="grey", contrast_percentile=100))
    zero_index = np.argmin(np.abs(data))
    assert out.flat[zero_index] == pytest.approx(128, abs=2)


def test_diverging_normalisation_is_symmetric_for_equal_and_opposite_values() -> None:
    data = np.array([[-5.0, 5.0]])
    out = normalise(data, DisplaySettings(palette="grey", contrast_percentile=100))
    assert int(out[0, 0]) + int(out[0, 1]) == pytest.approx(255, abs=2)


def test_sequential_normalisation_spans_the_full_range() -> None:
    data = np.linspace(0, 100, 64).reshape(8, 8)
    out = normalise(data, DisplaySettings(palette="rainbow", contrast_percentile=100))
    assert out.min() == 0
    assert out.max() == 255


def test_lower_contrast_percentile_clips_harder() -> None:
    data = np.random.default_rng(0).normal(size=(64, 64))
    soft = normalise(data, DisplaySettings(contrast_percentile=100))
    hard = normalise(data, DisplaySettings(contrast_percentile=85))
    assert (hard == 0).sum() + (hard == 255).sum() > (soft == 0).sum() + (soft == 255).sum()


def test_brightness_shifts_the_whole_image() -> None:
    data = np.zeros((8, 8))
    base = normalise(data, DisplaySettings(brightness=0.0))
    lifted = normalise(data, DisplaySettings(brightness=0.5))
    assert lifted.mean() > base.mean()


def test_render_returns_native_size_when_none_is_requested() -> None:
    data = np.random.default_rng(1).normal(size=(256, 386))
    assert to_rgb(data, DisplaySettings()).shape == (256, 386, 3)


def test_render_resamples_to_the_requested_size() -> None:
    data = np.random.default_rng(1).normal(size=(256, 386))
    assert to_rgb(data, DisplaySettings(width_px=800, height_px=400)).shape == (400, 800, 3)


def test_resampling_never_invents_a_value_that_was_not_in_the_source() -> None:
    # Nearest-neighbour only: a blended pixel would read as a real sample.
    data = np.array([[-1.0, 1.0], [1.0, -1.0]])
    out = to_rgb(data, DisplaySettings(palette="grey", contrast_percentile=100, width_px=8, height_px=8))
    assert len(np.unique(out.reshape(-1, 3), axis=0)) == 2


def test_rendering_an_empty_radargram_raises() -> None:
    with pytest.raises(ValueError, match="empty radargram"):
        to_rgb(np.zeros((0, 0)), DisplaySettings())


def test_rendering_an_all_nan_radargram_raises() -> None:
    with pytest.raises(ValueError, match="no finite samples"):
        to_rgb(np.full((4, 4), np.nan), DisplaySettings())


def test_an_invalid_render_size_raises() -> None:
    with pytest.raises(ValueError, match="invalid render size"):
        to_rgb(np.zeros((4, 4)), DisplaySettings(width_px=0, height_px=4))


def test_png_encoding_produces_a_png() -> None:
    encoded = encode_png(to_rgb(np.random.default_rng(2).normal(size=(32, 32)), DisplaySettings()))
    assert encoded[:8] == b"\x89PNG\r\n\x1a\n"


def test_trace_waveform_returns_the_requested_column() -> None:
    data = np.arange(20.0).reshape(4, 5)
    assert trace_waveform(data, 2) == [2.0, 7.0, 12.0, 17.0]


def test_trace_waveform_rejects_an_out_of_range_trace() -> None:
    with pytest.raises(IndexError, match="out of range"):
        trace_waveform(np.zeros((4, 5)), 5)
