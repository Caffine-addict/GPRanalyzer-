"""Safe storage for per-job annotation files (candidate boxes, interpreted picks).

Both stores keep one JSON file per job under `annotations/<job>/` and update it by
read-modify-write. Three things go wrong with that done naively, and all three are
handled here, once, rather than separately in each store:

- **A job name that escapes the folder.** Only a single path component is accepted: no
  `/`, no `\\` (a separator on Windows, where this may be deployed), no leading dot.
- **Two writers at once, in one process or in two.** The Studio serves requests from a
  thread pool, so two saves to the same job can interleave and the later write silently
  erases the earlier one. Worse, the detector runs as its own CLI process while a Studio
  may be running, and `core.boxes.replace_detector_boxes` rewrites the whole file — so the
  losing interleave destroys a human-drawn box, which cannot be recovered. Callers hold
  `job_lock()` across the whole read-modify-write: it takes an in-process lock scoped to
  *that job* **and** an OS-level exclusive lock on that job's lock file, so it serialises
  threads and separate processes alike — without making one job's contention (a slow
  detector run) stall every other job's requests in the same Studio process. Proven against
  real subprocesses in `tests/test_annotation_cross_process_lock.py`, which threads alone
  could not prove.
- **A crash mid-write.** Files are written to a temporary name and renamed into place,
  so a reader sees either the old file or the new one, never half of one.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

_SAFE_JOB_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

# Kept for anything that still imports it directly (e.g. an existing caller pinned to the old
# name); job_lock() itself uses the per-job locks below, not this.
ANNOTATION_WRITE_LOCK = threading.RLock()

# A lock file of its own, never the data file: `write_text_atomically` replaces the data file
# by rename, which swaps the inode out from under anyone holding a lock on it.
_LOCK_FILE_NAME = ".annotation.lock"

# One in-process RLock per job directory, not one lock shared by every job. The OS-level wait
# inside job_lock() can be genuinely slow under contention (another process mid-write), and a
# single global lock held across that wait would stall every unrelated job in this process —
# a detector run on Job_A blocking a Studio request for Job_B, which is a real availability
# regression the per-job split avoids. `_registry_lock` guards only the dict itself, never held
# across a job's own critical section.
_job_locks: dict[Path, threading.RLock] = {}
_registry_lock = threading.Lock()
# Per thread: how deep this thread is inside job_lock() for each job, so only the outermost
# entry touches the OS lock (see job_lock).
_held = threading.local()


def _lock_for(job_dir: Path) -> threading.RLock:
    resolved = job_dir.resolve()
    with _registry_lock:
        lock = _job_locks.get(resolved)
        if lock is None:
            lock = threading.RLock()
            _job_locks[resolved] = lock
        return lock

# `sys.platform` is compared directly rather than through a variable so mypy narrows the
# branches and does not try to type-check the Windows API on POSIX (and vice versa).
if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
    import msvcrt

    def _acquire_os_lock(handle: IO[bytes]) -> None:
        """Block until this process holds an exclusive OS lock on `handle`.

        msvcrt has no blocking mode — it raises after its own short wait — so this retries
        rather than surface a spurious failure under contention. Seeks to byte 0 first: the
        handle is opened "a+b", and acquiring and releasing must lock the *same* byte range or
        they silently stop corresponding to the same lock.
        """
        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError:
                time.sleep(0.05)

    def _release_os_lock(handle: IO[bytes]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _acquire_os_lock(handle: IO[bytes]) -> None:
        """Block until this process holds an exclusive OS lock on `handle`."""
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def _release_os_lock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def job_lock(job_dir: Path) -> Iterator[None]:
    """Exclusive access to one job's annotation files, across threads *and* processes.

    Hold this across the entire read-modify-write, not just the write: the race that loses
    data is a stale read, not a torn write. `write_text_atomically` already makes the write
    itself all-or-nothing.

    The in-process lock is scoped to this one job directory (see `_lock_for`), taken first and
    released last, so a thread cannot be waiting on the OS lock while holding nothing, and a
    slow cross-process wait on one job never blocks a thread working on another. It is
    re-entrant, so nesting this inside another `job_lock` for the same job in one thread is safe.

    Only the outermost entry takes the OS lock. flock/msvcrt locks belong to an open file
    description, so a nested entry opening the lock file again would wait on the lock its own
    thread already holds — a self-deadlock, which is what this did before the depth count.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    key = job_dir.resolve()
    with _lock_for(job_dir):
        depths: dict[Path, int] = _held.__dict__.setdefault("depths", {})
        if depths.get(key, 0) > 0:
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        with open(job_dir / _LOCK_FILE_NAME, "a+b") as handle:
            _acquire_os_lock(handle)
            depths[key] = 1
            try:
                yield
            finally:
                depths[key] = 0
                _release_os_lock(handle)


def safe_job_dir(root: Path, job_name: str) -> Path:
    """`root/job_name`, refusing any name that is not a single safe path component."""
    if not isinstance(job_name, str) or not _SAFE_JOB_NAME.fullmatch(job_name):
        raise ValueError(f"unsafe job name: {job_name!r}")
    return root / job_name


def write_text_atomically(path: Path, text: str) -> None:
    """Replace `path` with `text` in one step: write a temporary file, then rename it over."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp_name, path)  # atomic on both POSIX and Windows
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
