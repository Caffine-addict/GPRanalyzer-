"""Run the shape diagnosis over every candidate box in a job, and persist the result.

The measurement itself lives in `detect/hyperbola.py`, which touches no files. This module is
the job-level driver: it reads boxes from `core/boxes.py`, loads each channel through
`studio/session.py`, and writes `annotations/<job>/diagnoses.json` keyed by box id.

It lives in `studio/` rather than `detect/` on purpose. Loading a channel means
`studio.session`, and `studio/velocity.py` imports the fitters from `detect.hyperbola` — putting
the driver in `detect/` would make the two packages import each other. Same reasoning that
moved `hyperbola_half_aperture_m` into `detect/measure.py`.

Previously `scripts/diagnose_candidates.py`, which reached into `scripts/` for both the box
store and the channel loader and needed a `sys.path.insert` at call time to do it. `scripts/`
is not a declared package, so that broke on any installed copy; the loaders it wanted
(`CHANNEL_ORDER`, `load_frame`) already existed in `studio/session.py`.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from core import boxes as box_store
from core.annotation_io import safe_job_dir, write_text_atomically
from detect.hyperbola import Diagnosis, diagnose_box
from detect.measure import compute_normalized_envelope
from studio import session

_ANNOTATIONS_ROOT = Path("annotations")


def _diagnoses_path(job_name: str) -> Path:
    return safe_job_dir(_ANNOTATIONS_ROOT, job_name) / "diagnoses.json"


def _cached_diagnoses(job_name: str) -> list[Diagnosis] | None:
    """The last-written diagnoses, if they're at least as new as boxes.json — None otherwise.

    `list_candidates` (studio/candidates.py) calls diagnose_job on every `/candidates` request
    with no cache, so a job with real candidate counts (measured: ~23ms/box, ~1.5s on a 64-box
    job) re-runs the RANSAC fit on every click. This makes reopening the same, unchanged job
    read the file diagnose_job already writes instead of recomputing. A malformed or
    outdated-schema cache file falls back to recomputing rather than raising — this is a speed
    path, not a correctness one.
    """
    diagnoses_path = _diagnoses_path(job_name)
    boxes_file = box_store.boxes_path(job_name)
    if not diagnoses_path.exists():
        return None
    if boxes_file.exists() and boxes_file.stat().st_mtime > diagnoses_path.stat().st_mtime:
        return None  # a box was added/changed since the last diagnosis run
    try:
        raw = json.loads(diagnoses_path.read_text(encoding="utf-8"))
        return [Diagnosis(**d) for d in raw]
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def diagnose_job(job_dir: Path, *, force: bool = False) -> list[Diagnosis]:
    """Diagnose every box in `job_dir`'s job, write the file, and return the diagnoses.

    A box whose channel is missing from the job, or whose channel carries no traces, is
    skipped rather than guessed at — the box is still a real region someone flagged, and
    `studio/candidates.py` keeps returning it with its diagnostic fields empty.

    Reads a cached result when `boxes.json` hasn't changed since the last run — pass
    `force=True` to always recompute (e.g. a CLI re-run after changing the measurement code
    itself, where the box set is unchanged but the diagnosis logic isn't).
    """
    job_name = job_dir.name
    if not force:
        cached = _cached_diagnoses(job_name)
        if cached is not None:
            return cached
    boxes = box_store.load_boxes(job_name)

    frames_by_channel = {}
    for extension, _label in session.CHANNEL_ORDER:
        # `session.load_frame` raises JobNotFoundError for a channel this job does not have and
        # ValueError for one that parsed without traces. Both mean "nothing measurable here",
        # which is ordinary for a job recorded on fewer than three channels — not an error. An
        # earlier version guarded with `channel_path(...).exists()`, but channel_path raises on a
        # missing file itself, so the guard never ran and the whole job aborted instead.
        try:
            frames_by_channel[extension] = session.load_frame(job_dir, extension)
        except (session.JobNotFoundError, ValueError):
            continue

    diagnoses: list[Diagnosis] = []
    for box in boxes:
        frame = frames_by_channel.get(box.channel)
        if frame is None or frame.traces is None or frame.sample_interval_ns is None:
            continue
        normalized = compute_normalized_envelope(frame.traces, frame.sample_interval_ns)
        header = frame.provenance.get("raw_header", {})
        shaft_interval_m = (
            float(header["SPR_SHAFT_INTERVAL"]) if "SPR_SHAFT_INTERVAL" in header else None
        )
        diagnoses.append(
            diagnose_box(
                frame.traces,
                normalized,
                box.id,
                int(box.x),
                int(box.y),
                int(box.w),
                int(box.h),
                shaft_interval_m,
                frame.sample_interval_ns,
            )
        )

    write_text_atomically(
        _diagnoses_path(job_name), json.dumps([asdict(d) for d in diagnoses], indent=2)
    )
    return diagnoses
