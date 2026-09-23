"""Phase-1 (box-only, no class) annotation storage.

Two-phase labeling: this stores just localization (where is a pattern?),
one boxes.json per job under annotations/ — deliberately separate from
Dataset/, which stays pure vendor data. Class assignment (what is it?) is
phase 2, once the company confirms ground truth, and isn't modeled here at
all — a Box has no class field on purpose.

Coordinates are in native (trace_index, sample_index) units for the named
channel, matching `studio.session.ChannelInfo`'s n_traces/n_samples — not
display pixels — so boxes stay valid across any future change to display
resolution.

Lives in `core/` rather than `scripts/`: `scripts/` is not a declared package in
pyproject.toml and has no `__init__.py`, so anything importing it breaks the
moment the project is installed rather than run from the repo root. The Studio
depends on this store, and the Studio ships.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from core.annotation_io import ANNOTATION_WRITE_LOCK, safe_job_dir, write_text_atomically

_ANNOTATIONS_ROOT = Path("annotations")

# Reserved: a note starting with this marks a machine-written box with no structured
# provenance, and `replace_detector_boxes` purges those. `add_box` refuses it so an operator's
# free-typed note can never collide with it and get their own box deleted.
_RESERVED_NOTE_PREFIX = "auto:"


@dataclass(frozen=True)
class Box:
    """One stored box, and — if a machine drew it — which machine, and in which run.

    The provenance fields are `None` for a box a person drew, and also for any box stored
    before these fields existed. Those two cases are not the same thing, but neither can be
    distinguished from the other in a legacy file, which is exactly why they were added: see
    `replace_detector_boxes`.
    """

    id: str
    channel: str
    x: float
    y: float
    w: float
    h: float
    note: str = ""

    # Stable name of the detector that produced this box, e.g. "energy-envelope-candidates".
    # `None` means a person drew it. Replacement is keyed on this, not on the version, so
    # re-running a detector after changing it still replaces its own earlier output.
    detector: str | None = None
    # What that detector was when it ran — a version string and/or commit. Provenance only:
    # two versions of the same detector must never coexist in the file, so this never affects
    # which boxes are replaced.
    detector_version: str | None = None
    # Which run wrote it. Lets a reader tell "found twice by two runs" from "found once".
    run_id: str | None = None


@dataclass(frozen=True)
class DetectedBox:
    """One box a detector found, before it is given an id and provenance."""

    channel: str
    x: float
    y: float
    w: float
    h: float
    note: str = ""


def boxes_path(job_name: str) -> Path:
    """Public so callers can check this file's mtime (e.g. studio/diagnose.py's cache)
    without reaching into this module's storage layout."""
    return safe_job_dir(_ANNOTATIONS_ROOT, job_name) / "boxes.json"


def load_boxes(job_name: str) -> list[Box]:
    path = boxes_path(job_name)
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [Box(**b) for b in data["boxes"]]


def _save(job_name: str, boxes: list[Box]) -> None:
    write_text_atomically(boxes_path(job_name), json.dumps({"boxes": [asdict(b) for b in boxes]}, indent=2))


def add_box(job_name: str, *, channel: str, x: float, y: float, w: float, h: float, note: str = "") -> Box:
    """Store one box a person drew. Detector output goes through `replace_detector_boxes`.

    Refuses a note starting with `_RESERVED_NOTE_PREFIX`: that prefix marks legacy machine
    output, and `replace_detector_boxes` deletes boxes carrying it. The Studio passes an
    operator's free text straight here, so without this guard a note like "auto: same duct as
    before" would silently destroy that box on the next detector run.
    """
    if w <= 0 or h <= 0:
        raise ValueError(f"box width/height must be positive, got w={w} h={h}")
    if note.strip().lower().startswith(_RESERVED_NOTE_PREFIX):
        raise ValueError(
            f"a note may not start with {_RESERVED_NOTE_PREFIX!r} — that prefix is reserved for "
            "machine-written boxes, and a detector run deletes boxes carrying it"
        )
    box = Box(id=uuid.uuid4().hex[:8], channel=channel, x=x, y=y, w=w, h=h, note=note)
    with ANNOTATION_WRITE_LOCK:
        _save(job_name, [*load_boxes(job_name), box])
    return box


def _is_unattributed_auto_box(box: Box) -> bool:
    """A machine-written box with no detector recorded — legacy output that cannot be trusted.

    Before provenance existed, detector output was written through `add_box` with an "auto:"
    note. Such a box cannot be attributed to any detector, so it cannot be told apart from the
    current detector's own earlier output. Keeping it across a run is what produced the union
    this module now refuses to build.
    """
    return box.detector is None and box.note.strip().lower().startswith(_RESERVED_NOTE_PREFIX)


def replace_detector_boxes(
    job_name: str,
    *,
    detector: str,
    detector_version: str,
    boxes: Sequence[DetectedBox],
) -> list[Box]:
    """Store one detector run's output, replacing whatever that detector stored before.

    **Replaces, never appends.** The previous behaviour — append anything whose rounded
    coordinates were not already present — silently accumulated every detector version ever
    run: `boxes.json` held 233 boxes where the then-current detector found 166, with 67
    unreproducible and written by a version already known to be wrong. Every downstream number
    (the credibility funnel, everything published from it) was computed over that union.

    What survives a run:
      - boxes from *other* detectors, keyed by name — not this run's business,
      - boxes a person drew (no detector, no "auto:" note) — never touched by a machine.

    What does not:
      - this detector's previous output, whatever version wrote it,
      - legacy "auto:" boxes with no detector recorded (see `_is_unattributed_auto_box`).

    An empty `boxes` clears this detector's previous output rather than leaving it in place: a
    run that finds nothing is a real answer, and the file must not keep reporting findings the
    current detector does not stand behind.
    """
    for box in boxes:
        if box.w <= 0 or box.h <= 0:
            raise ValueError(f"box width/height must be positive, got w={box.w} h={box.h}")

    run_id = uuid.uuid4().hex[:12]
    with ANNOTATION_WRITE_LOCK:
        kept = [
            b
            for b in load_boxes(job_name)
            if b.detector != detector and not _is_unattributed_auto_box(b)
        ]
        written = [
            Box(
                id=uuid.uuid4().hex[:8],
                channel=b.channel,
                x=b.x,
                y=b.y,
                w=b.w,
                h=b.h,
                note=b.note,
                detector=detector,
                detector_version=detector_version,
                run_id=run_id,
            )
            for b in boxes
        ]
        _save(job_name, [*kept, *written])
    return written


def delete_box(job_name: str, box_id: str) -> bool:
    with ANNOTATION_WRITE_LOCK:
        boxes = load_boxes(job_name)
        remaining = [b for b in boxes if b.id != box_id]
        if len(remaining) == len(boxes):
            return False
        _save(job_name, remaining)
        return True
