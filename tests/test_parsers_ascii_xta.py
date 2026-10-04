"""Tests for parsers/ascii_xta.py — distance / time / amplitude ASCII exports."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from parsers.ascii_xta import read_xta


def write_xta(path: Path, traces: np.ndarray, spacing: float = 0.02, dt: float = 0.1) -> Path:
    rows = [f"{i * spacing:14.6f} {j * dt:14.6f} {v:14.6f}\r\n"
            for i, trace in enumerate(traces) for j, v in enumerate(trace)]
    path.write_text("".join(rows))
    return path


def test_traces_time_axis_and_spacing(tmp_path: Path) -> None:
    traces = np.array([[1.0, -2.0, 3.0], [4.0, 5.0, -6.0]])
    line = read_xta(write_xta(tmp_path / "line.asc", traces))
    assert np.array_equal(line.traces, traces)
    assert line.sample_interval_ns == pytest.approx(0.1)
    assert line.trace_spacing_m == pytest.approx(0.02)


@pytest.mark.parametrize("problem", ["not_three_columns", "ragged", "irregular_time", "garbage_mid_file"])
def test_irregular_exports_are_refused_not_resampled(tmp_path: Path, problem: str) -> None:
    path = write_xta(tmp_path / "line.asc", np.zeros((2, 3)))
    text = path.read_text()
    if problem == "not_three_columns":
        path.write_text(text + "1.0\n")
    elif problem == "ragged":
        path.write_text(text + "0.040000 0.000000 1.0\n")
    elif problem == "irregular_time":
        path.write_text(text.replace("0.020000       0.100000", "0.020000       0.150000"))
    else:  # a corrupted transfer: must be refused, not silently cut short
        lines = text.splitlines(keepends=True)
        path.write_text("".join(lines[:2]) + "  0.0 garbage 1.0\n" + "".join(lines[2:]))
    with pytest.raises(ValueError):
        read_xta(path)


_REAL = Path("Dataset/external/tu1208/Database_2018/GPR3_ASCII/600MHZ_SILT_H1_REV.ASC")


@pytest.mark.skipif(not _REAL.exists(), reason="TU1208 dataset not downloaded")
def test_a_real_ids_export() -> None:
    line = read_xta(_REAL)
    assert line.traces.shape == (477, 1024)  # the paper's Table 3: 477 traces, 9.52 m
    assert line.trace_spacing_m == pytest.approx(0.02)
    assert line.sample_interval_ns == pytest.approx(0.0977, abs=1e-3)
