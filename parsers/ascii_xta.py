"""Read a radargram exported as ASCII columns: distance (m), two-way time (ns), amplitude.

The common export of GPR software when the native format is closed: IDS GRED writes it (its .dt
files are undocumented and are not read directly), as do Reflexw and others. One row per sample,
trace after trace. The grid must be regular: every trace the same times, distance increasing
trace by trace. Anything else is refused rather than resampled.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from core.contracts import ScanFrame
from parsers.base import register
from parsers.line import RadarLine, to_scan_frame


def read_xta(path: Path) -> RadarLine:
    lines = [line.split() for line in Path(path).read_text(encoding="latin-1").splitlines() if line.strip()]
    if len(lines) < 2 or any(len(cols) != 3 for cols in lines):
        raise ValueError(f"{path}: not three columns of distance, time and amplitude on every line")
    try:
        rows = np.array(lines, dtype=np.float64)  # a non-numeric token raises; nothing is skipped
    except ValueError as exc:
        raise ValueError(f"{path}: a value is not a number ({exc})") from exc
    x, t, amplitude = rows[:, 0], rows[:, 1], rows[:, 2]
    starts = np.flatnonzero(np.r_[True, np.diff(x) != 0])  # a new trace wherever distance changes
    n_traces = len(starts)
    if len(rows) % n_traces:
        raise ValueError(f"{path}: traces have different numbers of samples")
    n_samples = len(rows) // n_traces
    times = t[:n_samples]
    if not (np.all(np.diff(starts) == n_samples) and np.allclose(t.reshape(n_traces, n_samples), times)):
        raise ValueError(f"{path}: samples are not on one regular time grid")
    dt = float(np.median(np.diff(times)))
    if dt <= 0:
        raise ValueError(f"{path}: time does not increase down the trace")
    distances = x[starts]
    steps = np.diff(distances)
    spacing = float(np.median(steps)) if n_traces > 1 and np.median(steps) > 0 else None
    return RadarLine(amplitude.reshape(n_traces, n_samples).copy(), dt, spacing, distances if spacing else None)


@register("asc")
def parse_xta(path: Path) -> ScanFrame:
    return to_scan_frame(read_xta(path), source_type="ascii_xta", path=Path(path))
