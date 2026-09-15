"""Tests for pipeline/orchestrator.py.

The integration test runs a real ReplaySource over fixture frames with a
mocked detector (no real YOLO weights exist yet) and a mocked LLM client (no
real Groq key), asserting: every frame with detections produces findings,
fast-path emission precedes reasoning emission for each finding, fast-path
latency stays under config.yaml's target, and findings are actually
persisted to a real (temp-file) DuckDB store.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import parsers.image  # noqa: F401 - registers the image parser as a side effect
from core.config import load_config
from core.contracts import Detection, Finding, ScanFrame, SourceCapabilities
from detect.model import ModelNotFoundError
from pipeline.orchestrator import Orchestrator
from reason.engine import ReasoningEngine
from sources.base import ScanSource
from sources.replay import ReplaySource
from store.duckdb_store import DuckDBStore

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_fixture_frames(directory: Path, n: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.fromarray(np.full((64, 64), 100, dtype=np.uint8)).save(directory / f"{i:03d}.jpg")


def _config(frames_dir: Path):
    from dataclasses import replace

    cfg = load_config(REPO_ROOT / "config.yaml")
    return replace(
        cfg,
        source=replace(
            cfg.source,
            replay=replace(cfg.source.replay, directory=str(frames_dir), step_mode=True),
        ),
    )


class _FakeDetector:
    """Returns one detection per frame, with a unique confidence per call so tests can
    correlate a given finding's "created" and "reasoned" emissions.
    """

    def __init__(self) -> None:
        self.call_count = 0

    def detect(self, image: np.ndarray) -> list[Detection]:
        self.call_count += 1
        confidence = 0.5 + self.call_count * 0.01
        return [Detection(class_name="cavities", confidence=confidence, bbox_xyxy=(10.0, 10.0, 50.0, 50.0))]


class _EmptyDetector:
    def detect(self, image: np.ndarray) -> list[Detection]:
        return []


class _RaisingDetector:
    def detect(self, image: np.ndarray) -> list[Detection]:
        raise ModelNotFoundError("no weights configured")


class _MultiClassDetector:
    """Returns two different-class detections per frame — cavities + a utility-like class,
    which should trigger risk/score.py's cavities_with_utility escalation rule.
    """

    def detect(self, image: np.ndarray) -> list[Detection]:
        return [
            Detection(class_name="cavities", confidence=0.5, bbox_xyxy=(5.0, 5.0, 15.0, 15.0)),
            Detection(
                class_name="elongated_linear_target", confidence=0.5, bbox_xyxy=(20.0, 20.0, 30.0, 30.0)
            ),
        ]


_VALID_RESPONSE = {
    "what": "a reflector",
    "where": "unavailable",
    "why": "moderate confidence",
    "how": "low overall confidence",
    "recommended_action": "confirm with a second pass",
}


class _FakeLLMClient:
    def complete_json(self, prompt, *, schema, model, temperature, max_tokens, timeout_s, strict):
        return _VALID_RESPONSE


@pytest.mark.asyncio
async def test_orchestrator_end_to_end_with_reasoning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 3)
    cfg = _config(frames_dir)

    source = ReplaySource(cfg.source.replay)
    store = DuckDBStore(tmp_path / "test.duckdb")
    detector = _FakeDetector()
    engine = ReasoningEngine(_FakeLLMClient(), cfg.reasoning)

    emitted: list[tuple[Finding, str]] = []
    orchestrator = Orchestrator(
        source=source,
        detector=detector,
        store=store,
        config=cfg,
        emit=lambda finding_id, finding, event_type: emitted.append((finding, event_type)),
        survey_id="survey-1",
        reasoning_engine=engine,
    )

    with caplog.at_level(logging.INFO, logger="pipeline.orchestrator"):
        await orchestrator.run()
        await orchestrator.wait_for_pending_reasoning()

    created = [(f, t) for f, t in emitted if t == "finding.created"]
    reasoned = [(f, t) for f, t in emitted if t == "finding.reasoned"]
    assert len(created) == 3  # one detection per frame, 3 frames
    assert len(reasoned) == 3

    # Fast-path emission must precede reasoning emission for each finding
    # individually — correlate by the fake detector's unique per-call
    # confidence rather than assuming a strict global ordering, since
    # reasoning tasks for different frames can complete out of order.
    created_index = {f.evidence.detection_confidence: i for i, (f, _) in enumerate(emitted) if _ == "finding.created"}
    reasoned_index = {f.evidence.detection_confidence: i for i, (f, _) in enumerate(emitted) if _ == "finding.reasoned"}
    assert set(created_index) == set(reasoned_index)
    for confidence, created_at in created_index.items():
        assert created_at < reasoned_index[confidence]

    # Reasoned findings actually carry the reasoning fields.
    for finding, _ in reasoned:
        assert finding.what == _VALID_RESPONSE["what"]
        assert finding.recommended_action == _VALID_RESPONSE["recommended_action"]
        assert finding.reasoning_latency_ms is not None

    # Persisted to the real store, not just emitted in-memory.
    persisted = store.get_findings_by_line("survey-1", "line_1")
    assert len(persisted) == 3
    assert all(f.what is not None for f in persisted)  # reasoning updates landed in storage too

    # Fast-path latency stayed under config's target.
    fast_path_latencies = [
        float(m.group(1))
        for record in caplog.records
        if (m := re.search(r"orchestrator\.fast_path.*latency_ms=([\d.]+)", record.getMessage()))
    ]
    assert len(fast_path_latencies) == 3
    assert all(latency < cfg.latency.fast_path_target_ms for latency in fast_path_latencies)

    store.close()


@pytest.mark.asyncio
async def test_orchestrator_without_reasoning_engine_only_emits_created(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 2)
    cfg = _config(frames_dir)

    store = DuckDBStore(tmp_path / "test.duckdb")
    emitted: list[tuple[Finding, str]] = []
    orchestrator = Orchestrator(
        source=ReplaySource(cfg.source.replay),
        detector=_FakeDetector(),
        store=store,
        config=cfg,
        emit=lambda finding_id, finding, event_type: emitted.append((finding, event_type)),
        survey_id="survey-1",
        reasoning_engine=None,
    )

    await orchestrator.run()
    await orchestrator.wait_for_pending_reasoning()

    assert len(emitted) == 2
    assert all(event_type == "finding.created" for _, event_type in emitted)
    store.close()


@pytest.mark.asyncio
async def test_orchestrator_no_weights_skips_frames_without_crashing(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 2)
    cfg = _config(frames_dir)

    store = DuckDBStore(tmp_path / "test.duckdb")
    emitted: list[tuple[Finding, str]] = []
    orchestrator = Orchestrator(
        source=ReplaySource(cfg.source.replay),
        detector=_RaisingDetector(),
        store=store,
        config=cfg,
        emit=lambda finding_id, finding, event_type: emitted.append((finding, event_type)),
        survey_id="survey-1",
    )

    await orchestrator.run()  # must not raise

    assert emitted == []
    assert store.get_survey_summary("survey-1")["total_findings"] == 0
    store.close()


@pytest.mark.asyncio
async def test_orchestrator_empty_detections_produces_no_findings(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    cfg = _config(frames_dir)

    store = DuckDBStore(tmp_path / "test.duckdb")
    emitted: list[tuple[Finding, str]] = []
    orchestrator = Orchestrator(
        source=ReplaySource(cfg.source.replay),
        detector=_EmptyDetector(),
        store=store,
        config=cfg,
        emit=lambda finding_id, finding, event_type: emitted.append((finding, event_type)),
        survey_id="survey-1",
    )

    await orchestrator.run()

    assert emitted == []
    # The frame itself is still persisted even with zero detections.
    store.close()


@pytest.mark.asyncio
async def test_orchestrator_reasoning_failure_still_leaves_finding_persisted(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    cfg = _config(frames_dir)

    class _FailingLLMClient:
        def complete_json(self, prompt, *, schema, model, temperature, max_tokens, timeout_s, strict):
            raise RuntimeError("network error")

    store = DuckDBStore(tmp_path / "test.duckdb")
    engine = ReasoningEngine(_FailingLLMClient(), cfg.reasoning)
    emitted: list[tuple[Finding, str]] = []
    orchestrator = Orchestrator(
        source=ReplaySource(cfg.source.replay),
        detector=_FakeDetector(),
        store=store,
        config=cfg,
        emit=lambda finding_id, finding, event_type: emitted.append((finding, event_type)),
        survey_id="survey-1",
        reasoning_engine=engine,
    )

    await orchestrator.run()
    await orchestrator.wait_for_pending_reasoning()

    # Only the fast-path emission happened — no "finding.reasoned" event —
    # but nothing crashed and the finding survives in storage as-is.
    assert [t for _, t in emitted] == ["finding.created"]
    persisted = store.get_findings_by_line("survey-1", "line_1")
    assert len(persisted) == 1
    assert persisted[0].what is None
    store.close()


@pytest.mark.asyncio
async def test_orchestrator_emits_the_real_per_finding_id_not_the_shared_frame_id(tmp_path: Path) -> None:
    # Every other test in this file discards emit()'s own first argument (its lambda is
    # `lambda finding_id, finding, event_type: ...` but never reads finding_id), so a bug that
    # emitted frame_id — or any other single shared value — instead of each finding's own
    # store.save_finding() id would pass every one of them. One frame producing two findings
    # (_MultiClassDetector) is the smallest fixture where "the real id" and "the frame id"
    # provably diverge: with the wiring correct the two findings get two different ids; with
    # frame_id substituted, both would incorrectly share the same value.
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    cfg = _config(frames_dir)

    store = DuckDBStore(tmp_path / "test.duckdb")
    engine = ReasoningEngine(_FakeLLMClient(), cfg.reasoning)
    created_ids: list[int] = []
    reasoned_ids: list[int] = []

    def emit(finding_id: int, finding: Finding, event_type: str) -> None:
        (created_ids if event_type == "finding.created" else reasoned_ids).append(finding_id)

    orchestrator = Orchestrator(
        source=ReplaySource(cfg.source.replay),
        detector=_MultiClassDetector(),
        store=store,
        config=cfg,
        emit=emit,
        survey_id="survey-1",
        reasoning_engine=engine,
    )

    await orchestrator.run()
    await orchestrator.wait_for_pending_reasoning()

    assert len(created_ids) == 2
    assert len(set(created_ids)) == 2  # two real, distinct ids -- not one shared frame_id

    # Each finding_id must be the one store.save_finding() actually assigned that finding: a
    # reasoning update keyed by the wrong id silently touches zero rows (`WHERE finding_id = ?`
    # matches nothing) rather than crashing, so both findings ending up reasoned is the proof
    # both real ids, not one repeated id, reached _reason_and_emit / update_finding_reasoning.
    persisted = store.get_findings_by_line("survey-1", "line_1")
    assert len(persisted) == 2
    assert all(f.what == _VALID_RESPONSE["what"] for f in persisted)

    # created/reasoned pairing (Session 8's finding_id correlation contract) must still hold
    # per-finding once there's more than one finding sharing a frame.
    assert set(created_ids) == set(reasoned_ids)
    store.close()


class _SingleTracesOnlyFrameSource(ScanSource):
    """No real source produces a traces-only ScanFrame yet — this exists purely to exercise
    the orchestrator's render/bscan.py fallback path (frame.image is None, frame.traces isn't).
    """

    def frames(self):
        yield ScanFrame(
            source_type="synthetic_traces",
            provenance={},
            traces=np.random.default_rng(0).normal(size=(20, 300)),
            position=None,
            position_source="unknown",
            sample_interval_ns=0.1,
        )

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            has_calibrated_depth=False,
            has_real_position=False,
            has_true_amplitude=False,
            latency_class="batch",
        )


@pytest.mark.asyncio
async def test_orchestrator_renders_traces_only_frame_via_render_bscan(tmp_path: Path) -> None:
    cfg = _config(tmp_path / "unused")  # replay source unused here, just need a valid Config
    store = DuckDBStore(tmp_path / "test.duckdb")
    emitted: list[tuple[Finding, str]] = []
    orchestrator = Orchestrator(
        source=_SingleTracesOnlyFrameSource(),
        detector=_FakeDetector(),
        store=store,
        config=cfg,
        emit=lambda finding_id, finding, event_type: emitted.append((finding, event_type)),
        survey_id="survey-1",
    )

    await orchestrator.run()  # must not raise: traces -> render/bscan.py -> enhance -> detect

    assert len(emitted) == 1
    assert emitted[0][1] == "finding.created"
    store.close()


@pytest.mark.asyncio
async def test_orchestrator_shares_frame_wide_risk_across_detections(tmp_path: Path) -> None:
    # Documents and locks in a deliberate design choice (see core/contracts.py's
    # Finding docstring): risk is scored once per frame, not per detection,
    # because escalation rules need cross-detection context. Two different
    # classes co-occurring in one frame (cavities + a utility-like class)
    # must both end up HIGH via the cavities_with_utility rule, with
    # identical risk_level/rules_fired — not independently-scored MEDIUM/LOW.
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    cfg = _config(frames_dir)

    store = DuckDBStore(tmp_path / "test.duckdb")
    emitted: list[tuple[Finding, str]] = []
    orchestrator = Orchestrator(
        source=ReplaySource(cfg.source.replay),
        detector=_MultiClassDetector(),
        store=store,
        config=cfg,
        emit=lambda finding_id, finding, event_type: emitted.append((finding, event_type)),
        survey_id="survey-1",
    )

    await orchestrator.run()

    findings = [f for f, _ in emitted]
    assert len(findings) == 2
    assert {f.evidence.detection_class for f in findings} == {"cavities", "elongated_linear_target"}
    assert all(f.risk_level == "HIGH" for f in findings)
    assert all(f.risk_rules_fired == ("cavities_with_utility",) for f in findings)
    assert findings[0].risk_score == findings[1].risk_score
    store.close()


class _OneBadFrameThenGoodSource(ScanSource):
    """First frame has traces containing NaN (frame.image=None, so render/bscan.py's
    traces_to_image raises ValueError) — an unexpected failure mode not covered by the
    orchestrator's specific ModelNotFoundError/ModelLoadError handling. Second frame is
    a normal image-bearing frame.
    """

    def __init__(self, good_image: np.ndarray) -> None:
        self._good_image = good_image

    def frames(self):
        yield ScanFrame(
            source_type="synthetic",
            provenance={},
            traces=np.array([[0.0, np.nan], [1.0, 2.0]]),
            position=None,
            position_source="unknown",
        )
        yield ScanFrame(
            source_type="synthetic",
            provenance={},
            image=self._good_image,
            position=None,
            position_source="unknown",
        )

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            has_calibrated_depth=False,
            has_real_position=False,
            has_true_amplitude=False,
            latency_class="batch",
        )


@pytest.mark.asyncio
async def test_orchestrator_one_bad_frame_does_not_abort_the_survey(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    good_image = np.full((64, 64), 100, dtype=np.uint8)
    cfg = _config(tmp_path)  # source unused (custom source below), just need a valid Config
    store = DuckDBStore(tmp_path / "test.duckdb")
    emitted: list[tuple[Finding, str]] = []
    orchestrator = Orchestrator(
        source=_OneBadFrameThenGoodSource(good_image),
        detector=_FakeDetector(),
        store=store,
        config=cfg,
        emit=lambda finding_id, finding, event_type: emitted.append((finding, event_type)),
        survey_id="survey-1",
    )

    with caplog.at_level(logging.ERROR, logger="pipeline.orchestrator"):
        await orchestrator.run()  # must not raise: frame 1's failure must not stop frame 2

    assert len(emitted) == 1  # only frame 2 produced a finding
    assert emitted[0][1] == "finding.created"
    messages = [r.getMessage() for r in caplog.records]
    assert any("orchestrator.frame_failed" in m for m in messages)
    store.close()


class _SlowLLMClient:
    def complete_json(self, prompt, *, schema, model, temperature, max_tokens, timeout_s, strict):
        time.sleep(1.0)  # much longer than any real fast_path_target_ms
        return _VALID_RESPONSE


@pytest.mark.asyncio
async def test_orchestrator_fast_path_does_not_block_on_slow_reasoning(tmp_path: Path) -> None:
    # The core "fast path never awaits reasoning" guarantee, proven rather
    # than assumed: with a mocked LLM client too fast to matter, run() and
    # an inline-awaited reasoning call would look identical to the test
    # suite. A deliberately slow client makes the difference observable.
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    cfg = _config(frames_dir)

    store = DuckDBStore(tmp_path / "test.duckdb")
    engine = ReasoningEngine(_SlowLLMClient(), cfg.reasoning)
    emitted: list[tuple[Finding, str]] = []
    orchestrator = Orchestrator(
        source=ReplaySource(cfg.source.replay),
        detector=_FakeDetector(),
        store=store,
        config=cfg,
        emit=lambda finding_id, finding, event_type: emitted.append((finding, event_type)),
        survey_id="survey-1",
        reasoning_engine=engine,
    )

    start = time.monotonic()
    # Well under the 1s reasoning delay — if reasoning were awaited inline,
    # this would time out.
    await asyncio.wait_for(orchestrator.run(), timeout=0.5)
    elapsed = time.monotonic() - start

    assert elapsed < 0.5
    assert [t for _, t in emitted] == ["finding.created"]  # reasoning hasn't landed yet

    await orchestrator.wait_for_pending_reasoning()
    assert [t for _, t in emitted] == ["finding.created", "finding.reasoned"]
    store.close()


@pytest.mark.asyncio
async def test_cancel_pending_reasoning_discards_in_flight_task_without_stale_emit(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    cfg = _config(frames_dir)

    store = DuckDBStore(tmp_path / "test.duckdb")
    engine = ReasoningEngine(_SlowLLMClient(), cfg.reasoning)  # 1.0s delay
    emitted: list[tuple[Finding, str]] = []
    orchestrator = Orchestrator(
        source=ReplaySource(cfg.source.replay),
        detector=_FakeDetector(),
        store=store,
        config=cfg,
        emit=lambda finding_id, finding, event_type: emitted.append((finding, event_type)),
        survey_id="survey-1",
        reasoning_engine=engine,
    )

    await orchestrator.run()  # dispatches the single frame's reasoning task, doesn't wait for it
    await asyncio.sleep(0.1)  # let the reasoning task's executor call actually start running
    assert len(orchestrator._pending_reasoning) == 1  # still mid-flight in the 1s-slow LLM call

    start = time.monotonic()
    await orchestrator.cancel_pending_reasoning()
    elapsed = time.monotonic() - start

    # Cancelling a task awaiting run_in_executor() resolves near-instantly
    # regardless of whether the blocking call has already started in a
    # worker thread (asyncio.Future.cancel() succeeds unconditionally on a
    # pending future) — confirmed empirically, not assumed. The orphaned
    # thread keeps sleeping in the background, but its result is discarded:
    # store.update_finding_reasoning()/emit() never run for this task.
    assert elapsed < 0.5
    assert orchestrator._pending_reasoning == set()
    assert [t for _, t in emitted] == ["finding.created"]  # no stale "finding.reasoned"
    store.close()


class _TwoValueEqualDetectionsDetector:
    """Two separately-constructed but value-equal Detection objects (Detection is a frozen
    dataclass with default, value-based equality) — distinguishes `is not` (identity) from
    `!=` (equality) exclusion in the orchestrator's neighbours computation.
    """

    def detect(self, image: np.ndarray) -> list[Detection]:
        return [
            Detection(class_name="cavities", confidence=0.8, bbox_xyxy=(10.0, 10.0, 50.0, 50.0)),
            Detection(class_name="cavities", confidence=0.8, bbox_xyxy=(10.0, 10.0, 50.0, 50.0)),
        ]


@pytest.mark.asyncio
async def test_orchestrator_neighbours_uses_identity_not_equality(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    cfg = _config(frames_dir)

    store = DuckDBStore(tmp_path / "test.duckdb")
    emitted: list[tuple[Finding, str]] = []
    orchestrator = Orchestrator(
        source=ReplaySource(cfg.source.replay),
        detector=_TwoValueEqualDetectionsDetector(),
        store=store,
        config=cfg,
        emit=lambda finding_id, finding, event_type: emitted.append((finding, event_type)),
        survey_id="survey-1",
    )

    await orchestrator.run()

    findings = [f for f, _ in emitted]
    assert len(findings) == 2
    # Using `!=` (value equality) instead of `is not` (identity) would
    # wrongly exclude the other detection too, since both are value-equal —
    # each finding's neighbours would be () instead of ("cavities",).
    assert all(f.evidence.neighbours == ("cavities",) for f in findings)
    store.close()


@pytest.mark.asyncio
async def test_orchestrator_store_write_happens_before_reasoned_emit(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    cfg = _config(frames_dir)

    store = DuckDBStore(tmp_path / "test.duckdb")
    engine = ReasoningEngine(_FakeLLMClient(), cfg.reasoning)
    read_back_at_reasoned_emit: list[Finding] = []

    def emit(finding_id: int, finding: Finding, event_type: str) -> None:
        if event_type == "finding.reasoned":
            # Read back from the SAME store instance synchronously, inside
            # the callback — if the store update happened after emit()
            # instead of before, this would still see the pre-reasoning row.
            [persisted] = store.get_findings_by_line("survey-1", "line_1")
            read_back_at_reasoned_emit.append(persisted)

    orchestrator = Orchestrator(
        source=ReplaySource(cfg.source.replay),
        detector=_FakeDetector(),
        store=store,
        config=cfg,
        emit=emit,
        survey_id="survey-1",
        reasoning_engine=engine,
    )

    await orchestrator.run()
    await orchestrator.wait_for_pending_reasoning()

    assert len(read_back_at_reasoned_emit) == 1
    assert read_back_at_reasoned_emit[0].what == _VALID_RESPONSE["what"]
    store.close()


class _SlowPacedSource(ScanSource):
    """A source with a real (blocking) pacing delay between frames, like ReplaySource with
    step_mode=False — used to prove run() doesn't freeze the whole event loop while waiting
    for the next frame.
    """

    def __init__(self, n_frames: int, delay_s: float) -> None:
        self._n_frames = n_frames
        self._delay_s = delay_s

    def frames(self):
        for _ in range(self._n_frames):
            time.sleep(self._delay_s)
            yield ScanFrame(
                source_type="synthetic",
                provenance={},
                image=np.full((64, 64), 100, dtype=np.uint8),
                position=None,
                position_source="unknown",
            )

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            has_calibrated_depth=False,
            has_real_position=False,
            has_true_amplitude=False,
            latency_class="batch",
        )


@pytest.mark.asyncio
async def test_orchestrator_run_does_not_block_the_event_loop_between_frames(tmp_path: Path) -> None:
    # A real (API-served) survey paces itself between frames (ReplaySource
    # honouring playback_rate_hz, or eventually real hardware). If run()'s
    # frame loop blocks the event loop during that pacing, the whole server
    # — including delivering the WebSocket messages this survey itself is
    # producing — freezes until the survey finishes, silently defeating the
    # entire point of streaming findings as they happen.
    cfg = _config(tmp_path)
    store = DuckDBStore(tmp_path / "test.duckdb")
    orchestrator = Orchestrator(
        source=_SlowPacedSource(n_frames=2, delay_s=0.3),
        detector=_FakeDetector(),
        store=store,
        config=cfg,
        emit=lambda finding_id, finding, event_type: None,
        survey_id="survey-1",
    )

    run_task = asyncio.create_task(orchestrator.run())

    other_progressed = False

    async def other_coroutine() -> None:
        nonlocal other_progressed
        await asyncio.sleep(0.05)
        other_progressed = True

    # Well under the 0.6s total pacing delay (2 frames x 0.3s) — if run()'s
    # loop were blocking the event loop, this would time out instead.
    await asyncio.wait_for(other_coroutine(), timeout=0.3)

    assert other_progressed
    assert not run_task.done()  # the slow survey is still in progress

    await run_task
    store.close()


class _ShapeDetector:
    """Stands in for a detector trained on the three shapes (detect/shapes.py)."""

    def __init__(self, class_name: str, bbox: tuple[float, float, float, float]) -> None:
        self.class_name, self.bbox = class_name, bbox

    def detect(self, image: np.ndarray) -> list[Detection]:
        return [Detection(class_name=self.class_name, confidence=0.8, bbox_xyxy=self.bbox)]


class _TracesFrameSource(ScanSource):
    def __init__(self, traces: np.ndarray) -> None:
        self._traces = traces

    def frames(self):
        yield ScanFrame(
            source_type="synthetic_traces",
            provenance={},
            traces=self._traces,
            position=None,
            position_source="unknown",
            sample_interval_ns=0.1,
        )

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            has_calibrated_depth=False, has_real_position=False, has_true_amplitude=False, latency_class="batch"
        )


def _line_with_a_strong_reflector() -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """384 x 256 traces with a direct wave and one strong reflector, plus the rendered-image box over it."""
    traces = np.random.default_rng(0).normal(0.0, 0.2, size=(384, 256))
    traces[:, 18:23] += [-3.0, 4.0, 10.0, 4.0, -3.0]
    traces[100:110, 120:126] += 8.0
    return traces, (98 / 384 * 640, 115 / 256 * 640, 112 / 384 * 640, 132 / 256 * 640)


async def _created_findings(source: ScanSource, detector, cfg, tmp_path: Path) -> list[Finding]:
    store = DuckDBStore(tmp_path / "test.duckdb")
    emitted: list[tuple[Finding, str]] = []
    orchestrator = Orchestrator(
        source=source, detector=detector, store=store, config=cfg,
        emit=lambda finding_id, finding, event_type: emitted.append((finding, event_type)), survey_id="survey-1",
    )
    await orchestrator.run()
    store.close()
    return [finding for finding, event in emitted if event == "finding.created"]


@pytest.mark.asyncio
async def test_a_shape_detection_reaches_risk_and_evidence_as_a_measured_taxonomy_class(tmp_path: Path) -> None:
    # The detector finds shapes; everything downstream speaks the taxonomy. On a traces
    # frame the refinement measures amplitude, so a strong reflector is reported as clear.
    traces, bbox = _line_with_a_strong_reflector()
    [finding] = await _created_findings(
        _TracesFrameSource(traces), _ShapeDetector("point_reflector", bbox), _config(tmp_path / "unused"), tmp_path
    )
    assert finding.evidence.detection_class == "clear_point_reflector"


@pytest.mark.asyncio
async def test_a_shape_detection_on_an_image_frame_falls_back_to_the_low_snr_class(tmp_path: Path) -> None:
    # A source-supplied image has no known mapping onto samples, so amplitude is not
    # measured and the class that claims least is reported.
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    cfg = _config(frames_dir)
    [finding] = await _created_findings(
        ReplaySource(cfg.source.replay), _ShapeDetector("point_reflector", (10.0, 10.0, 50.0, 50.0)), cfg, tmp_path
    )
    assert finding.evidence.detection_class == "low_snr_point_reflector"


@pytest.mark.asyncio
async def test_a_class_outside_both_shapes_and_taxonomy_never_becomes_a_finding(tmp_path: Path) -> None:
    traces, bbox = _line_with_a_strong_reflector()
    findings = await _created_findings(
        _TracesFrameSource(traces), _ShapeDetector("tractor", bbox), _config(tmp_path / "unused"), tmp_path
    )
    assert findings == []
