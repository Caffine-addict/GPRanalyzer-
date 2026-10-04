"""Tests for parsers/spr_tags.py — operator target tags beside SPRScan lines."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from parsers.spr_tags import read_tags

_CLASS_ID = struct.pack("<i", 4)


def _tag_file(path: Path, tags: list[tuple[int, int, str, str]]) -> Path:
    """The record shapes the SPR software writes: a Point class, its repeats, then the tag strings."""
    (x0, y0, *_), *rest = tags
    data = b"\x00" * 8 + b"\x09" + struct.pack("<i", 2) + b"\x09" + struct.pack("<i", 3)
    data += b"\x10" + struct.pack("<i", 2) + struct.pack("<i", len(tags))  # the Keys array and its length
    data += b"\x05" + _CLASS_ID + b"\x14System.Drawing.Point" + struct.pack("<i", 2)
    data += b"\x01x\x01y\x00\x00\x08\x08" + struct.pack("<i", 10) + struct.pack("<ii", x0, y0)
    for k, (x, y, *_) in enumerate(rest):
        data += b"\x01" + struct.pack("<i", 5 + k) + _CLASS_ID + struct.pack("<ii", x, y)
    data += b"\x0b\x16USRCore.TagSurveyPoint"
    for k, (*_, tag_id, description) in enumerate(tags):
        for j, text in enumerate((tag_id, description)):
            data += b"\x06" + struct.pack("<i", 20 + 2 * k + j) + bytes([len(text)]) + text.encode()
    path.write_bytes(data)
    return path


def test_each_tag_gets_its_point_in_order(tmp_path: Path) -> None:
    tags = read_tags(_tag_file(tmp_path / "a.tag", [(80, 32, "A46", "u"), (227, 189, "A45", "u")]), n_samples=256)
    assert [(t.tag_id, t.trace, t.sample, t.description) for t in tags] == [("A46", 80, 32, "u"), ("A45", 227, 189, "u")]


def test_a_point_past_the_last_sample_is_kept_but_flagged(tmp_path: Path) -> None:
    (tag,) = read_tags(_tag_file(tmp_path / "a.tag", [(27, 262, "A47", "u")]), n_samples=256)
    assert tag.sample == 262 and not tag.in_record


def test_a_file_whose_points_and_strings_do_not_pair_is_refused(tmp_path: Path) -> None:
    path = _tag_file(tmp_path / "a.tag", [(80, 32, "A46", "u"), (10, 10, "A47", "u")])
    path.write_bytes(path.read_bytes()[:-8])  # cut the last tag's strings
    with pytest.raises(ValueError, match="cannot pair"):
        read_tags(path, n_samples=256)


def test_a_count_that_disagrees_with_the_declared_array_is_refused(tmp_path: Path) -> None:
    path = _tag_file(tmp_path / "a.tag", [(80, 32, "A46", "u")])
    data = bytearray(path.read_bytes())
    data[18:22] = struct.pack("<i", 2)  # the file now declares two tags but holds one
    path.write_bytes(bytes(data))
    with pytest.raises(ValueError, match="cannot pair"):
        read_tags(path, n_samples=256)


def test_a_file_with_no_tags_reads_as_none(tmp_path: Path) -> None:
    (tmp_path / "empty.tag").write_bytes(b"\x00" * 64)
    assert read_tags(tmp_path / "empty.tag", n_samples=256) == []


_REAL = Path("Dataset/incoming/04102026_GPRFiles/GPR_Set5/Single-02.tag")


@pytest.mark.skipif(not _REAL.exists(), reason="company tag files not present")
def test_a_real_tag_file() -> None:
    tags = read_tags(_REAL, n_samples=256)
    assert [(t.tag_id, t.trace, t.sample) for t in tags] == [("A47", 27, 262), ("A45", 227, 189), ("A46", 80, 32)]
