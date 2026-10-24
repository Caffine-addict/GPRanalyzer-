"""Removing a radar line from the project, recoverably.

A removed line's folder (its radar files) and its annotations (picks, claims, candidate boxes,
georeference) move together into `<dataset parent>/removed_lines/<job>__<UTC time>/`, with a note
of who removed it and when. Nothing is deleted: `restore` puts both back where they were. Emptying
`removed_lines` for good is left as a deliberate step outside the app.

Survey data cannot be re-recorded, so a click in the browser must never be able to destroy it.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.annotation_io import _LOCK_FILE_NAME, job_lock, safe_job_dir
from studio import picks as pick_store
from studio import session

REMOVED_DIR = "removed_lines"
_MANIFEST = "removed.json"
_ENTRY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*__\d{8}T\d{6}Z$")


def _root(dataset_dir: Path) -> Path:
    return dataset_dir.parent / REMOVED_DIR


def _annotations(job: str) -> Path:
    return safe_job_dir(pick_store._ANNOTATIONS_ROOT, job)


def remove(dataset_dir: Path, job: str, removed_by: str) -> dict[str, Any]:
    """Move a line and its annotations out of the project. Raises JobNotFoundError for an unknown line."""
    job_dir = session.resolve_job(job, dataset_dir)  # only a line actually in the project
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    entry = _root(dataset_dir) / f"{job}__{stamp}"
    annotations = _annotations(job)
    had_annotations = annotations.exists()
    entry.mkdir(parents=True)  # a second removal of the same name in the same second fails here, moving nothing
    with job_lock(annotations):  # no pick or claim write lands halfway through the move
        shutil.move(str(job_dir), str(entry / "line"))
        try:
            if had_annotations:
                shutil.move(str(annotations), str(entry / "annotations"))
        except OSError:
            shutil.move(str(entry / "line"), str(job_dir))  # put the radar files back: all or nothing
            entry.rmdir()
            raise
    if not had_annotations:  # job_lock made the folder for its lock file: tidy only that, never anything else
        (annotations / _LOCK_FILE_NAME).unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            annotations.rmdir()  # refused if anything else appeared in it meanwhile
    manifest = {"id": entry.name, "job": job, "removed_by": removed_by, "removed_at": stamp,
                "had_annotations": had_annotations}
    (entry / _MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def removed(dataset_dir: Path) -> list[dict[str, Any]]:
    """Every removed line that can be restored, newest first."""
    root = _root(dataset_dir)
    if not root.exists():
        return []
    found = []
    for entry in root.iterdir():
        manifest = entry / _MANIFEST
        if _ENTRY.match(entry.name) and manifest.exists():
            found.append(json.loads(manifest.read_text()))
    return sorted(found, key=lambda m: m["removed_at"], reverse=True)


def restore(dataset_dir: Path, entry_id: str) -> dict[str, Any]:
    """Put a removed line and its annotations back. Refuses if a line of that name exists again."""
    manifest = next((m for m in removed(dataset_dir) if m["id"] == entry_id), None)
    if manifest is None:
        raise KeyError(entry_id)
    job = manifest["job"]
    entry = _root(dataset_dir) / entry_id
    target = safe_job_dir(dataset_dir, job)
    annotations = _annotations(job)
    if target.exists() or (manifest["had_annotations"] and annotations.exists()):
        raise ValueError(f"a line named {job} is in the project again; rename or remove it first")
    shutil.move(str(entry / "line"), str(target))
    if manifest["had_annotations"]:
        annotations.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(entry / "annotations"), str(annotations))
    (entry / _MANIFEST).unlink()
    entry.rmdir()
    return manifest
