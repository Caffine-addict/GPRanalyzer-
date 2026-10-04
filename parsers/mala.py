"""Read MALÅ radargrams: a `.rd3` (16-bit) or `.rd7` (32-bit) sample file beside a `.rad` header.

The header is plain `KEY:value` text. What is used: SAMPLES per trace, FREQUENCY (the sampling
frequency in MHz, so the interval is 1000 / FREQUENCY ns), DISTANCE INTERVAL (metres per trace,
0 when the line was recorded on time rather than a wheel) and LAST TRACE. Samples are signed,
little-endian, one trace after another with no trace headers.

The header is looked for as `<file>.rad` first (`Single-01.rd3.rad`, the name the Studio stores it
under so it can never be mistaken for an SPR `.RAD` channel on a case-insensitive disk), then as
`<name>.rad`, MALÅ's own layout.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from core.contracts import ScanFrame
from parsers.base import register
from parsers.line import RadarLine, to_scan_frame

_DTYPES: dict[str, np.dtype[np.signedinteger]] = {".rd3": np.dtype("<i2"), ".rd7": np.dtype("<i4")}


def header_path(path: Path) -> Path | None:
    for candidate in (Path(f"{path}.rad"), path.with_suffix(".rad"), path.with_suffix(".RAD")):
        if candidate.exists() and candidate != path:
            return candidate
    return None


def read_header(path: Path) -> dict[str, str]:
    fields = {}
    for line in path.read_text(encoding="latin-1").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip().upper()] = value.strip()
    return fields


def read_mala(path: Path) -> RadarLine:
    path = Path(path)
    dtype = _DTYPES.get(path.suffix.lower())
    if dtype is None:
        raise ValueError(f"{path}: not a MALÅ sample file (.rd3 or .rd7)")
    head_path = header_path(path)
    if head_path is None:
        raise ValueError(f"{path}: its .rad header is missing — MALÅ samples cannot be read without it")
    header = read_header(head_path)
    try:
        samples = int(header["SAMPLES"])
        frequency_mhz = float(header["FREQUENCY"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"{head_path}: missing or unreadable SAMPLES/FREQUENCY ({exc})") from exc
    if samples <= 0 or frequency_mhz <= 0:
        raise ValueError(f"{head_path}: SAMPLES and FREQUENCY must be positive")

    data = np.fromfile(path, dtype=dtype)
    if data.size == 0 or data.size % samples:
        raise ValueError(f"{path}: {data.size} samples do not divide into traces of {samples}")
    traces = data.reshape(-1, samples).astype(np.float64)

    try:
        interval = float(header.get("DISTANCE INTERVAL", "0"))
    except ValueError:
        interval = 0.0
    spacing = interval if interval > 0 else None
    distances = np.arange(len(traces)) * spacing if spacing else None
    return RadarLine(traces, 1000.0 / frequency_mhz, spacing, distances)


@register("rd3", "rd7")
def parse_mala(path: Path) -> ScanFrame:
    head = header_path(Path(path))
    header = read_header(head) if head else {}
    return to_scan_frame(read_mala(path), source_type="mala_file", path=Path(path), header=header)
