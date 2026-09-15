#!/usr/bin/env python3
"""Run the orchestrator against replayed data to populate a store, then generate the
end-of-survey PDF report from it — Session 9's gate: a real report built from real
(or synthetic-fallback) findings, not a hand-built fixture.

Usage:
    .venv/bin/python scripts/report_heartbeat.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

import parsers.image  # noqa: F401 - registers the image parser as a side effect
from core.config import load_config
from core.contracts import Detection
from detect.model import Detector, ModelLoadError, ModelNotFoundError
from pipeline.orchestrator import Orchestrator
from reason.engine import GroqClient, ReasoningEngine
from reports.generate import generate_report
from sources.replay import ReplaySource
from store.duckdb_store import DuckDBStore


class _DetectorWithSyntheticFallback:
    """Same fallback pattern as scripts/orchestrator_heartbeat.py — substitutes synthetic
    detections per frame when no trained weights exist yet, so this demo produces real
    findings for the report to actually render (two classes, so the report's per-class
    and risk-escalation sections have something real to show).
    """

    def __init__(self, real: Detector) -> None:
        self._real = real
        self._warned = False

    def detect(self, image):
        try:
            return self._real.detect(image)
        except (ModelNotFoundError, ModelLoadError) as e:
            if not self._warned:
                print(f"detect: no real weights ({e}) — using synthetic detections for this demo")
                self._warned = True
            h, w = image.shape
            return [
                Detection(class_name="cavities", confidence=0.72, bbox_xyxy=(w * 0.2, h * 0.3, w * 0.4, h * 0.5)),
                Detection(
                    class_name="disturbed_zone", confidence=0.4, bbox_xyxy=(w * 0.6, h * 0.1, w * 0.8, h * 0.3)
                ),
            ]


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(name)s %(message)s")
    load_dotenv()

    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    source = ReplaySource(cfg.source.replay)
    detector = _DetectorWithSyntheticFallback(Detector(cfg.detection))
    store = DuckDBStore(Path(cfg.store.path).parent / "report_heartbeat.duckdb")

    api_key = os.environ.get("GROQ_API_KEY")
    reasoning_engine = ReasoningEngine(GroqClient(api_key), cfg.reasoning) if api_key else None
    if reasoning_engine is None:
        print("reason: no GROQ_API_KEY in .env — findings will show 'reasoning unavailable' in the report")

    survey_id = f"report-demo-{int(time.time())}"
    orchestrator = Orchestrator(
        source=source,
        detector=detector,
        store=store,
        config=cfg,
        emit=lambda finding_id, finding, event_type: None,
        survey_id=survey_id,
        reasoning_engine=reasoning_engine,
    )

    asyncio.run(_run(orchestrator))

    output_path = Path(cfg.store.path).parent / f"{survey_id}.pdf"
    out = generate_report(survey_id, store, output_path)
    summary = store.get_survey_summary(survey_id)
    store.close()

    print(f"survey summary: {summary}")
    print(f"report written: {out} ({out.stat().st_size} bytes)")
    return 0


async def _run(orchestrator: Orchestrator) -> None:
    await orchestrator.run()
    await orchestrator.wait_for_pending_reasoning()


if __name__ == "__main__":
    raise SystemExit(main())
