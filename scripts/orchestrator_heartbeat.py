#!/usr/bin/env python3
"""Run the full orchestrator end to end on replayed data — no hardware, no company weights, no
API key required to prove the wiring; each is used for real when present.

This is Session 6's gate: source -> preprocess -> detect -> evidence -> risk
-> store, with the fast path emitting immediately and reasoning arriving
behind it asynchronously. Since no trained weights exist yet, the real
Detector's ModelNotFoundError is caught here and a single synthetic
detection is substituted per frame — same "demonstrate the designed failure
path honestly" approach as scripts/pipeline_heartbeat.py, just wired
through the real orchestrator instead of called by hand.

Usage:
    .venv/bin/python scripts/orchestrator_heartbeat.py
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
from core.contracts import Detection, Finding
from detect.model import Detector, ModelLoadError, ModelNotFoundError
from pipeline.orchestrator import Orchestrator
from reason.engine import GroqClient, ReasoningEngine
from sources.replay import ReplaySource
from store.duckdb_store import DuckDBStore


class _DetectorWithSyntheticFallback:
    """Wraps the real Detector; substitutes one synthetic detection per frame when no
    trained weights exist yet, so this demo produces real findings to show the full chain
    instead of only demonstrating the (already-covered-elsewhere) empty-detections path.
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
                Detection(
                    class_name="cavities",
                    confidence=0.72,
                    bbox_xyxy=(w * 0.2, h * 0.3, w * 0.4, h * 0.5),
                )
            ]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    load_dotenv()

    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    source = ReplaySource(cfg.source.replay)
    detector = _DetectorWithSyntheticFallback(Detector(cfg.detection))
    store = DuckDBStore(Path(cfg.store.path).parent / "orchestrator_heartbeat.duckdb")

    api_key = os.environ.get("GROQ_API_KEY")
    reasoning_engine = ReasoningEngine(GroqClient(api_key), cfg.reasoning) if api_key else None
    if reasoning_engine is None:
        print("reason: no GROQ_API_KEY in .env — running fast path only, no reasoning emission")

    def emit(finding: Finding, event_type: str) -> None:
        if event_type == "finding.created":
            print(
                f"[{event_type}] {finding.evidence.detection_class} "
                f"risk={finding.risk_level}({finding.risk_score:.2f}) "
                f"depth={finding.evidence.depth_m} ({finding.evidence.depth_confidence})"
            )
        else:
            print(f"[{event_type}] recommended_action={finding.recommended_action!r}")

    survey_id = f"demo-{int(time.time())}"
    orchestrator = Orchestrator(
        source=source,
        detector=detector,
        store=store,
        config=cfg,
        emit=emit,
        survey_id=survey_id,
        reasoning_engine=reasoning_engine,
    )

    start = time.monotonic()
    asyncio.run(_run(orchestrator))
    print(f"total: {(time.monotonic() - start) * 1000:.2f}ms")
    print(f"survey summary: {store.get_survey_summary(survey_id)}")
    store.close()
    return 0


async def _run(orchestrator: Orchestrator) -> None:
    await orchestrator.run()
    await orchestrator.wait_for_pending_reasoning()


if __name__ == "__main__":
    raise SystemExit(main())
