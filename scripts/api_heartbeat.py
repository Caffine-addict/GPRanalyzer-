#!/usr/bin/env python3
"""Session 7 gate: connect a WebSocket client, start a replay survey, watch findings stream
with reasoning updates following — against a real running FastAPI server (a subprocess),
not the in-process TestClient the test suite uses.

No real YOLO weights or GROQ_API_KEY exist yet, so this uses the real /surveys/{id}/start
endpoint against the real config.yaml — expect it to report 0 findings (ModelNotFoundError
handled gracefully, same as every other heartbeat script), which is itself the honest,
correct outcome at this stage. Run scripts/orchestrator_heartbeat.py to see findings flow
with synthetic detections substituted; this script proves the *API* wiring specifically.

Usage:
    .venv/bin/python scripts/api_heartbeat.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets

REPO_ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PORT = 8765
BASE_URL = f"http://{HOST}:{PORT}"


def _wait_for_server() -> None:
    for _ in range(50):
        try:
            urllib.request.urlopen(f"{BASE_URL}/surveys", timeout=1)
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("server did not start in time")


def _start_survey(survey_id: str) -> dict:
    req = urllib.request.Request(f"{BASE_URL}/surveys/{survey_id}/start", method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


async def _stream_findings() -> None:
    async with websockets.connect(f"ws://{HOST}:{PORT}/ws/live") as ws:
        record = _start_survey("demo-survey")
        print(f"survey started: {record}")

        print("listening for findings (5s)...")
        deadline = time.monotonic() + 5
        n_messages = 0
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(remaining, 0.1))
            except TimeoutError:
                break
            message = json.loads(raw)
            n_messages += 1
            print(f"  [{message['type']}] {message['finding']['evidence']['detection_class']}")

        print(f"done: {n_messages} WebSocket messages received")


def main() -> int:
    # Started outside the asyncio event loop deliberately: subprocess.Popen
    # is a blocking call and shouldn't run inside an async def.
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api.server:app",
            "--host",
            HOST,
            "--port",
            str(PORT),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
    )
    try:
        _wait_for_server()
        print(f"server up at {BASE_URL}")
        asyncio.run(_stream_findings())
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
