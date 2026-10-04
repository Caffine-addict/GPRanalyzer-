"""Read the target tags an operator marked on an SPRScan line (`Single-NN.tag` beside its channels).

The radar software saves them as a .NET BinaryFormatter `Hashtable` of `System.Drawing.Point` keys
to `USRCore.TagSurveyPoint` values (id such as "A46", a description, a colour, a timestamp). On the
delivered lines a tag's point is (trace, sample) on the radargram: the shallow tags sit exactly on
hyperbola apexes in all three channels (checked against GPR_Set3-5, 2026-10-04). Some tags lie past
the last sample; their y cannot be a sample index, so they are kept but marked `in_record=False`
rather than clipped onto the radargram.

This reads the format's fixed record shapes, not a general BinaryFormatter: the Point class record
and its `ClassWithId` repeats for the keys, and the length-prefixed strings for each tag's id and
description. A file whose counts do not pair up is refused rather than guessed at.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

_POINT_CLASS = b"\x14System.Drawing.Point"
_POINT_MEMBERS = b"\x01x\x01y\x00\x00\x08\x08"


@dataclass(frozen=True)
class Tag:
    trace: int
    sample: int
    tag_id: str
    description: str
    in_record: bool  # False when the point lies outside the line's samples (not a sample index there)


def _points(data: bytes) -> list[tuple[int, int]]:
    at = data.find(_POINT_CLASS)
    if at < 4:
        return []
    class_id = data[at - 4:at]
    members = data.find(_POINT_MEMBERS, at)
    if members < 0:
        raise ValueError("Point record without its x/y members")
    first = members + len(_POINT_MEMBERS) + 4  # + library id
    points = [struct.unpack_from("<ii", data, first)]
    repeat = re.compile(re.escape(b"\x01") + b"(....)" + re.escape(class_id) + b"(........)", re.DOTALL)
    points += [struct.unpack("<ii", m.group(2)) for m in repeat.finditer(data, first + 8)]
    return points


def _strings(data: bytes) -> list[str]:
    """BinaryObjectString records: 0x06, object id (4 bytes), length (1 byte), printable text."""
    found, i = [], 0
    while (i := data.find(b"\x06", i)) >= 0:
        if i + 6 <= len(data):
            length = data[i + 5]
            text = data[i + 6:i + 6 + length]
            if 0 < length < 64 and len(text) == length and all(32 <= c < 127 for c in text):
                found.append(text.decode("ascii"))
                i += 6 + length
                continue
        i += 1
    return found


def _declared_count(data: bytes) -> int | None:
    """Length of the Hashtable's Keys array, as the file itself declares it (ArraySingleObject 0x10)."""
    # The table's members end with references to its Keys and Values arrays (0x09 + id each),
    # followed at once by the Keys array record carrying that same id and its length.
    match = re.compile(rb"\x09(....)\x09(....)\x10\1(....)", re.DOTALL).search(data)
    return struct.unpack("<i", match.group(3))[0] if match else None


def read_tags(path: Path, n_samples: int) -> list[Tag]:
    data = Path(path).read_bytes()
    values_at = data.find(b"USRCore.TagSurveyPoint")
    if values_at < 0:
        return []
    try:
        points = _points(data[:values_at])  # keys are written before the values
    except struct.error as exc:
        raise ValueError(f"{path}: truncated tag points ({exc})") from exc
    strings = _strings(data[values_at:])
    declared = _declared_count(data)
    if not points or len(strings) != 2 * len(points) or declared != len(points):
        raise ValueError(f"{path}: {len(points)} tag points, {len(strings)} tag strings, {declared} declared "
                         "— cannot pair them")
    return [Tag(trace=x, sample=y, tag_id=strings[2 * k], description=strings[2 * k + 1], in_record=0 <= y < n_samples)
            for k, (x, y) in enumerate(points)]
