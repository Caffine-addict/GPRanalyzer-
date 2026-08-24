"""Wires source -> preprocess -> detect -> evidence -> risk -> store into one running pipeline.

The fast path (source through risk scoring) never waits on reasoning:
findings are persisted and emitted immediately with the four reasoning
fields empty, then reasoning runs in a background asyncio task (a blocking
LLM call dispatched to a thread executor, not awaited inline) and emits a
second time when it completes. A Finding survives without reasoning if that
task fails or reasoning is disabled entirely (reasoning_engine=None).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Any, Protocol

import numpy as np

from core.config import Config
from core.contracts import Detection, Finding, ScanFrame, SourceCapabilities
from detect.model import ModelLoadError, ModelNotFoundError
from evidence.extract import extract_evidence
from preprocess.enhance import enhance
from reason.engine import ReasoningEngine
from render.bscan import traces_to_image
from risk.score import RiskAssessment, score_detections
from sources.base import ScanSource
from store.base import Store

logger = logging.getLogger(__name__)

EmitCallback = Callable[[Finding, str], None]


class DetectorLike(Protocol):
    """Whatever detect/model.py's Detector implements — a Protocol so tests can substitute a
    fake without needing real YOLO weights.
    """

    def detect(self, image: np.ndarray) -> list[Detection]: ...


def _next_frame(iterator: Iterator[ScanFrame]) -> ScanFrame | None:
    return next(iterator, None)


class Orchestrator:
    def __init__(
        self,
        source: ScanSource,
        detector: DetectorLike,
        store: Store,
        config: Config,
        emit: EmitCallback,
        survey_id: str,
        line_id: str = "line_1",
        reasoning_engine: ReasoningEngine | None = None,
    ) -> None:
        self._source = source
        self._detector = detector
        self._store = store
        self._config = config
        self._emit = emit
        self._survey_id = survey_id
        self._line_id = line_id
        self._reasoning_engine = reasoning_engine
        self._pending_reasoning: set[asyncio.Task[Any]] = set()

    async def run(self) -> None:
        capabilities = self._source.capabilities()
        loop = asyncio.get_running_loop()
        frames_iter = iter(self._source.frames())
        while True:
            # A source may pace itself with a blocking time.sleep() between
            # frames (ReplaySource honouring playback_rate_hz; real hardware
            # likely will too) — fetching the next frame is offloaded to a
            # thread so that sleep doesn't freeze the whole event loop.
            # _process_frame itself stays on this thread (not the executor
            # one), since it calls asyncio.create_task() for reasoning
            # dispatch, which requires a running loop in the current thread.
            frame = await loop.run_in_executor(None, _next_frame, frames_iter)
            if frame is None:
                break
            try:
                await self._process_frame(frame, capabilities)
            except Exception as e:  # one bad frame must not abort the whole survey
                logger.exception("orchestrator.frame_failed error_type=%s", type(e).__name__)

    async def wait_for_pending_reasoning(self) -> None:
        """For tests and graceful shutdown: wait for all in-flight reasoning tasks."""
        if self._pending_reasoning:
            await asyncio.gather(*self._pending_reasoning)

    async def cancel_pending_reasoning(self) -> None:
        """For a stopped survey: cancel every in-flight reasoning task before returning, so no
        stray finding.reasoned emission from this run can arrive after the caller treats the
        survey as fully stopped (e.g. immediately restarting the same survey_id).

        Cancelling a task awaiting loop.run_in_executor() resolves near-instantly regardless of
        whether the underlying blocking call has already started running in a worker thread —
        confirmed empirically, not assumed: asyncio.Future.cancel() succeeds unconditionally on
        a not-yet-done future, decoupled from whether the wrapped concurrent.futures.Future can
        actually be interrupted. The CancelledError lands at the `await run_in_executor(...)`
        line inside _reason_and_emit, so neither store.update_finding_reasoning() nor emit() ever
        run for that task — no stale write, no stale broadcast. The orphaned OS thread keeps
        running the real blocking call to completion in the background (can't force-kill a
        Python thread), but its result is silently discarded since nothing awaits it anymore.
        """
        tasks = list(self._pending_reasoning)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _process_frame(self, frame: ScanFrame, capabilities: SourceCapabilities) -> None:
        fast_path_start = time.monotonic()
        frame_id = self._store.save_frame(self._survey_id, self._line_id, frame)

        if frame.image is not None:
            image = frame.image
        else:
            assert frame.traces is not None, "ScanFrame guarantees traces or image"
            image = traces_to_image(frame.traces)

        enhanced = enhance(image, self._config.enhancement)

        try:
            detections = self._detector.detect(enhanced)
        except (ModelNotFoundError, ModelLoadError) as e:
            logger.warning("orchestrator.detect_unavailable frame_id=%d error=%s", frame_id, e)
            return

        if not detections:
            latency_ms = (time.monotonic() - fast_path_start) * 1000
            logger.info("orchestrator.fast_path frame_id=%d latency_ms=%.2f n_findings=0", frame_id, latency_ms)
            return

        risk = score_detections(detections, self._config.risk)

        for detection in detections:
            neighbours = tuple(d.class_name for d in detections if d is not detection)
            evidence = extract_evidence(
                detection, frame, capabilities, self._config.evidence, neighbours=neighbours
            )
            finding = Finding(
                evidence=evidence,
                risk_level=risk.level,
                risk_score=risk.score,
                risk_rules_fired=risk.rules_fired,
            )

            finding_id = self._store.save_finding(self._survey_id, self._line_id, frame_id, finding)
            self._emit(finding, "finding.created")

            if self._reasoning_engine is not None:
                task = asyncio.create_task(self._reason_and_emit(finding_id, finding, risk))
                self._pending_reasoning.add(task)
                task.add_done_callback(self._pending_reasoning.discard)

        latency_ms = (time.monotonic() - fast_path_start) * 1000
        logger.info(
            "orchestrator.fast_path frame_id=%d latency_ms=%.2f n_findings=%d",
            frame_id,
            latency_ms,
            len(detections),
        )

    async def _reason_and_emit(self, finding_id: int, finding: Finding, risk: RiskAssessment) -> None:
        assert self._reasoning_engine is not None
        loop = asyncio.get_running_loop()
        result, latency_ms = await loop.run_in_executor(
            None, self._reasoning_engine.reason, finding.evidence, risk
        )
        logger.info("orchestrator.reasoning finding_id=%d latency_ms=%.2f success=%s", finding_id, latency_ms, result is not None)

        if result is None:
            return

        updated = replace(
            finding,
            what=result.what,
            where=result.where,
            why=result.why,
            how=result.how,
            recommended_action=result.recommended_action,
            reasoning_latency_ms=latency_ms,
        )
        self._store.update_finding_reasoning(finding_id, updated)
        self._emit(updated, "finding.reasoned")
