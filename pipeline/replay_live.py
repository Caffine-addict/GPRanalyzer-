"""Replay any path through the real pipeline and print findings as they settle — no UI needed.

    .venv/bin/python -m pipeline.replay_live Dataset/DSU_GPR_Files/Job_0703
    .venv/bin/python -m pipeline.replay_live ~/Downloads/survey.rar --fast
    .venv/bin/python -m pipeline.replay_live Dataset/DSU_GPR_Files/Job_0703/Single-01.ra1 --reason

The same orchestrator, detector and evidence code the API runs, with the configured source
replaced by a replay of `path` (a scan file, a folder, or an archive). Findings go to a throwaway
store. Pacing is walking pace from config.yaml unless --fast. --reason calls the configured LLM
for each finding (needs GROQ_API_KEY in .env), otherwise reasoning is off.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

import parsers.image
import parsers.spr  # noqa: F401 - registers the SPR parsers as a side effect
from core.config import load_config
from core.contracts import Finding
from pipeline.classical_detector import ClassicalDetector
from pipeline.orchestrator import Orchestrator
from reason.engine import GroqClient, ReasoningEngine
from sources.intake import IntakeError
from sources.replay import ReplaySource
from store.duckdb_store import DuckDBStore


def _describe(finding: Finding) -> str:
    ev = finding.evidence
    where = "position unavailable" if ev.position_m is None else f"{ev.position_m:6.2f} m ({ev.position_confidence})"
    depth = "depth unavailable" if ev.depth_m is None else f"depth {ev.depth_m:4.2f} m ({ev.depth_confidence})"
    return f"{where} | {depth} | {ev.detection_class} | fit R2 {ev.detection_confidence:.2f} | risk {finding.risk_level}"


async def _run(args: argparse.Namespace) -> int:
    load_dotenv()
    config = load_config(args.config)
    source = ReplaySource(replace(config.source.replay, path=str(args.path), step_mode=args.fast))
    try:
        intake = source.intake()
    except IntakeError as e:
        print(f"cannot use {args.path}: {e}", file=sys.stderr)
        return 2
    print(f"{len(intake.files)} scan file(s):")
    for f in intake.files:
        print(f"  + {f}")
    for f, reason in intake.skipped:
        print(f"  - skipped {f.name}: {reason}")

    engine = None
    if args.reason:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            print("--reason needs GROQ_API_KEY in .env", file=sys.stderr)
            return 2
        engine = ReasoningEngine(GroqClient(api_key), config.reasoning)

    start = time.monotonic()
    current_line: list[object] = [None]

    def emit_frame(frame) -> None:  # type: ignore[no-untyped-def]
        line = frame.provenance.get("path")
        if line != current_line[0]:
            current_line[0] = line
            print(f"\n== {line}")

    def emit(finding_id: int, finding: Finding, event_type: str) -> None:
        stamp = f"[{time.monotonic() - start:6.2f}s]"
        if event_type == "finding.created":
            print(f"{stamp} #{finding_id} {_describe(finding)}")
        else:
            print(f"{stamp} #{finding_id} reasoning: {finding.what} — {finding.recommended_action}")

    with tempfile.TemporaryDirectory() as tmp:
        store = DuckDBStore(Path(tmp) / "replay_live.duckdb")
        orchestrator = Orchestrator(
            source=source, detector=ClassicalDetector(), store=store, config=config,
            emit=emit, survey_id="replay-live", reasoning_engine=engine, emit_frame=emit_frame,
        )
        try:
            await orchestrator.run()
            await orchestrator.wait_for_pending_reasoning()
        finally:
            store.close()
    print(f"\ndone in {time.monotonic() - start:.2f}s")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="a scan file, a folder, or an archive (.rar/.zip/.7z/.tar)")
    parser.add_argument("--fast", action="store_true", help="no pacing: process as fast as possible")
    parser.add_argument("--reason", action="store_true", help="explain each finding with the configured LLM")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
