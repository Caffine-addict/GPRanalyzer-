"""One recorded GPR line as a file reader returns it, whatever the manufacturer's format."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.contracts import ScanFrame


@dataclass(frozen=True)
class RadarLine:
    traces: np.ndarray  # (n_traces, n_samples), float64, signed amplitude
    sample_interval_ns: float
    trace_spacing_m: float | None  # None when the file carries no usable distance
    distances_m: np.ndarray | None
    positions_m: np.ndarray | None = None  # (n_traces, 2) easting, northing in the file's grid


def to_scan_frame(line: RadarLine, *, source_type: str, path: Path, header: dict[str, str] | None = None,
                  dielectric: float | None = None) -> ScanFrame:
    """The pipeline's frame for a line read from a file: traces, time axis and wheel spacing."""
    return ScanFrame(
        source_type=source_type,
        provenance={"path": str(path), "raw_header": dict(header or {})},
        traces=line.traces,
        sample_interval_ns=line.sample_interval_ns,
        dielectric_assumed=dielectric,
        trace_spacing_m=line.trace_spacing_m,
    )
