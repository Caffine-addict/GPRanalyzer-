"""Getting new files into the dataset from the Studio UI, instead of copying folders by hand.

Two kinds of upload, kept apart because they are different things:

- **A radar line** — a line's `.RAD/.RA1/.RA2` channels, optionally with its `.gps`/`.map`
  sidecars. It becomes a new job folder that opens like any delivered line. Every channel must
  actually parse before anything lands in the dataset, and the whole line is staged and moved in
  with one rename, so the job list never shows a half-written line.
- **Reference drawings** — deliverable sheets (PDF/DWG/images/archives). Stored and listed, never
  processed: Studio reads radar traces, not pictures of radargrams, so a drawing dropped into a
  job folder would just be invisible. They live in a sibling folder of the dataset, outside the
  job list.

File names from the browser are never trusted as paths. A line's files are renamed by extension
to the layout `studio/session.py` expects; a drawing keeps only its base name.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

from core.annotation_io import safe_job_dir
from parsers.spr import parse_spr
from studio.session import list_jobs

_CHANNELS = ("RAD", "RA1", "RA2")
REFERENCE_SUFFIXES = frozenset({".pdf", ".dwg", ".dxf", ".png", ".jpg", ".jpeg", ".rar", ".zip", ".csv"})

# ponytail: uploads are read whole into memory — fine for a local workstation and 300 MB
# archives; stream to disk if this ever serves several users at once.
Upload = tuple[str, bytes]


def _base_name(client_name: str) -> str:
    """The last path component of a browser-supplied name, whichever separator it used."""
    return client_name.replace("\\", "/").rsplit("/", 1)[-1].strip()


def _line_destination(client_name: str, job_name: str) -> str:
    suffix = Path(_base_name(client_name)).suffix[1:]
    if suffix.upper() in _CHANNELS:
        return f"Single-01.{suffix.upper()}"
    if suffix.lower() == "gps":
        return "Single-01.gps"
    if suffix.lower() == "map":
        return f"{job_name}.map"
    raise ValueError(
        f"{client_name}: not part of a radar line — expected .RAD, .RA1 or .RA2 (plus optional "
        ".gps/.map). Drawings and reports go in as reference drawings instead."
    )


def import_line(dataset_dir: Path, job_name: str, files: Sequence[Upload]) -> list[str]:
    """Add one radar line as a new job. Returns the stored file names, sorted.

    All-or-nothing: raises ValueError, leaving the dataset untouched, if the name is unsafe or
    taken, a file is not part of a line, two files claim the same channel, no channel is present,
    or any channel fails to parse.
    """
    target = safe_job_dir(dataset_dir, job_name)
    if target.exists():
        raise ValueError(f"a job called {job_name!r} already exists — choose another name")

    staged: dict[str, Upload] = {}
    for client_name, data in files:
        destination = _line_destination(client_name, job_name)
        if destination in staged:
            raise ValueError(f"{staged[destination][0]} and {client_name} both map to {destination}")
        if not data:
            raise ValueError(f"{client_name} is empty")
        staged[destination] = (client_name, data)
    if not any(name.startswith("Single-01.R") for name in staged):
        raise ValueError("no radar channel in this upload — a line needs at least one .RAD, .RA1 or .RA2")

    # Staged beside the dataset, not inside it: list_jobs scans the dataset folder, and a
    # staging folder full of channel files would briefly appear there as a job.
    dataset_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=dataset_dir.parent, prefix=".gpr-import-"))
    try:
        for destination, (_, data) in staged.items():
            (staging / destination).write_bytes(data)
        for destination, (client_name, _) in staged.items():
            if destination.startswith("Single-01.R"):
                try:
                    parse_spr(staging / destination)
                # parse_spr raises KeyError for a missing header field, ValueError for a bad one
                except (ValueError, KeyError) as exc:
                    raise ValueError(f"{client_name} could not be read as an SPR channel: {exc}") from exc
        try:
            staging.rename(target)
        except OSError as exc:
            # Another import took the name while this one was parsing; theirs stays untouched.
            raise ValueError(f"a job called {job_name!r} already exists — choose another name") from exc
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return sorted(staged)


def reference_dir(dataset_dir: Path) -> Path:
    return dataset_dir.parent / "reference_drawings"


def _free_name(folder: Path, name: str) -> str:
    candidate, stem, suffix, n = name, Path(name).stem, Path(name).suffix, 1
    while (folder / candidate).exists():
        candidate = f"{stem} ({n}){suffix}"
        n += 1
    return candidate


def import_references(dataset_dir: Path, files: Sequence[Upload]) -> list[str]:
    """Store reference drawings, never overwriting one already there. Returns the stored names."""
    checked = []
    for client_name, data in files:
        name = _base_name(client_name)
        if not name or name.startswith(".") or Path(name).suffix.lower() not in REFERENCE_SUFFIXES:
            raise ValueError(
                f"{client_name}: not an accepted drawing — expected one of "
                f"{', '.join(sorted(REFERENCE_SUFFIXES))}"
            )
        if not data:
            raise ValueError(f"{client_name} is empty")
        checked.append((name, data))

    folder = reference_dir(dataset_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stored = []
    for name, data in checked:
        final = _free_name(folder, name)
        fd, temp = tempfile.mkstemp(dir=folder, prefix=".upload-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temp, folder / final)
        stored.append(final)
    return sorted(stored)


def list_references(dataset_dir: Path) -> list[dict[str, object]]:
    folder = reference_dir(dataset_dir)
    if not folder.exists():
        return []
    return [
        {"name": path.name, "size_bytes": path.stat().st_size}
        for path in sorted(folder.iterdir())
        if path.is_file() and not path.name.startswith(".")
    ]


def reference_path(dataset_dir: Path, name: str) -> Path:
    """A stored drawing's path, found by membership in the listing — never by joining the name."""
    for item in list_references(dataset_dir):
        if item["name"] == name:
            return reference_dir(dataset_dir) / name
    raise FileNotFoundError(name)


def _natural_key(path: str) -> list[object]:
    """Sort "2.pdf" before "10.pdf" — sheet numbers, as a reader expects them."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path)]


def list_documents(dataset_dir: Path) -> list[dict[str, object]]:
    """Drawings, reports and images already sitting in the dataset folder, found without uploading.

    Anything with an accepted drawing suffix, anywhere under the dataset, except inside radar
    job folders and hidden files. Read-only: this lists and serves what is there, it never
    moves or copies it.
    """
    if not dataset_dir.exists():
        return []
    jobs = set(list_jobs(dataset_dir))
    found = []
    for path in dataset_dir.rglob("*"):
        rel = path.relative_to(dataset_dir)
        if rel.parts[0] in jobs or any(part.startswith(".") for part in rel.parts):
            continue
        if path.is_file() and path.suffix.lower() in REFERENCE_SUFFIXES:
            found.append({
                "path": rel.as_posix(),
                "name": path.name,
                "folder": rel.parent.as_posix() if rel.parent != Path(".") else "",
                "size_bytes": path.stat().st_size,
            })
    return sorted(found, key=lambda doc: _natural_key(str(doc["path"])))


def document_path(dataset_dir: Path, rel_path: str) -> Path:
    """A listed document's path, found by membership in the listing — never by joining the name."""
    for doc in list_documents(dataset_dir):
        if doc["path"] == rel_path:
            return dataset_dir / str(doc["path"])
    raise FileNotFoundError(rel_path)
