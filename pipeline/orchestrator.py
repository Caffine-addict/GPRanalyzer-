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
from core.contracts import Detection, Finding, ScanFrame, SourceCapabilities, TraceBox
from detect.model import ModelLoadError, ModelNotFoundError
from detect.refine import box_in_traces, refine_detections
from evidence.extract import extract_evidence
from preprocess.enhance import enhance
from reason.engine import ReasoningEngine
from render.bscan import traces_to_image
from risk.score import RiskAssessment, score_detections
from sources.base import ScanSource
from store.base import Store

logger = logging.getLogger(__name__)

EmitCallback = Callable[[int, Finding, str], None]
FrameCallback = Callable[[ScanFrame], None]

# A streamed line arrives as overlapping windows, so the same target is seen in several of them,
# and a box touching the window's leading edge may still be growing. A detection is reported
# once its box ends this many traces behind the newest trace (or the line is complete), and
# never again once reported. 48 traces is 1.2 m on the SPR wheel encoder.
SETTLE_MARGIN_TRACES = 48
# ...and once it has been found, settled, in this many consecutive windows. Early windows see
# only part of the line, so the detector's per-row normalisation differs from the whole line's
# and throws up boxes that are gone a window later; persistence is what separates those from
# targets. Measured on the four delivered lines by scripts/compare_live_batch.py (16-trace
# chunks): every setting found all 88 whole-line targets; findings with no whole-line match fell
# 67 (no persistence, margin 32) -> 48 (2 windows) -> 13 (4 windows, margin 48), flat beyond.
# The price is latency: a target is reported ~margin + 3 chunks = 96 traces (2.4 m, ~1.7 s at
# walking pace) after the antenna passes it.
CONFIRM_WINDOWS = 4

# Reasoning calls in flight at once, per survey. A streamed line can report a dozen findings in a
# few seconds; dispatching them all together flooded the provider (connection errors, then 429s
# on Groq's free tier) and lost most of the explanations. The rest wait their turn instead.
MAX_CONCURRENT_REASONING = 2


