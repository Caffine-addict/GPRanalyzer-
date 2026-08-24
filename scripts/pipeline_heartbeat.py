#!/usr/bin/env python3
"""Feed one replayed frame through preprocess, detect, evidence, risk, and reasoning — with timings printed.

No trained YOLO weights exist yet (no company data/training run) — that's
expected at this stage of the project. This script demonstrates the real
parts honestly: real enhancement timing on a real replayed frame, and the
detector's required behavior when weights are absent — a clear, typed
error, not a stack trace. Since there's no real Detection to carry forward,
evidence + risk + reasoning are then demonstrated on one explicitly-labelled
synthetic detection instead. Same principle for reasoning: without a real
GROQ_API_KEY in .env, it demonstrates the failure path (best-effort,
Finding survives without reasoning) rather than faking a response.

Usage:
    .venv/bin/python scripts/pipeline_heartbeat.py
"""

from __future__ import annotations

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
from detect.model import Detector, ModelNotFoundError
from evidence.extract import extract_evidence
from preprocess.enhance import enhance
from reason.engine import GroqClient, ReasoningEngine
from risk.score import score_detections
from sources.replay import ReplaySource


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    load_dotenv()

    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml")
    source = ReplaySource(cfg.source.replay)

    frame = next(source.frames())
    if frame.image is None:
        print("frame has no image (traces-only) — render/bscan.py would be needed first, skipping")
        return 0
    print(f"frame: position={frame.position} image_shape={frame.image.shape}")

    start = time.monotonic()
    enhanced = enhance(frame.image, cfg.enhancement)
    print(f"preprocess: {(time.monotonic() - start) * 1000:.2f}ms, output_shape={enhanced.shape}")

    detector = Detector(cfg.detection)
    start = time.monotonic()
    try:
        detections = detector.detect(enhanced)
        print(f"detect: {(time.monotonic() - start) * 1000:.2f}ms, n_detections={len(detections)}")
        for d in detections:
            print(f"  {d.class_name}: {d.confidence:.2%} bbox={d.bbox_xyxy}")
    except ModelNotFoundError as e:
        print(f"detect: {(time.monotonic() - start) * 1000:.2f}ms — no weights yet (expected): {e}")
        # Sized to actually sit inside this frame's image — an off-frame
        # bbox now correctly reports "unavailable" evidence rather than
        # fabricating a value from a clamped edge pixel (see evidence/
        # extract.py's _clip_range), which would defeat the point of this
        # demo.
        h, w = frame.image.shape
        detections = [
            Detection(
                class_name="cavities",
                confidence=0.72,
                bbox_xyxy=(w * 0.2, h * 0.3, w * 0.4, h * 0.5),
            ),
        ]
        print(f"  using 1 SYNTHETIC detection instead (no trained model to produce a real one): {detections[0]}")

    capabilities = source.capabilities()

    start = time.monotonic()
    risk = score_detections(detections, cfg.risk)
    print(
        f"risk: {(time.monotonic() - start) * 1000:.2f}ms score={risk.score:.3f} "
        f"level={risk.level} rules_fired={risk.rules_fired}"
    )

    api_key = os.environ.get("GROQ_API_KEY")
    engine = ReasoningEngine(GroqClient(api_key), cfg.reasoning) if api_key else None
    if engine is None:
        print("reason: skipped — no GROQ_API_KEY in .env (see .env.example)")

    for detection in detections:
        ev = extract_evidence(detection, frame, capabilities, cfg.evidence)
        print(
            f"evidence: depth={ev.depth_m} ({ev.depth_confidence}) "
            f"position={ev.position_m} ({ev.position_confidence}) "
            f"amplitude={ev.amplitude} ({ev.amplitude_confidence})"
        )

        if engine is not None:
            result, latency_ms = engine.reason(ev, risk)
            if result is None:
                print(f"reason: {latency_ms:.2f}ms — failed (see WARNING log above), Finding survives without it")
            else:
                print(f"reason: {latency_ms:.2f}ms")
                print(f"  what: {result.what}")
                print(f"  where: {result.where}")
                print(f"  why: {result.why}")
                print(f"  how: {result.how}")
                print(f"  recommended_action: {result.recommended_action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
