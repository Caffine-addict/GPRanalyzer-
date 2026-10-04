"""Read GPR radargrams stored as SEG-Y, as exported by RadarMap / Zond and most GPR software.

Used for the public University of Twente utility dataset (docs/EXTERNAL_DATA.md), whose 959
radargrams come with trial-trench ground truth. Only what that needs is read: the binary
header's sample count, interval and data format, each trace's receiver-x distance, and each
trace's map position when the file carries one.

Two GPR conventions differ from the seismic standard, and both are checked rather than assumed:

- **Byte order.** SEG-Y is big-endian, but RadarMap writes little-endian. The order whose binary
  header gives a sample count that fits the file exactly is the one used.
- **Units.** The sample interval field is microseconds in the standard; GPR writers put
  picoseconds there (97 → 0.097 ns, a 50 ns window for a 500 MHz antenna). Trace distance comes
  from the receiver-x field scaled by the coordinate scalar (-1000 → millimetres).
- **Positions.** The standard keeps source X/Y (bytes 73-80) under the same scalar. RadarMap
  writes NMEA latitude/longitude floats there instead, and its projected grid coordinates as
  float32 northing/easting at bytes 171-178 (the Dutch RD grid for Twente). A track whose fixes
  never change is no track, and is reported as None rather than placing every trace on one spot.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from parsers.line import RadarLine

_TEXT, _BINARY, _TRACE_HEADER = 3200, 400, 240
_MAX_FIELD = 32_767  # samples and interval are signed 16-bit; a swap of a sane value lands above or <= 0
_MAX_TRACE_STEP_M = 1.0
_FORMATS = {1: None, 2: "i4", 3: "i2", 5: "f4", 8: "i1"}  # 1 = IBM float: not supported


def _layout(data: bytes, endian: str) -> tuple[int, int, int] | None:
    """(samples, interval, format) if this byte order describes the file exactly, else None."""
    if len(data) < _TEXT + _BINARY:
        return None
    header = data[_TEXT:_TEXT + _BINARY]
    interval, samples, fmt = (struct.unpack(f"{endian}h", header[o:o + 2])[0] for o in (16, 20, 24))
    dtype = _FORMATS.get(fmt)
    # Bounds keep the wrong byte order from passing on file size alone (a swapped 512 is 2).
    if not (2 <= samples <= _MAX_FIELD and 1 <= interval <= _MAX_FIELD) or dtype is None:
        return None
    trace_bytes = _TRACE_HEADER + samples * np.dtype(dtype).itemsize
    body = len(data) - _TEXT - _BINARY
    return (samples, interval, fmt) if body > 0 and body % trace_bytes == 0 else None


def read_segy(path: Path) -> RadarLine:
    data = Path(path).read_bytes()
    for endian, prefix in (("<", "<"), (">", ">")):
        layout = _layout(data, endian)
        if layout is not None:
            break
    else:
        raise ValueError(f"{path}: not a SEG-Y layout this reader understands (IBM floats are not supported)")
    samples, interval, fmt = layout
    dtype = np.dtype(prefix + _FORMATS[fmt])  # type: ignore[operator]
    trace_bytes = _TRACE_HEADER + samples * dtype.itemsize
    n = (len(data) - _TEXT - _BINARY) // trace_bytes
    raw = np.frombuffer(data, dtype=np.uint8, offset=_TEXT + _BINARY).reshape(n, trace_bytes)
    traces = raw[:, _TRACE_HEADER:].copy().view(dtype).astype(np.float64)
    headers = raw[:, :_TRACE_HEADER]
    scalar = struct.unpack(f"{endian}h", bytes(headers[0, 70:72]))[0]
    factor = 1 / -scalar if scalar < 0 else (scalar or 1)
    x = np.array([struct.unpack(f"{endian}i", bytes(h[80:84]))[0] for h in headers], dtype=np.float64) * factor
    steps = np.diff(x)
    spacing = float(np.median(steps)) if steps.size and np.median(steps) > 0 else None
    positions = _positions(data[:_TEXT], headers, endian, factor)
    return RadarLine(traces, interval / 1000.0, spacing, x if spacing else None, positions)


def _positions(text: bytes, headers: np.ndarray, endian: str, factor: float) -> np.ndarray | None:
    if b"RadarMap" in text:
        northing_easting = np.array([struct.unpack(f"{endian}ff", bytes(h[170:178])) for h in headers])
        xy = northing_easting[:, ::-1].astype(np.float64)
    else:
        xy = np.array([struct.unpack(f"{endian}ii", bytes(h[72:80])) for h in headers], dtype=np.float64) * factor
    if not np.isfinite(xy).all() or len(np.unique(xy, axis=0)) < max(2, 0.2 * len(xy)):
        return None
    # Traces are centimetres apart: a track jumping metres per trace is misread bytes, not a walk.
    if np.median(np.linalg.norm(np.diff(xy, axis=0), axis=1)) > _MAX_TRACE_STEP_M:
        return None
    return xy
