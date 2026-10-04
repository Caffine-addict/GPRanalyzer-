"""One recorded GPR line as a file reader returns it, whatever the manufacturer's format."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RadarLine:
    traces: np.ndarray  # (n_traces, n_samples), float64, signed amplitude
    sample_interval_ns: float
    trace_spacing_m: float | None  # None when the file carries no usable distance
    distances_m: np.ndarray | None
    positions_m: np.ndarray | None = None  # (n_traces, 2) easting, northing in the file's grid
