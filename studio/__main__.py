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
from dotenv import load_dotenv

from store.auth_store import AuthStore
from store.database import DatabaseNotConfiguredError, database_url
from store.label_store import LabelStore
from studio.auth import Auth
from studio.server import app
from studio.session import DEFAULT_DATASET_DIR

DEFAULT_PORT = 8500


def main(argv: list[str]) -> int:
    dataset_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_DATASET_DIR
    if not dataset_dir.exists():
        print(f"dataset directory not found: {dataset_dir.resolve()}", file=sys.stderr)
        return 1

    load_dotenv()
    try:
        app.state.auth = Auth(AuthStore(database_url()))
        app.state.labels = LabelStore(database_url())
    except DatabaseNotConfiguredError as exc:
        print(f"cannot start: {exc}", file=sys.stderr)
        return 1
    if not app.state.auth.store.has_accounts():
        print("no accounts yet; create one with: python -m studio.users add <name>")

    app.state.dataset_dir = dataset_dir
    print(f"GPR Studio — dataset: {dataset_dir.resolve()}")
    print(f"open http://127.0.0.1:{DEFAULT_PORT}")
    # Loopback only: other devices reach it through an HTTPS proxy on this machine (docs/HOSTING.md),
    # whose forwarded headers uvicorn trusts from 127.0.0.1 by default, so sign-ins log the real address.
    uvicorn.run(app, host="127.0.0.1", port=DEFAULT_PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
