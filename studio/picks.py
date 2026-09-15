"""Persistent store for interpreted targets — the interpreter's output, per job.

Deliberately separate from `scripts/box_store.py`, which holds *candidate*
boxes (Phase 1: "something is here", no claim about what). A pick is the next
step: a human has looked at a target, measured it, and is willing to put a
depth on it. Mixing the two would quietly turn candidates into conclusions.

A pick records how its depth was arrived at, not just the number:

- `velocity_source="fitted"` — velocity measured from this target's own
  hyperbola curvature. The depth is as good as this software can produce.
- `velocity_source="manual"` — operator dialled the velocity in by eye.
- `velocity_source="assumed"` — the file header's `SPR_MEDIUM_DIELECTRIC`,
  which is a site setting, not a measurement.

The same three-way honesty as `Evidence`'s confidence labels in
`core/contracts.py`: the number and its provenance travel together, so a depth
can never be read without seeing where its velocity came from.

Storage is one JSON file per job under `annotations/`, matching `box_store`'s
layout, so everything a human contributed about a job lives in one folder.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from core.annotation_io import ANNOTATION_WRITE_LOCK, safe_job_dir, write_text_atomically

_ANNOTATIONS_ROOT = Path("annotations")

VelocitySource = Literal["fitted", "manual", "assumed"]
_VELOCITY_SOURCES = ("fitted", "manual", "assumed")


@dataclass(frozen=True)
class Pick:
    """One interpreted target. Frozen: edits replace the record, never mutate it."""

    id: str
    channel: str
    trace: float  # native trace index along the line
    sample: float  # native sample index down the trace
    time_ns: float  # two-way time at the apex
    depth_m: float
    velocity_m_per_ns: float
    velocity_source: VelocitySource
    dielectric: float
    label: str  # free text — NOT the 9-class taxonomy; the company's ground truth decides that
    note: str
    fit_r2: float | None  # None unless velocity_source == "fitted"
    created_at: str


def _picks_path(job_name: str) -> Path:
    return safe_job_dir(_ANNOTATIONS_ROOT, job_name) / "picks.json"


def load_picks(job_name: str) -> list[Pick]:
    """Every pick on a job, oldest first. A job with no picks yet is not an error."""
    path = _picks_path(job_name)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
        return [Pick(**entry) for entry in payload["picks"]]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        # Overwriting a corrupt file would destroy an interpreter's work
        # silently; refuse and let a human look at it.
        raise ValueError(f"corrupt pick file {path}: {exc}") from exc


def _save(job_name: str, picks: list[Pick]) -> None:
    write_text_atomically(_picks_path(job_name), json.dumps({"picks": [asdict(p) for p in picks]}, indent=2) + "\n")


def add_pick(
    job_name: str,
    *,
    channel: str,
    trace: float,
    sample: float,
    time_ns: float,
    depth_m: float,
    velocity_m_per_ns: float,
    velocity_source: str,
    dielectric: float,
    label: str = "",
    note: str = "",
    fit_r2: float | None = None,
) -> Pick:
    """Record one target. Returns the stored pick, including its generated id."""
    if velocity_source not in _VELOCITY_SOURCES:
        raise ValueError(f"velocity_source must be one of {_VELOCITY_SOURCES}, got {velocity_source!r}")
    if velocity_m_per_ns <= 0:
        raise ValueError(f"velocity must be positive, got {velocity_m_per_ns}")
    if velocity_source == "fitted" and fit_r2 is None:
        raise ValueError("a fitted velocity must carry the fit's r2 — otherwise it isn't checkable")
    if velocity_source != "fitted" and fit_r2 is not None:
        raise ValueError(f"fit_r2 is meaningless for velocity_source={velocity_source!r}")

    pick = Pick(
        id=uuid.uuid4().hex[:12],
        channel=channel,
        trace=float(trace),
        sample=float(sample),
        time_ns=float(time_ns),
        depth_m=float(depth_m),
        velocity_m_per_ns=float(velocity_m_per_ns),
        velocity_source=velocity_source,  # type: ignore[arg-type]
        dielectric=float(dielectric),
        label=label,
        note=note,
        fit_r2=None if fit_r2 is None else float(fit_r2),
        created_at=datetime.now(UTC).isoformat(),
    )
    with ANNOTATION_WRITE_LOCK:
        _save(job_name, [*load_picks(job_name), pick])
    return pick


def delete_pick(job_name: str, pick_id: str) -> bool:
    """Remove one pick. False if there was no such pick — the caller decides if that's a 404."""
    with ANNOTATION_WRITE_LOCK:
        picks = load_picks(job_name)
        remaining = [p for p in picks if p.id != pick_id]
        if len(remaining) == len(picks):
            return False
        _save(job_name, remaining)
        return True


def to_csv_rows(picks: list[Pick]) -> list[list[str]]:
    """Flatten picks for export, header row first.

    Velocity source rides along as its own column on purpose: a target list
    that leaves the site without saying which depths were measured and which
    were assumed is the exact failure mode this project exists to avoid.
    """
    header = [
        "id", "channel", "trace", "sample", "time_ns", "depth_m",
        "velocity_m_per_ns", "velocity_source", "dielectric", "fit_r2",
        "label", "note", "created_at",
    ]
    rows = [header]
    for p in picks:
        rows.append([
            p.id, p.channel, f"{p.trace:.2f}", f"{p.sample:.2f}", f"{p.time_ns:.3f}",
            f"{p.depth_m:.3f}", f"{p.velocity_m_per_ns:.5f}", p.velocity_source,
            f"{p.dielectric:.2f}", "" if p.fit_r2 is None else f"{p.fit_r2:.3f}",
            p.label, p.note, p.created_at,
        ])
    return rows
