"""Tests for parsers/mala.py — MALÅ .rd3/.rd7 with a .rad header."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from parsers.mala import read_mala


def write_mala(folder: Path, traces: np.ndarray, *, suffix: str = ".rd3", interval: float = 0.02,
               header_name: str | None = None, frequency: float = 5000.0) -> Path:
    data = folder / f"line{suffix}"
    traces.astype("<i2" if suffix == ".rd3" else "<i4").tofile(data)
    header = folder / (header_name or "line.rad")
    header.write_text(f"SAMPLES:{traces.shape[1]}\r\nFREQUENCY:{frequency}\r\nDISTANCE INTERVAL:{interval}\r\n"
                      f"LAST TRACE:{traces.shape[0]}\r\n")
    return data


@pytest.mark.parametrize("suffix", [".rd3", ".rd7"])
def test_signed_samples_interval_and_spacing(tmp_path: Path, suffix: str) -> None:
    traces = np.array([[0, -5, 300, -32000], [1, 2, -3, 4], [7, 0, 0, -7]])
    line = read_mala(write_mala(tmp_path, traces, suffix=suffix))
    assert np.array_equal(line.traces, traces)
    assert line.sample_interval_ns == pytest.approx(0.2)  # 5000 MHz sampling
    assert line.trace_spacing_m == pytest.approx(0.02)


def test_the_studio_header_name_is_found_too(tmp_path: Path) -> None:
    line = read_mala(write_mala(tmp_path, np.zeros((2, 4)), header_name="line.rd3.rad"))
    assert line.traces.shape == (2, 4)


def test_a_time_triggered_line_has_no_spacing_rather_than_a_guess(tmp_path: Path) -> None:
    line = read_mala(write_mala(tmp_path, np.zeros((2, 4)), interval=0.0))
    assert line.trace_spacing_m is None and line.distances_m is None


@pytest.mark.parametrize("problem", ["no_header", "ragged", "bad_header"])
def test_unreadable_lines_are_refused(tmp_path: Path, problem: str) -> None:
    data = write_mala(tmp_path, np.zeros((2, 4)))
    if problem == "no_header":
        (tmp_path / "line.rad").unlink()
    elif problem == "ragged":
        data.write_bytes(data.read_bytes() + b"\x00\x00")
    else:
        (tmp_path / "line.rad").write_text("SAMPLES:abc\n")
    with pytest.raises(ValueError):
        read_mala(data)


_REAL = Path("Dataset/external/tu1208/Database_2018/SILT/500MHz_silt_1_rev.rd3")


@pytest.mark.skipif(not _REAL.exists(), reason="TU1208 dataset not downloaded")
def test_a_real_mala_line() -> None:
    line = read_mala(_REAL)
    assert line.traces.shape == (1313, 499)  # the paper's Table 3: 1313 traces
    assert line.sample_interval_ns * 499 == pytest.approx(89.364912, rel=1e-4)  # the header's TIMEWINDOW
    assert line.trace_spacing_m == pytest.approx(0.019668)
