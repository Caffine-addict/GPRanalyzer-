"""Tests for parsers/segy.py — SEG-Y radargrams as GPR software writes them."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from parsers.segy import read_segy


def write_segy(path: Path, traces: np.ndarray, interval_ps: int, spacing_mm: int, endian: str = "<") -> None:
    _, samples = traces.shape
    binary = bytearray(400)
    binary[16:18] = struct.pack(f"{endian}h", interval_ps)
    binary[20:22] = struct.pack(f"{endian}h", samples)
    binary[24:26] = struct.pack(f"{endian}h", 3)  # int16
    body = bytearray()
    for i, trace in enumerate(traces):
        header = bytearray(240)
        header[70:72] = struct.pack(f"{endian}h", -1000)
        header[80:84] = struct.pack(f"{endian}i", i * spacing_mm)
        body += header + trace.astype(f"{endian}i2").tobytes()
    path.write_bytes(bytes(3200) + bytes(binary) + bytes(body))


@pytest.mark.parametrize("endian", ["<", ">"])
def test_reads_traces_interval_and_spacing_in_either_byte_order(tmp_path: Path, endian: str) -> None:
    traces = np.arange(3 * 8).reshape(3, 8) - 10
    path = tmp_path / "line.sgy"
    write_segy(path, traces, interval_ps=97, spacing_mm=20, endian=endian)
    line = read_segy(path)
    assert np.array_equal(line.traces, traces)
    assert line.sample_interval_ns == pytest.approx(0.097)
    assert line.trace_spacing_m == pytest.approx(0.02)


def test_a_file_that_is_not_segy_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "junk.sgy"
    path.write_bytes(b"x" * 5000)
    with pytest.raises(ValueError, match="not a SEG-Y layout"):
        read_segy(path)


def test_a_truncated_file_is_refused_not_crashed(tmp_path: Path) -> None:
    path = tmp_path / "short.sgy"
    path.write_bytes(b"x" * 100)
    with pytest.raises(ValueError, match="not a SEG-Y layout"):
        read_segy(path)


def test_big_endian_is_not_mistaken_for_little_when_the_size_fits_both(tmp_path: Path) -> None:
    # 61 traces of 512 int16 samples also divide into 244-byte traces, the size a byte-swapped
    # header (512 -> 2 samples) would imply; the format code and the bounds must reject that reading.
    traces = np.arange(61 * 512, dtype=np.int64).reshape(61, 512) % 1000
    path = tmp_path / "big.sgy"
    write_segy(path, traces, interval_ps=97, spacing_mm=20, endian=">")
    line = read_segy(path)
    assert line.traces.shape == (61, 512)
    assert line.sample_interval_ns == pytest.approx(0.097)


_REAL = Path("Dataset/external/twente_utilities/01/01.1/Radargrams/Path1.sgy")


@pytest.mark.skipif(not _REAL.exists(), reason="Twente dataset not downloaded")
def test_a_real_twente_line() -> None:
    line = read_segy(_REAL)
    assert line.traces.shape == (304, 512)
    assert line.trace_spacing_m == pytest.approx(0.02)
    assert line.sample_interval_ns == pytest.approx(0.097)
