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
from dataclasses import asdict, dataclass
from pathlib import Path

from core.annotation_io import ANNOTATION_WRITE_LOCK, safe_job_dir, write_text_atomically

_ANNOTATIONS_ROOT = Path("annotations")


@dataclass(frozen=True)
class Box:
    id: str
    channel: str
    x: float
    y: float
    w: float
    h: float
    note: str = ""


def _boxes_path(job_name: str) -> Path:
    return safe_job_dir(_ANNOTATIONS_ROOT, job_name) / "boxes.json"


def load_boxes(job_name: str) -> list[Box]:
    path = _boxes_path(job_name)
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [Box(**b) for b in data["boxes"]]


def _save(job_name: str, boxes: list[Box]) -> None:
    write_text_atomically(_boxes_path(job_name), json.dumps({"boxes": [asdict(b) for b in boxes]}, indent=2))


def add_box(job_name: str, *, channel: str, x: float, y: float, w: float, h: float, note: str = "") -> Box:
    if w <= 0 or h <= 0:
        raise ValueError(f"box width/height must be positive, got w={w} h={h}")
    box = Box(id=uuid.uuid4().hex[:8], channel=channel, x=x, y=y, w=w, h=h, note=note)
    with ANNOTATION_WRITE_LOCK:
        _save(job_name, [*load_boxes(job_name), box])
    return box


def delete_box(job_name: str, box_id: str) -> bool:
    with ANNOTATION_WRITE_LOCK:
        boxes = load_boxes(job_name)
        remaining = [b for b in boxes if b.id != box_id]
        if len(remaining) == len(boxes):
            return False
        _save(job_name, remaining)
        return True
