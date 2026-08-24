#!/usr/bin/env python3
"""Instantiate ReplaySource on a folder of images and print one line per frame at
the configured rate. This is the heartbeat of everything downstream — if this
doesn't run cleanly, nothing built on top of it will either.

Usage:
    .venv/bin/python scripts/replay_heartbeat.py [directory]

If no directory is given, uses source.replay.directory from config.yaml.
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import parsers.image  # noqa: F401 - registers the image parser as a side effect
from core.config import load_config
from sources.replay import ReplaySource


def main() -> int:
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    replay_cfg = cfg.source.replay
    if len(sys.argv) > 1:
        replay_cfg = replace(replay_cfg, directory=sys.argv[1])

    source = ReplaySource(replay_cfg)
    caps = source.capabilities()
    print(
        f"source=replay directory={replay_cfg.directory} "
        f"rate_hz={replay_cfg.playback_rate_hz} capabilities={caps}"
    )

    start = time.monotonic()
    count = 0
    for frame in source.frames():
        count += 1
        elapsed = time.monotonic() - start
        shape = frame.image.shape if frame.image is not None else None
        print(
            f"[{elapsed:6.2f}s] frame={count} position={frame.position} "
            f"position_source={frame.position_source} image_shape={shape}"
        )

    print(f"done: {count} frames in {time.monotonic() - start:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
