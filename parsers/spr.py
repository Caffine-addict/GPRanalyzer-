"""Loads Subsurface Imaging Systems SPRScan 3D .RAD/.RA1/.RA2 files into a ScanFrame.

Reverse-engineered against 4 real survey files (all SPR_FILE_VERSION 7) —
no vendor spec exists for this proprietary format. Every structural
assumption below is validated against the file itself at parse time
(record stride, each record's own self-declared header length) rather than
assumed outright; a file that doesn't match raises instead of silently
mis-parsing. Only SPR_FILE_VERSION "7" is supported — fail loudly for any
other version rather than guess whether the layout still holds.

Sample dtype (signed int16 little-endian) was confirmed empirically, not
from documentation: interpreting the bytes this way gives a near-zero-mean
signal with strong trace-to-trace spatial coherence (real B-scans are
smooth along the survey direction); the unsigned interpretation does not.

SPR_SAMPLING_INTERVAL's unit is likewise not vendor-documented. It is
assumed to be picoseconds based on physical plausibility (256 samples *
100ps = 25.6ns two-way window -> roughly 1.3m depth at the recorded
dielectric of 9, a sane range for a handheld utility-locating GPR) — this
is an inference, not a confirmed fact. Do not wire this into
SourceCapabilities.has_calibrated_depth until the company confirms it.
"""

from __future__ import annotations

import re
import struct
from itertools import pairwise
from pathlib import Path

import numpy as np

from core.contracts import ScanFrame
from parsers.base import register

_SUPPORTED_FILE_VERSION = "7"
_RECORD_HEADER_TAG = b"\x0d\x00LINE_ID"
_TAG_OFFSET_IN_RECORD = 32  # offset of the tag's own first byte (0x0d) within a record, SPR_FILE_VERSION 7
_RECORD_HEADER_LEN = 64
_SAMPLE_DTYPE = np.dtype("<i2")

_HEADER_FIELD_RE = re.compile(r"([A-Z][A-Z0-9_]*)\s+(.*)")
_HEADER_ANCHOR = b"ACQUISITION_DATE"
_HEADER_WINDOW = 2048


def _parse_text_header(data: bytes) -> dict[str, str]:
    """Locate and parse the ASCII key/value header block.

    Two things about the real format aren't documented anywhere and were
    confirmed empirically: the header is not at byte 0 — every real file
    examined has a large (~64KB) opaque binary block before it, of fixed
    size regardless of survey length (so it isn't per-trace data; its
    purpose is unknown) — so this anchors on a known field name instead of
    assuming a position. And each "KEY VALUE" pair is a null-terminated
    string preceded by a 2-byte length field (same style as the per-trace
    LINE_ID/SPR_MARKER fields _locate_records reads), not a newline-
    separated line — splitting on NUL and taking the first KEY-looking run
    in each fragment sidesteps the length-prefix encoding rather than
    replicating it exactly, since only the field values are needed here.
    """
    anchor = data.find(_HEADER_ANCHOR)
    if anchor < 0:
        raise ValueError(f"SPR text header not found: {_HEADER_ANCHOR!r} does not appear in file")

    window = data[anchor : anchor + _HEADER_WINDOW]
    header: dict[str, str] = {}
    for fragment in window.split(b"\x00"):
        match = _HEADER_FIELD_RE.search(fragment.decode("ascii", errors="ignore"))
        if match:
            header[match.group(1)] = match.group(2).strip()
    return header


def _locate_records(data: bytes, n_samples: int) -> tuple[int, int, int]:
    """Find where trace records start, validated end-to-end against the file.

    Returns (first_record_start, record_size, n_records). Raises ValueError
    if the file's actual layout doesn't match what a well-formed SPR file
    of this version should look like.
    """
    record_size = _RECORD_HEADER_LEN + n_samples * _SAMPLE_DTYPE.itemsize
    offsets = [m.start() for m in re.finditer(re.escape(_RECORD_HEADER_TAG), data)]
    if not offsets:
        raise ValueError("no SPR trace records found: LINE_ID record tag is not present")

    strides = {b - a for a, b in pairwise(offsets)}
    if strides - {record_size}:
        raise ValueError(
            f"SPR trace records are not uniformly spaced: expected stride {record_size}, "
            f"found {sorted(strides)}"
        )

    first_start = offsets[0] - _TAG_OFFSET_IN_RECORD
    if first_start < 0:
        raise ValueError("SPR file too short: first trace record would start before byte 0")

    span = len(data) - first_start
    if span % record_size != 0:
        raise ValueError(
            f"SPR file size inconsistent with record layout: {span} bytes of trace data "
            f"is not a multiple of the {record_size}-byte record size"
        )

    n_records = span // record_size
    if n_records != len(offsets):
        raise ValueError(
            f"SPR record count mismatch: {len(offsets)} LINE_ID tags found but the file "
            f"layout implies {n_records} records"
        )
    return first_start, record_size, n_records


def parse_spr(path: Path) -> ScanFrame:
    path = Path(path)
    data = path.read_bytes()
    header = _parse_text_header(data)

    file_version = header.get("SPR_FILE_VERSION")
    if file_version != _SUPPORTED_FILE_VERSION:
        raise ValueError(
            f"unsupported SPR_FILE_VERSION {file_version!r} in {path} — only version "
            f"{_SUPPORTED_FILE_VERSION!r} has been reverse-engineered and validated"
        )

    n_samples = int(header["SPR_SAMPLES_PER_SCAN"])
    first_start, record_size, n_records = _locate_records(data, n_samples)

    traces = np.empty((n_records, n_samples), dtype=_SAMPLE_DTYPE)
    for i in range(n_records):
        record = data[first_start + i * record_size : first_start + (i + 1) * record_size]
        header_len = struct.unpack_from("<H", record, 2)[0]
        if header_len != _RECORD_HEADER_LEN:
            raise ValueError(
                f"record {i} declares header length {header_len}, expected {_RECORD_HEADER_LEN} "
                "— SPR record layout assumption violated, refusing to guess"
            )
        traces[i] = np.frombuffer(record, dtype=_SAMPLE_DTYPE, count=n_samples, offset=_RECORD_HEADER_LEN)

    sampling_interval_ps = float(header["SPR_SAMPLING_INTERVAL"])
    dielectric = header.get("SPR_MEDIUM_DIELECTRIC")

    return ScanFrame(
        source_type="spr_file",
        provenance={
            "path": str(path),
            "channel_num": header.get("SPR_CHANNEL_NUM"),
            "line_id": header.get("LINE_ID"),
            "antenna_type_detected": header.get("ANTENNA_TYPE_DETECTED"),
            "wheel_circumference_mm": header.get("WHEEL_CIRCUMFERENCE_MM"),
            "raw_header": header,
        },
        traces=traces,
        image=None,
        position=None,
        position_source="unknown",
        antenna_freq_mhz=None,
        sample_interval_ns=sampling_interval_ps / 1000.0,
        dielectric_assumed=float(dielectric) if dielectric is not None else None,
    )


register("rad", "ra1", "ra2")(parse_spr)
