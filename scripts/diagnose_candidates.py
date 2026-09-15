#!/usr/bin/env python3
"""Command line over the package's shape diagnosis: measure every candidate box in a job.

The measurement lives in `detect/hyperbola.py` (pure, no I/O) and the job-level driver in
`studio/diagnose.py`. This file is only the entry point, kept because the workflow it supports
is real: run the candidate detector, then run this, then open the Studio and look at what it
suggested.

It used to hold all of it, and the Studio imported it — but `scripts/` is not a declared package
in pyproject.toml and has no `__init__.py`, so every installed copy of the project broke on that
import. Nothing in `core/`, `detect/` or `studio/` may import from here.

Usage:
    .venv/bin/python scripts/diagnose_candidates.py Dataset/DSU_GPR_Files/Job_0703
    .venv/bin/python scripts/diagnose_candidates.py --all
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from studio.diagnose import diagnose_job


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <job_directory | --all>", file=sys.stderr)
        return 1

    if argv[1] == "--all":
        base = Path("Dataset/DSU_GPR_Files")
        if not base.exists():
            print(f"no dataset at {base}", file=sys.stderr)
            return 1
        job_dirs = sorted(d for d in base.iterdir() if d.is_dir())
    else:
        job_dirs = [Path(argv[1])]

    for job_dir in job_dirs:
        results = diagnose_job(job_dir)
        by_class: dict[str | None, int] = {}
        for diagnosis in results:
            by_class[diagnosis.suggested_class] = by_class.get(diagnosis.suggested_class, 0) + 1
        print(f"{job_dir.name}: {len(results)} boxes diagnosed -> {by_class}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
