"""Run the Studio workstation.

    .venv/bin/python -m studio [dataset_dir]
    # then open http://127.0.0.1:8500

Kept separate from `api/server.py`'s entry point: this is a desktop
interpretation tool over recorded lines, not the live survey pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

from studio.server import app
from studio.session import DEFAULT_DATASET_DIR

DEFAULT_PORT = 8500


def main(argv: list[str]) -> int:
    dataset_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_DATASET_DIR
    if not dataset_dir.exists():
        print(f"dataset directory not found: {dataset_dir.resolve()}", file=sys.stderr)
        return 1

    app.state.dataset_dir = dataset_dir
    print(f"GPR Studio — dataset: {dataset_dir.resolve()}")
    print(f"open http://127.0.0.1:{DEFAULT_PORT}")
    uvicorn.run(app, host="127.0.0.1", port=DEFAULT_PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
