"""Turns whatever path someone hands over — a scan file, a folder, an archive — into scan files.

Archives are unpacked with `bsdtar` (libarchive), which ships with macOS and Windows 10+, reads
RAR/RAR5, ZIP, 7z and tar, and by default refuses entries with absolute paths or `..`, so an
archive cannot *write* outside its extraction folder. It is called as a subprocess rather than
through a Python RAR library because every such library shells out to an external tool anyway.

What bsdtar does not stop is an archive *reading* outside it: a symlink entry named `x.rad`
pointing at `~/.env` extracts as that symlink, and parsing it would read the target. So no
symlink, and nothing that resolves outside the folder being walked, is ever collected. An archive
is also sized from its own listing before anything is written, so a small archive that expands
to fill the disk is refused rather than extracted.

Nothing is dropped silently: every file that is not a parseable scan comes back in `skipped`
with the reason, so a report or a DWG in the same archive is visible, not lost.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from parsers.base import registered_extensions

ARCHIVE_SUFFIXES = frozenset({".rar", ".zip", ".7z", ".tar", ".tgz", ".gz", ".bz2", ".xz"})
EXTRACT_ROOT = Path(".tmp/intake")  # gitignored; one folder per distinct archive version
_EXTRACT_TIMEOUT_S = 600
_MAX_ARCHIVE_DEPTH = 3  # an archive inside an archive inside an archive, and no further
# Largest total an archive may expand to. A full survey day of SPR lines is well under 1 GB.
MAX_EXTRACT_BYTES = 5 * 1024**3


class IntakeError(Exception):
    """The path cannot be turned into scan files at all (missing, or an archive that won't open)."""


@dataclass(frozen=True)
class Intake:
    files: tuple[Path, ...]
    skipped: tuple[tuple[Path, str], ...]  # (file, why it was not used)


def collect_scan_files(path: Path, extract_root: Path = EXTRACT_ROOT) -> Intake:
    """Every parseable scan file under `path`, in a stable order, plus everything skipped."""
    path = Path(path)
    if not path.exists():
        raise IntakeError(f"path not found: {path}")
    files: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    _collect(path, extract_root, files, skipped, depth=0)
    return Intake(files=tuple(files), skipped=tuple(skipped))


def _collect(path: Path, extract_root: Path, files: list[Path], skipped: list[tuple[Path, str]], depth: int) -> None:
    if path.is_dir():
        root = path.resolve()
        for child in sorted(path.rglob("*")):
            if child.is_symlink() or not child.resolve().is_relative_to(root):
                skipped.append((child, "symbolic link — not followed"))
                continue
            if not child.is_file() or _is_noise(child, path):
                continue
            _collect_file(child, extract_root, files, skipped, depth)
    else:
        _collect_file(path, extract_root, files, skipped, depth)


def _collect_file(path: Path, extract_root: Path, files: list[Path], skipped: list[tuple[Path, str]], depth: int) -> None:
    suffix = path.suffix.lower()
    if suffix in ARCHIVE_SUFFIXES:
        if depth >= _MAX_ARCHIVE_DEPTH:
            skipped.append((path, f"archive nested more than {_MAX_ARCHIVE_DEPTH} deep — not opened"))
            return
        _collect(_extract(path, extract_root), extract_root, files, skipped, depth + 1)
    elif suffix.lstrip(".") in registered_extensions():
        files.append(path)
    else:
        skipped.append((path, f"not a scan format this tool reads ({suffix or 'no extension'})"))


def _is_noise(path: Path, root: Path) -> bool:
    """macOS archive debris: resource forks and Finder metadata, never data."""
    parts = path.relative_to(root).parts
    return "__MACOSX" in parts or path.name.startswith("._") or path.name == ".DS_Store"


def _extract(archive: Path, extract_root: Path) -> Path:
    """Unpack `archive` once into a folder keyed on its identity and version; reuse it after."""
    stat = archive.stat()
    key = hashlib.sha256(f"{archive.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:12]
    dest = extract_root / f"{archive.stem}-{key}"
    if dest.exists():
        return dest
    bsdtar = shutil.which("bsdtar") or shutil.which("tar")
    if bsdtar is None:
        raise IntakeError("cannot open archives: bsdtar not found (built into macOS and Windows 10+; "
                          "on Linux install libarchive-tools)")
    size = _listed_size(bsdtar, archive)
    if size > MAX_EXTRACT_BYTES:
        raise IntakeError(f"{archive.name} would expand to {size / 1024**3:.1f} GB, over the "
                          f"{MAX_EXTRACT_BYTES / 1024**3:.0f} GB limit — not extracted")
    # A folder unique to this attempt: two requests extracting the same archive at once each get
    # their own, and whichever finishes second finds `dest` already in place and uses it.
    extract_root.mkdir(parents=True, exist_ok=True)
    partial = Path(tempfile.mkdtemp(dir=extract_root, prefix=f"{dest.name}.partial-"))
    try:
        result = subprocess.run(
            [bsdtar, "-xf", str(archive), "-C", str(partial)],
            capture_output=True, text=True, timeout=_EXTRACT_TIMEOUT_S, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(partial, ignore_errors=True)
        raise IntakeError(f"extracting {archive.name} took longer than {_EXTRACT_TIMEOUT_S} s") from exc
    if result.returncode != 0:
        shutil.rmtree(partial, ignore_errors=True)
        raise IntakeError(f"could not extract {archive.name}: {result.stderr.strip() or 'unknown error'}")
    # Renamed into place only once complete, so a crash mid-extract is never mistaken for a cache hit.
    try:
        partial.rename(dest)
    except OSError:
        shutil.rmtree(partial, ignore_errors=True)
        if not dest.exists():
            raise
    return dest


def _listed_size(bsdtar: str, archive: Path) -> int:
    """Total uncompressed bytes the archive's own listing declares.

    Fails closed: a listing line whose size field cannot be read means the size is unknown, and
    an archive of unknown size is not extracted.
    """
    result = subprocess.run(
        [bsdtar, "-tvf", str(archive)], capture_output=True, text=True, timeout=_EXTRACT_TIMEOUT_S, check=False
    )
    if result.returncode != 0:
        raise IntakeError(f"could not extract {archive.name}: {result.stderr.strip() or 'unknown error'}")
    total = 0
    for line in result.stdout.splitlines():
        fields = line.split(None, 8)
        if len(fields) < 5 or not fields[4].isdigit():
            raise IntakeError(f"could not read the size of an entry in {archive.name} — not extracted")
        total += int(fields[4])
    return total
