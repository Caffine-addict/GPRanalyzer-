"""Tests for the SPR (.RAD/.RA1/.RA2) parser — Subsurface Imaging Systems SPRScan 3D format.

Fixture bytes replicate the real per-record header layout measured from 4
real survey files (SPR_FILE_VERSION 7): a fixed 64-byte record header whose
own offset-2 field declares its length, followed by int16 LE sample data.
Real customer survey data never enters the repo — this fixture is built
from scratch with known sample values so assertions can pin exact output,
not just shape/not-None.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

import parsers.spr  # noqa: F401 - registers the "rad"/"ra1"/"ra2" parsers as a side effect
from parsers.base import get_parser, registered_extensions

# Real 64-byte record header template, byte-for-byte from a real Job_0703
# capture: 32 bytes of fixed fields (offset 2-3 = 0x0040 = header length),
# then tag 0x000d + "LINE_ID 01\0", tag 0x0010 + "SPR_MARKER 00\0", padding.
_RECORD_HEADER = bytes(
    [0x22, 0x44, 0x40, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00]
    + [0x00] * 16
    + [0x0D, 0x00]
    + list(b"LINE_ID 01")
    + [0x00, 0x10, 0x00]
    + list(b"SPR_MARKER 00")
    + [0x00, 0x00, 0x00, 0x00]
)
assert len(_RECORD_HEADER) == 64

def _encode_header_field(text: str) -> bytes:
    """Real header fields are [uint16 LE length][ascii text][null terminator],
    not newline-separated lines — confirmed against real files, where
    `strings`-style tools misleadingly render them as clean text lines."""
    payload = text.encode("ascii") + b"\x00"
    return struct.pack("<H", len(payload)) + payload


_TEXT_HEADER = b"".join(
    _encode_header_field(line)
    for line in (
        "ACQUISITION_DATE 12/24/22",
        "ACQUISITION_TIME 13:07:20",
        "SPR_FILE_VERSION 7",
        "SPR_SAMPLES_PER_SCAN 4",
        "SPR_SAMPLING_INTERVAL 100",
        "SPR_MEDIUM_DIELECTRIC 09.00",
        "SPR_CHANNEL_NUM 0",
        "ANTENNA_TYPE_DETECTED eQUANTUM",
        "WHEEL_CIRCUMFERENCE_MM 1276.7",
        "LINE_ID 01",
    )
)


def _build_spr_file(
    traces: np.ndarray,
    *,
    preamble_size: int = 137,
    text_header: bytes = _TEXT_HEADER,
    record_header: bytes = _RECORD_HEADER,
    corrupt_header_len: bool = False,
) -> bytes:
    """Build a synthetic SPR file: opaque preamble + text header + N trace records.

    Real files carry a large (~64KB) opaque binary block before the ASCII
    header, of fixed size regardless of survey length — preamble_size here
    stands in for that at a much smaller scale so tests stay fast, and is
    varied to prove the parser locates the header/records by content, not
    by a hardcoded offset.
    """
    body = b"\x00" * preamble_size + text_header
    for row in traces:
        header = bytearray(record_header)
        if corrupt_header_len:
            struct.pack_into("<H", header, 2, 999)
        body += bytes(header) + row.astype("<i2").tobytes()
    return body


def test_spr_parsers_registered_for_expected_extensions() -> None:
    exts = registered_extensions()
    for ext in ("rad", "ra1", "ra2"):
        assert ext in exts


def test_parse_spr_extracts_exact_trace_values(tmp_path: Path) -> None:
    traces = np.array([[10, -20, 30, -40], [11, -21, 31, -41], [12, -22, 32, -42]], dtype="<i2")
    path = tmp_path / "Single-01.RAD"
    path.write_bytes(_build_spr_file(traces))

    frame = get_parser(path)(path)

    assert frame.traces is not None
    assert frame.traces.shape == (3, 4)
    np.testing.assert_array_equal(frame.traces, traces)


def test_parse_spr_populates_calibration_fields_from_header(tmp_path: Path) -> None:
    traces = np.zeros((2, 4), dtype="<i2")
    path = tmp_path / "Single-01.RA1"
    path.write_bytes(_build_spr_file(traces))

    frame = get_parser(path)(path)

    assert frame.sample_interval_ns == pytest.approx(0.1)  # 100 ps -> 0.1 ns
    assert frame.dielectric_assumed == pytest.approx(9.0)
    assert frame.antenna_freq_mhz is None


def test_parse_spr_is_honest_about_position(tmp_path: Path) -> None:
    traces = np.zeros((1, 4), dtype="<i2")
    path = tmp_path / "Single-01.RA2"
    path.write_bytes(_build_spr_file(traces))

    frame = get_parser(path)(path)

    assert frame.position is None
    assert frame.position_source == "unknown"
    assert frame.image is None


def test_parse_spr_records_provenance(tmp_path: Path) -> None:
    traces = np.zeros((1, 4), dtype="<i2")
    path = tmp_path / "Single-01.RAD"
    path.write_bytes(_build_spr_file(traces))

    frame = get_parser(path)(path)

    assert frame.source_type == "spr_file"
    assert frame.provenance["path"] == str(path)
    assert frame.provenance["channel_num"] == "0"
    assert frame.provenance["raw_header"]["SPR_FILE_VERSION"] == "7"


def test_parse_spr_ignores_leading_preamble_of_any_size(tmp_path: Path) -> None:
    """The real format has a large fixed-size opaque preamble before the first
    trace record — the parser must locate records by their own structure,
    not by assuming a specific preamble length."""
    traces = np.array([[1, 2, 3, 4]], dtype="<i2")
    for preamble_size in (0, 1, 5000):
        path = tmp_path / f"preamble_{preamble_size}.RAD"
        path.write_bytes(_build_spr_file(traces, preamble_size=preamble_size))

        frame = get_parser(path)(path)
        np.testing.assert_array_equal(frame.traces, traces)


def test_parse_spr_rejects_file_with_no_text_header(tmp_path: Path) -> None:
    path = tmp_path / "Single-01.RAD"
    path.write_bytes(b"\x00" * 500)

    with pytest.raises(ValueError, match="text header not found"):
        get_parser(path)(path)


def test_parse_spr_rejects_unsupported_file_version(tmp_path: Path) -> None:
    text_header = _TEXT_HEADER.replace(b"SPR_FILE_VERSION 7", b"SPR_FILE_VERSION 9")
    traces = np.zeros((1, 4), dtype="<i2")
    path = tmp_path / "Single-01.RAD"
    path.write_bytes(_build_spr_file(traces, text_header=text_header))

    with pytest.raises(ValueError, match="SPR_FILE_VERSION"):
        get_parser(path)(path)


def test_parse_spr_rejects_corrupted_record_header_length(tmp_path: Path) -> None:
    """A record whose own self-declared header length disagrees with the
    known-good value must fail loudly, not silently mis-slice sample data."""
    traces = np.zeros((2, 4), dtype="<i2")
    path = tmp_path / "Single-01.RAD"
    path.write_bytes(_build_spr_file(traces, corrupt_header_len=True))

    with pytest.raises(ValueError, match="header length"):
        get_parser(path)(path)


def test_parse_spr_rejects_record_tag_too_close_to_start_of_file(tmp_path: Path) -> None:
    """A single tag match near byte 0 implies a record starting before the
    file even begins — nonsensical, must fail rather than wrap/clip."""
    from parsers.spr import _RECORD_HEADER_TAG

    data = _RECORD_HEADER_TAG + b"\x00" * 5 + _TEXT_HEADER + b"\x00" * 100
    path = tmp_path / "Single-01.RAD"
    path.write_bytes(data)

    with pytest.raises(ValueError, match="too short"):
        get_parser(path)(path)


def test_parse_spr_rejects_file_with_no_trace_records(tmp_path: Path) -> None:
    path = tmp_path / "Single-01.RAD"
    path.write_bytes(_TEXT_HEADER + b"\x00" * 200)  # no record tag anywhere

    with pytest.raises(ValueError, match="no SPR trace records found"):
        get_parser(path)(path)


def test_parse_spr_rejects_truncated_trailing_record(tmp_path: Path) -> None:
    """One well-formed record plus a partial (cut-off) second one."""
    traces = np.zeros((1, 4), dtype="<i2")
    good = _build_spr_file(traces)
    truncated = good + _RECORD_HEADER[:10]  # a lone partial record header, no full body
    path = tmp_path / "Single-01.RAD"
    path.write_bytes(truncated)

    with pytest.raises(ValueError, match="not a multiple of the"):
        get_parser(path)(path)


def test_parse_spr_rejects_record_count_mismatch(tmp_path: Path) -> None:
    """Trailing space exactly one record long but containing no tag at all:
    stride checks between real records still pass, but the implied record
    count from file size no longer matches the number of tags actually found."""
    traces = np.zeros((2, 4), dtype="<i2")
    good = _build_spr_file(traces)
    record_size = len(_RECORD_HEADER) + 4 * 2
    padded = good + b"\x00" * record_size
    path = tmp_path / "Single-01.RAD"
    path.write_bytes(padded)

    with pytest.raises(ValueError, match="record count mismatch"):
        get_parser(path)(path)


def test_parse_spr_rejects_non_uniform_record_spacing(tmp_path: Path) -> None:
    """Simulates truncation/corruption: one record record boundary shifted."""
    traces = np.zeros((3, 4), dtype="<i2")
    good = _build_spr_file(traces)
    # Splice one extra byte into the middle of the record stream, shifting
    # every record after it out of alignment with the expected stride.
    cut = len(_TEXT_HEADER) + 137 + 70
    corrupted = good[:cut] + b"\x00" + good[cut:]
    path = tmp_path / "Single-01.RAD"
    path.write_bytes(corrupted)

    with pytest.raises(ValueError, match="not uniformly spaced"):
        get_parser(path)(path)
