"""Tests for parsers/dzt.py — GSSI radargrams."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from parsers.dzt import read_dzt


def write_dzt(path: Path, signed: np.ndarray, bits: int, spm: float = 50.0, nchan: int = 1) -> None:
    _, nsamp = signed.shape
    header = bytearray(1024)
    header[0:10] = struct.pack("<HHHHh", 255, 1024, nsamp, bits, 0)
    header[10:30] = struct.pack("<5f", 0.0, spm, 0.0, 0.0, 70.0)
    header[52:54] = struct.pack("<H", nchan)
    if bits == 32:
        body = signed.astype("<i4").tobytes()
    else:
        dtype, zero = {8: ("<u1", 128), 16: ("<u2", 32768)}[bits]
        body = (signed + zero).astype(dtype).tobytes()
    path.write_bytes(bytes(header) + body)


@pytest.mark.parametrize("bits", [8, 16, 32])
def test_samples_come_back_signed_with_their_polarity(tmp_path: Path, bits: int) -> None:
    signed = np.tile(np.array([0, 0, 5, -7, 12, -100]), (4, 1))
    path = tmp_path / "line.dzt"
    write_dzt(path, signed, bits)
    line = read_dzt(path)
    assert line.traces.shape == (4, 6)
    assert line.traces[0, 2:].tolist() == [5, -7, 12, -100]  # negative stays negative
    assert line.traces[0, :2].tolist() == [5, 5]  # scan mark and counter replaced
    assert line.sample_interval_ns == pytest.approx(70.0 / 6)
    assert line.trace_spacing_m == pytest.approx(0.02)


def test_no_wheel_means_no_distance_rather_than_a_guess(tmp_path: Path) -> None:
    path = tmp_path / "line.dzt"
    write_dzt(path, np.zeros((3, 8)), 16, spm=0.0)
    line = read_dzt(path)
    assert line.trace_spacing_m is None and line.distances_m is None


@pytest.mark.parametrize("problem", ["short", "ragged", "multichannel"])
def test_files_this_reader_cannot_read_are_refused(tmp_path: Path, problem: str) -> None:
    path = tmp_path / "line.dzt"
    if problem == "short":
        path.write_bytes(b"x" * 100)
    else:
        write_dzt(path, np.zeros((3, 8)), 16, nchan=2 if problem == "multichannel" else 1)
        if problem == "ragged":
            path.write_bytes(path.read_bytes() + b"\x00")
    with pytest.raises(ValueError):
        read_dzt(path)


_REAL = Path("Dataset/external/tu1208/Database_2018/SILT/400MHz_silt_2_rev.dzt")


@pytest.mark.skipif(not _REAL.exists(), reason="TU1208 dataset not downloaded")
def test_a_real_gssi_line() -> None:
    line = read_dzt(_REAL)
    assert line.traces.shape == (1316, 512)  # the paper's Table 3: 1316 scans
    assert line.sample_interval_ns == pytest.approx(70.0 / 512)
    assert abs(np.median(line.traces)) < 2000  # centred: the 32768 offset is removed
