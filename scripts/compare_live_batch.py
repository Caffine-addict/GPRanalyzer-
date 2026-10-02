#!/usr/bin/env python3
"""Does streaming a line find the same targets as processing it whole?

For every channel of every job: "batch" is the classical detector run once on the whole line;
"live" is the same line replayed in chunks through the real orchestrator, keeping what it
reports. Targets are matched with the orchestrator's own `_same_target` rule. Prints matched /
missed (batch only) / extra (live only) per line and in total.

    .venv/bin/python scripts/compare_live_batch.py [chunk_traces] [window_traces]
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import parsers.spr  # noqa: F401 - registers the SPR parsers
from core.config import load_config
from core.contracts import Finding, TraceBox
from parsers.base import get_parser
from pipeline.classical_detector import ClassicalDetector
from pipeline.orchestrator import Orchestrator, _same_target
from preprocess.enhance import enhance
from render.bscan import traces_to_image
from sources.replay import ReplaySource
from store.duckdb_store import DuckDBStore

DATASET = Path("Dataset/DSU_GPR_Files")


def batch_boxes(path: Path, config) -> list[TraceBox]:  # type: ignore[no-untyped-def]
    frame = get_parser(path)(path)
    assert frame.traces is not None
    image = enhance(traces_to_image(frame.traces), config.enhancement)
    n_traces, n_samples = frame.traces.shape
    height, width = image.shape[:2]
    out = []
    for d in ClassicalDetector().detect(image, frame):
        x1, y1, x2, y2 = d.bbox_xyxy
        out.append(TraceBox(round(x1 * n_traces / width), round(x2 * n_traces / width),
                            round(y1 * n_samples / height), round(y2 * n_samples / height)))
    return out


async def live_boxes(path: Path, config, chunk: int, window: int) -> list[TraceBox]:  # type: ignore[no-untyped-def]
    found: list[TraceBox] = []

    def emit(_id: int, finding: Finding, _event: str) -> None:
        assert finding.location is not None
        found.append(finding.location)

    replay = replace(config.source.replay, path=str(path), step_mode=True, chunk_traces=chunk, window_traces=window)
    with tempfile.TemporaryDirectory() as tmp:
        store = DuckDBStore(Path(tmp) / "cmp.duckdb")
        try:
            await Orchestrator(ReplaySource(replay), ClassicalDetector(), store, config, emit, "cmp").run()
        finally:
            store.close()
    return found


def main() -> None:
    chunk = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    window = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    config = load_config("config.yaml")
    totals = [0, 0, 0, 0, 0]
    print(f"chunk_traces={chunk} window_traces={window or 'whole line so far'}")
    print(f"{'line':32} {'batch':>5} {'live':>5} {'match':>5} {'missed':>6} {'extra':>5}")
    for job in sorted(d for d in DATASET.iterdir() if d.is_dir()):
        for path in sorted(p for p in job.iterdir() if p.suffix.lower() in {".rad", ".ra1", ".ra2"}):
            batch = batch_boxes(path, config)
            live = asyncio.run(live_boxes(path, config, chunk, window))
            matched = sum(any(_same_target(b, lv) for lv in live) for b in batch)
            extra = sum(not any(_same_target(lv, b) for b in batch) for lv in live)
            row = [len(batch), len(live), matched, len(batch) - matched, extra]
            totals = [t + r for t, r in zip(totals, row, strict=True)]
            print(f"{job.name + '/' + path.name:32} {row[0]:5} {row[1]:5} {row[2]:5} {row[3]:6} {row[4]:5}")
    print(f"{'TOTAL':32} {totals[0]:5} {totals[1]:5} {totals[2]:5} {totals[3]:6} {totals[4]:5}")
    print(f"batch targets found live: {totals[2]}/{totals[0]}; live findings with no batch match: {totals[4]}/{totals[1]}")


if __name__ == "__main__":
    main()
