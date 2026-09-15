"""Safe storage for per-job annotation files (candidate boxes, interpreted picks).

Both stores keep one JSON file per job under `annotations/<job>/` and update it by
read-modify-write. Three things go wrong with that done naively, and all three are
handled here, once, rather than separately in each store:

- **A job name that escapes the folder.** Only a single path component is accepted: no
  `/`, no `\\` (a separator on Windows, where this may be deployed), no leading dot.
- **Two writers at once.** The Studio serves requests from a thread pool, so two saves
  to the same job can interleave, and the later write silently erases the earlier
  one. Callers hold `ANNOTATION_WRITE_LOCK` across the whole read-modify-write. The lock
  is per process: two *separate* processes writing one job could still collide.
- **A crash mid-write.** Files are written to a temporary name and renamed into place,
  so a reader sees either the old file or the new one, never half of one.
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from pathlib import Path

_SAFE_JOB_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

# Re-entrant: an add() holds it while calling load(), which is itself safe to call alone.
ANNOTATION_WRITE_LOCK = threading.RLock()


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