class DetectorLike(Protocol):
    """Whatever detect/model.py's Detector implements — a Protocol so tests can substitute a
    fake without needing real YOLO weights.
    """

    def detect(self, image: np.ndarray, frame: ScanFrame) -> list[Detection]: ...


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
        emit_frame: FrameCallback | None = None,
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
        self._reasoning_slots = asyncio.Semaphore(MAX_CONCURRENT_REASONING)
        self._emit_frame = emit_frame
        self._line_key: object = None
        self._reported: list[TraceBox] = []
        self._candidates: list[tuple[TraceBox, int]] = []  # settled, not yet reported: (box, windows seen)

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
        if self._emit_frame is not None:
            self._emit_frame(frame)

        if frame.image is not None:
            image = frame.image
        else:
            assert frame.traces is not None, "ScanFrame guarantees traces or image"
            image = traces_to_image(frame.traces)

        enhanced = enhance(image, self._config.enhancement)

        try:
            detections = self._detector.detect(enhanced, frame)
        except (ModelNotFoundError, ModelLoadError) as e:
            logger.warning("orchestrator.detect_unavailable frame_id=%d error=%s", frame_id, e)
            return

        # The detector finds shapes; risk, evidence and reasoning speak the taxonomy.
        # Raw traces go in only when this frame's image was rendered from them here —
        # a source-supplied image has no known mapping from box pixels onto samples.
        refinements = refine_detections(
            detections,
            taxonomy=self._config.detection.taxonomy,
            image_shape=enhanced.shape,
            traces=frame.traces if frame.image is None else None,
            sample_interval_ns=frame.sample_interval_ns if frame.image is None else None,
        )
        detections = [refinement.detection for refinement in refinements]
        to_report = self._settled_and_new(frame, enhanced.shape, detections)

        if not to_report:
            latency_ms = (time.monotonic() - fast_path_start) * 1000
            logger.info("orchestrator.fast_path frame_id=%d latency_ms=%.2f n_findings=0", frame_id, latency_ms)
            return

        risk = score_detections(detections, self._config.risk)

        for detection, location in to_report:
            neighbours = tuple(d.class_name for d in detections if d is not detection)
            evidence = extract_evidence(
                detection,
                frame,
                capabilities,
                self._config.evidence,
                neighbours=neighbours,
                image_shape=enhanced.shape,
            )
            finding = Finding(
                evidence=evidence,
                risk_level=risk.level,
                risk_score=risk.score,
                risk_rules_fired=risk.rules_fired,
                location=location,
            )

            finding_id = self._store.save_finding(self._survey_id, self._line_id, frame_id, finding)
            self._emit(finding_id, finding, "finding.created")

            if self._reasoning_engine is not None:
                task = asyncio.create_task(self._reason_and_emit(finding_id, finding, risk))
                self._pending_reasoning.add(task)
                task.add_done_callback(self._pending_reasoning.discard)

        latency_ms = (time.monotonic() - fast_path_start) * 1000
        logger.info(
            "orchestrator.fast_path frame_id=%d latency_ms=%.2f n_findings=%d",
            frame_id,
            latency_ms,
            len(to_report),
        )

    def _settled_and_new(
        self, frame: ScanFrame, image_shape: tuple[int, ...], detections: list[Detection]
    ) -> list[tuple[Detection, TraceBox | None]]:
        """The detections to report from this frame, each with where it sits on its line.

        Risk and neighbours still see every detection in the frame; this only decides which
        become Findings. An image file reports everything, located in pixels. A whole line
        delivered at once reports everything, located in traces. A streamed line reports each
        target once, when it has settled and persisted — see SETTLE_MARGIN_TRACES and
        CONFIRM_WINDOWS.
        """
        if frame.traces is None:
            # An image's native units are its pixels, and the detector's boxes already are those.
            return [(d, _pixel_box(d)) for d in detections]
        streamed = frame.trace_offset is not None
        offset = frame.trace_offset or 0
        line_key = frame.provenance.get("path")
        if line_key != self._line_key:
            self._line_key, self._reported, self._candidates = line_key, [], []
        window_end = offset + frame.traces.shape[0]

        out: list[tuple[Detection, TraceBox | None]] = []
        candidates: list[tuple[TraceBox, int]] = []
        for detection in detections:
            rows, cols = box_in_traces(detection.bbox_xyxy, image_shape, frame.traces.shape)
            box = TraceBox(offset + cols.start, offset + cols.stop, rows.start, rows.stop)
            if streamed:
                if not frame.line_complete and box.trace_end > window_end - SETTLE_MARGIN_TRACES:
                    continue
                if any(_same_target(box, seen) for seen in self._reported):
                    continue
                seen_for = 1 + max((n for c, n in self._candidates if _same_target(box, c)), default=0)
                # The final window is the whole line, so what it finds stands without a streak.
                if seen_for < CONFIRM_WINDOWS and not frame.line_complete:
                    candidates.append((box, seen_for))
                    continue
                self._reported.append(box)
            out.append((detection, box))
        if streamed:
            self._candidates = candidates  # a candidate missing from this window starts over
        return out

    async def _reason_and_emit(self, finding_id: int, finding: Finding, risk: RiskAssessment) -> None:
        assert self._reasoning_engine is not None
        loop = asyncio.get_running_loop()
        async with self._reasoning_slots:
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
        self._emit(finding_id, updated, "finding.reasoned")


def _same_target(a: TraceBox, b: TraceBox) -> bool:
    """Two boxes from overlapping windows describe one target: they overlap in depth, and along
    the line by at least half the narrower box. Window-to-window re-normalisation moves a box's
    edges by a few traces; it does not move it off its own target."""
    trace_overlap = min(a.trace_end, b.trace_end) - max(a.trace_start, b.trace_start)
    sample_overlap = min(a.sample_end, b.sample_end) - max(a.sample_start, b.sample_start)
    narrower = min(a.trace_end - a.trace_start, b.trace_end - b.trace_start)
    return sample_overlap > 0 and trace_overlap >= 0.5 * narrower


def _pixel_box(detection: Detection) -> TraceBox:
    x1, y1, x2, y2 = detection.bbox_xyxy
    left, top = int(x1), int(y1)
    return TraceBox(left, max(left + 1, round(x2)), top, max(top + 1, round(y2)))
