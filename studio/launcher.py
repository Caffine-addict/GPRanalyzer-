"""Start GPR Studio as an installed app: the user's own data folder, a free port, a browser tab.

The Studio reads `config.yaml`, `.env` and writes `annotations/` relative to where it runs, which
for a developer is the repository. An installed app runs from wherever its icon sits, so this
moves into a per-user folder first (`~/GPR Studio`, or `$GPR_STUDIO_HOME`) and keeps everything
there: radar lines, reviews, reference drawings, settings. Reinstalling or updating the app never
touches it.

    gpr-studio            # the packaged app
    python -m studio.launcher
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import sys
import threading
import webbrowser
from collections.abc import Mapping
from pathlib import Path

APP_NAME = "GPR Studio"
LINES_DIR = "Lines"
PREFERRED_PORT = 8500


def home_dir(env: Mapping[str, str] = os.environ) -> Path:
    return Path(env["GPR_STUDIO_HOME"]) if env.get("GPR_STUDIO_HOME") else Path.home() / APP_NAME


def bundled_file(name: str) -> Path:
    """A file shipped with the app: beside the code when frozen, the repository root otherwise."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return root / name


def prepare_home(home: Path, default_config: Path) -> Path:
    """Create the data folder on first run; never overwrite what the user already has."""
    (home / LINES_DIR).mkdir(parents=True, exist_ok=True)
    config = home / "config.yaml"
    if not config.exists():
        shutil.copyfile(default_config, config)
    env_example = home / ".env.example"
    if not env_example.exists():
        env_example.write_text("# Optional: the reasoning assistant's key. Copy this file to .env and fill it in.\n"
                               "GROQ_API_KEY=\n")
    return home / LINES_DIR


def free_port(preferred: int = PREFERRED_PORT) -> int:
    """The preferred port when nothing holds it, else one the system offers."""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return int(s.getsockname()[1])
    raise RuntimeError("no free local port")


def main() -> int:
    home = home_dir()
    lines = prepare_home(home, bundled_file("config.yaml"))
    os.chdir(home)  # config.yaml, .env and annotations/ are all read relative to here

    from dotenv import load_dotenv

    load_dotenv(home / ".env")
    import uvicorn

    from studio.server import app  # imported after chdir: nothing may resolve paths before it

    app.state.dataset_dir = lines
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    # A double-clicked app has no visible console, so the log goes to the data folder as well.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(home / "studio.log", encoding="utf-8"), logging.StreamHandler()])
    logging.getLogger("gpr_studio").warning("started: data %s, %s", home, url)
    print(f"{APP_NAME} — your data: {home}\nopen {url}  (close this window to stop)", flush=True)
    threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)  # keep the file log
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
