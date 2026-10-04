"""Read GSSI radargrams (.dzt), the format of GSSI's SIR systems and the most common GPR format.

Layout (GSSI's own `readgssi.m` and the SIR manuals): a 1024-byte header per channel, then scans of
`nsamp` samples. Samples are unsigned 8- or 16-bit with the zero level at mid-range (128, 32768),
or signed 32-bit. The first two samples of every scan are a scan mark and a counter, not signal,
and are replaced by the third, as GSSI does. Time window `range` ns spans the scan; distance comes
from scans-per-metre, and is unknown (None) when the file was recorded without a wheel (spm = 0).

Only single-channel files are read: a multi-channel file is refused rather than mixed up.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from core.contracts import ScanFrame
from parsers.base import register
from parsers.line import RadarLine, to_scan_frame

_HEADER = 1024
_UNSIGNED = {8: ("<u1", 128), 16: ("<u2", 32768)}


def read_dzt(path: Path) -> RadarLine:
    data = Path(path).read_bytes()
    if len(data) < _HEADER:
        raise ValueError(f"{path}: too short for a GSSI header")
    _tag, data_field, nsamp, bits, _zero = struct.unpack("<HHHHh", data[:10])
    _sps, spm, _mpm, _position, time_range = struct.unpack("<5f", data[10:30])
    (nchan,) = struct.unpack("<H", data[52:54])
    if nchan > 1:
        raise ValueError(f"{path}: {nchan}-channel GSSI files are not supported")
    if nsamp < 3 or bits not in (8, 16, 32) or not 0 < time_range < 10_000:
        raise ValueError(f"{path}: not a GSSI layout this reader understands (nsamp={nsamp}, bits={bits})")
    offset = _HEADER * data_field if data_field < _HEADER else _HEADER * max(nchan, 1)
    scan_bytes = nsamp * bits // 8
    body = len(data) - offset
    if body <= 0 or body % scan_bytes:
        raise ValueError(f"{path}: data size does not divide into {nsamp}-sample scans")
    if bits == 32:
        samples = np.frombuffer(data, dtype="<i4", offset=offset).astype(np.float64)
    else:
        dtype, zero = _UNSIGNED[bits]
        samples = np.frombuffer(data, dtype=dtype, offset=offset).astype(np.float64) - zero
    traces = samples.reshape(-1, nsamp).copy()
    traces[:, :2] = traces[:, 2:3]  # scan mark and counter, not signal
    spacing = 1.0 / spm if spm > 0 else None
    distances = np.arange(len(traces)) * spacing if spacing else None
    return RadarLine(traces, time_range / nsamp, spacing, distances)


def _dielectric(path: Path) -> float | None:
    (epsr,) = struct.unpack("<f", Path(path).read_bytes()[54:58])
    return float(epsr) if 1.0 <= epsr <= 81.0 else None  # the operator's setting; 0 when never set


@register("dzt")
def parse_dzt(path: Path) -> ScanFrame:
    return to_scan_frame(read_dzt(path), source_type="gssi_dzt", path=Path(path), dielectric=_dielectric(path))
