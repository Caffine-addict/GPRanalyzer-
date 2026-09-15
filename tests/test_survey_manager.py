"""Tests for api/survey_manager.py."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import parsers.image  # noqa: F401 - registers the image parser as a side effect
from api.survey_manager import SurveyManager
from core.config import load_config
from core.contracts import Detection, ScanFrame, SourceCapabilities
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
    def detect(self, image: np.ndarray) -> list[Detection]:
        return [Detection(class_name="cavities", confidence=0.8, bbox_xyxy=(10.0, 10.0, 50.0, 50.0))]


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


def _manager(tmp_path: Path, frames_dir: Path, broadcast, reasoning_engine=None) -> tuple[SurveyManager, DuckDBStore]:
    cfg = _config(frames_dir)
    store = DuckDBStore(tmp_path / "test.duckdb")
    source = ReplaySource(cfg.source.replay)
    manager = SurveyManager(
        source=source,
        source_type="replay",
        detector=_FakeDetector(),
        store=store,
        config=cfg,
        broadcast=broadcast,
        reasoning_engine=reasoning_engine,
    )
    return manager, store


@pytest.mark.asyncio
async def test_start_survey_creates_running_record(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 2)
    broadcasts: list[dict] = []
    manager, store = _manager(tmp_path, frames_dir, broadcasts.append)

    record = manager.start_survey("survey-1")
    assert record.survey_id == "survey-1"
    assert record.status == "running"
    assert record.line_id == "line_1"
    assert record.source_type == "replay"

    await record.task
    store.close()


@pytest.mark.asyncio
async def test_start_survey_after_prior_completion_succeeds(tmp_path: Path) -> None:
    # start_survey's "already running" guard checks status == "running", not
    # merely "seen before" — a survey_id must be restartable once its prior
    # run has actually finished.
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    manager, store = _manager(tmp_path, frames_dir, lambda m: _noop())

    first = manager.start_survey("survey-1")
    await first.task
    assert first.status == "completed"

    second = manager.start_survey("survey-1")  # must not raise
    assert second.status == "running"
    assert second is not first

    await second.task
    store.close()


@pytest.mark.asyncio
async def test_start_survey_twice_while_running_raises(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 2)
    manager, store = _manager(tmp_path, frames_dir, lambda m: _noop())

    record = manager.start_survey("survey-1")
    with pytest.raises(ValueError, match="already running"):
        manager.start_survey("survey-1")

    await record.task
    store.close()


async def _noop():
    return None


@pytest.mark.asyncio
async def test_survey_completes_and_status_updates(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 2)

    async def broadcast(message: dict) -> None:
        pass

    manager, store = _manager(tmp_path, frames_dir, broadcast)
    record = manager.start_survey("survey-1")

    await record.task

    assert record.status == "completed"
    assert record.stopped_at is not None
    store.close()


@pytest.mark.asyncio
async def test_broadcast_receives_finding_created_and_reasoned(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    broadcasts: list[dict] = []

    async def broadcast(message: dict) -> None:
        broadcasts.append(message)

    cfg = _config(frames_dir)
    engine = ReasoningEngine(_FakeLLMClient(), cfg.reasoning)
    manager, store = _manager(tmp_path, frames_dir, broadcast, reasoning_engine=engine)

    record = manager.start_survey("survey-1")
    await record.task
    # Reasoning tasks are dispatched via asyncio.create_task inside emit();
    # give the loop a chance to run them to completion.
    await asyncio.sleep(0.05)

    types = [m["type"] for m in broadcasts]
    assert "finding.created" in types
    assert "finding.reasoned" in types
    assert all(m["survey_id"] == "survey-1" for m in broadcasts)
    assert all("finding" in m for m in broadcasts)

    # finding_id lets a client correlate "created" and "reasoned" for the same row — both
    # events here are for the survey's one finding, so they must carry the same real id.
    created = next(m for m in broadcasts if m["type"] == "finding.created")
    reasoned = next(m for m in broadcasts if m["type"] == "finding.reasoned")
    assert isinstance(created["finding_id"], int)
    assert created["finding_id"] == reasoned["finding_id"]
    store.close()


@pytest.mark.asyncio
async def test_stop_survey_cancels_and_marks_stopped(tmp_path: Path) -> None:
    class _SlowSource(ScanSource):
        def frames(self):
            import time

            for _ in range(5):
                time.sleep(0.3)
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

    cfg = _config(tmp_path)  # unused directory, custom source below
    store = DuckDBStore(tmp_path / "test.duckdb")
    manager = SurveyManager(
        source=_SlowSource(),
        source_type="synthetic",
        detector=_FakeDetector(),
        store=store,
        config=cfg,
        broadcast=lambda m: _noop(),
        reasoning_engine=None,
    )

    record = manager.start_survey("survey-1")
    await asyncio.sleep(0.1)  # let it start, still mid-first-frame-wait
    assert record.status == "running"

    stopped = await manager.stop_survey("survey-1")
    assert stopped.status == "stopped"
    assert stopped.stopped_at is not None
    store.close()


@pytest.mark.asyncio
async def test_stop_survey_unknown_id_raises_key_error(tmp_path: Path) -> None:
    manager, store = _manager(tmp_path, tmp_path, lambda m: _noop())
    with pytest.raises(KeyError):
        await manager.stop_survey("nonexistent")
    store.close()


@pytest.mark.asyncio
async def test_stop_survey_not_running_raises_value_error(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    manager, store = _manager(tmp_path, frames_dir, lambda m: _noop())

    record = manager.start_survey("survey-1")
    await record.task  # let it complete naturally

    with pytest.raises(ValueError, match="not running"):
        await manager.stop_survey("survey-1")
    store.close()


@pytest.mark.asyncio
async def test_list_and_get_survey(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    manager, store = _manager(tmp_path, frames_dir, lambda m: _noop())

    assert manager.list_surveys() == []
    assert manager.get_survey("survey-1") is None

    record = manager.start_survey("survey-1")
    assert manager.get_survey("survey-1") is record
    assert manager.list_surveys() == [record]

    await record.task
    store.close()


class _OneImmediateThenPacedSource(ScanSource):
    """Frame 1 is available immediately; frame 2 sits behind a real blocking sleep — puts a
    reasoning task genuinely in flight while stop_survey's outer-task cancellation is still
    working through the frame-fetch it's currently blocked on (asyncio can't interrupt a
    blocking call already running in a thread-pool worker — see
    pipeline/orchestrator.py's cancel_pending_reasoning docstring).
    """

    def __init__(self, second_frame_delay_s: float) -> None:
        self._second_frame_delay_s = second_frame_delay_s

    def frames(self):
        yield ScanFrame(
            source_type="synthetic",
            provenance={},
            image=np.full((64, 64), 100, dtype=np.uint8),
            position=None,
            position_source="unknown",
        )
        time.sleep(self._second_frame_delay_s)
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


class _SlowLLMClient:
    def __init__(self, delay_s: float) -> None:
        self._delay_s = delay_s

    def complete_json(self, prompt, *, schema, model, temperature, max_tokens, timeout_s, strict):
        time.sleep(self._delay_s)
        return _VALID_RESPONSE


@pytest.mark.asyncio
async def test_stop_survey_cancels_in_flight_reasoning_without_stale_emit(tmp_path: Path) -> None:
    cfg = _config(tmp_path)  # unused directory, custom source below
    store = DuckDBStore(tmp_path / "test.duckdb")
    # Long enough that the reasoning task is still genuinely running (not
    # already finished by chance) when stop_survey cancels it.
    engine = ReasoningEngine(_SlowLLMClient(delay_s=0.6), cfg.reasoning)
    broadcasts: list[dict] = []

    async def broadcast(message: dict) -> None:
        broadcasts.append(message)

    manager = SurveyManager(
        source=_OneImmediateThenPacedSource(second_frame_delay_s=0.2),
        source_type="synthetic",
        detector=_FakeDetector(),
        store=store,
        config=cfg,
        broadcast=broadcast,
        reasoning_engine=engine,
    )

    manager.start_survey("survey-1")
    await asyncio.sleep(0.1)  # let frame 1 process, dispatch reasoning, and start running it

    start = time.monotonic()
    stopped = await manager.stop_survey("survey-1")
    elapsed = time.monotonic() - start

    # Cancelling a task awaiting run_in_executor() resolves near-instantly
    # regardless of whether its blocking call has already started in a
    # worker thread (confirmed empirically) — stop_survey must not block
    # for anywhere near the 0.6s reasoning delay.
    assert elapsed < 0.5
    assert stopped.status == "stopped"
    assert stopped.orchestrator is not None
    assert stopped.orchestrator._pending_reasoning == set()  # fully drained, nothing orphaned

    # The in-flight reasoning call was genuinely discarded, not silently
    # left to land later under a survey_id a caller may have since reused:
    # no stale store write, no stale broadcast.
    [persisted] = store.get_findings_by_line("survey-1", "line_1")
    assert persisted.what is None
    assert not any(m["type"] == "finding.reasoned" for m in broadcasts)

    store.close()


class _FailingUpdateStore:
    """Wraps a real Store, but update_finding_reasoning always raises — proves an unexpected
    failure during reasoning is caught at the survey level (record marked "failed") rather
    than crashing, same "must survive" principle applied one layer up.
    """

    def __init__(self, inner: DuckDBStore) -> None:
        self._inner = inner

    def save_frame(self, *args, **kwargs):
        return self._inner.save_frame(*args, **kwargs)

    def save_finding(self, *args, **kwargs):
        return self._inner.save_finding(*args, **kwargs)

    def update_finding_reasoning(self, *args, **kwargs):
        raise RuntimeError("simulated store failure")

    def get_findings_by_line(self, *args, **kwargs):
        return self._inner.get_findings_by_line(*args, **kwargs)

    def get_findings_by_survey(self, *args, **kwargs):
        return self._inner.get_findings_by_survey(*args, **kwargs)

    def get_findings_for_line_id(self, *args, **kwargs):
        return self._inner.get_findings_for_line_id(*args, **kwargs)

    def get_prior_passes(self, *args, **kwargs):
        return self._inner.get_prior_passes(*args, **kwargs)

    def get_survey_summary(self, *args, **kwargs):
        return self._inner.get_survey_summary(*args, **kwargs)


@pytest.mark.asyncio
async def test_survey_marked_failed_on_unexpected_error_during_reasoning(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    cfg = _config(frames_dir)
    real_store = DuckDBStore(tmp_path / "test.duckdb")
    failing_store = _FailingUpdateStore(real_store)
    engine = ReasoningEngine(_FakeLLMClient(), cfg.reasoning)

    manager = SurveyManager(
        source=ReplaySource(cfg.source.replay),
        source_type="replay",
        detector=_FakeDetector(),
        store=failing_store,
        config=cfg,
        broadcast=lambda m: _noop(),
        reasoning_engine=engine,
    )

    record = manager.start_survey("survey-1")
    await record.task

    assert record.status == "failed"
    assert record.stopped_at is not None
    real_store.close()


@pytest.mark.asyncio
async def test_survey_record_to_dict_includes_capabilities(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    manager, store = _manager(tmp_path, frames_dir, lambda m: _noop())

    record = manager.start_survey("survey-1")
    d = record.to_dict()
    assert d["survey_id"] == "survey-1"
    assert d["status"] == "running"
    assert d["capabilities"]["has_calibrated_depth"] is False
    assert d["source_type"] == "replay"

    await record.task
    store.close()
