"""Survey lifecycle: starts/stops orchestrator runs, bridges each orchestrator's synchronous
emit callback to the async WebSocket broadcast. The orchestrator itself knows nothing about
WebSockets or surveys-as-a-concept — this module is where that wiring happens.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from api.schemas import capabilities_to_dict, finding_to_dict
from core.config import Config
from core.contracts import Finding, SourceCapabilities
from pipeline.orchestrator import DetectorLike, Orchestrator
from reason.engine import ReasoningEngine
from sources.base import ScanSource
from store.base import Store

logger = logging.getLogger(__name__)

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class SurveyRecord:
    survey_id: str
    line_id: str
    status: str  # "running" | "completed" | "stopped" | "failed"
    source_type: str
    capabilities: SourceCapabilities
    started_at: str
    stopped_at: str | None = None
    task: asyncio.Task[Any] | None = field(default=None, repr=False)
    orchestrator: Orchestrator | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "survey_id": self.survey_id,
            "line_id": self.line_id,
            "status": self.status,
            "source_type": self.source_type,
            "capabilities": capabilities_to_dict(self.capabilities),
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
        }


class SurveyManager:
    def __init__(
        self,
        source: ScanSource,
        source_type: str,
        detector: DetectorLike,
        store: Store,
        config: Config,
        broadcast: BroadcastFn,
        reasoning_engine: ReasoningEngine | None,
    ) -> None:
        self._source = source
        self._source_type = source_type
        self._detector = detector
        self._store = store
        self._config = config
        self._broadcast = broadcast
        self._reasoning_engine = reasoning_engine
        self._surveys: dict[str, SurveyRecord] = {}
        self._pending_broadcasts: set[asyncio.Task[Any]] = set()

    def list_surveys(self) -> list[SurveyRecord]:
        return list(self._surveys.values())

    def get_survey(self, survey_id: str) -> SurveyRecord | None:
        return self._surveys.get(survey_id)

    def start_survey(self, survey_id: str, line_id: str = "line_1") -> SurveyRecord:
        existing = self._surveys.get(survey_id)
        if existing is not None and existing.status == "running":
            raise ValueError(f"survey {survey_id!r} is already running")

        record = SurveyRecord(
            survey_id=survey_id,
            line_id=line_id,
            status="running",
            source_type=self._source_type,
            capabilities=self._source.capabilities(),
            started_at=_now_iso(),
        )
        self._surveys[survey_id] = record

        def emit(finding_id: int, finding: Finding, event_type: str) -> None:
            # Bridges the orchestrator's synchronous callback to the async
            # broadcast. Always called from the event-loop thread (directly
            # from _process_frame, or after a run_in_executor await resumes
            # on the loop) — asyncio.create_task is safe here.
            task = asyncio.create_task(
                self._broadcast_finding(survey_id, event_type, finding_id, finding)
            )
            self._pending_broadcasts.add(task)
            task.add_done_callback(self._pending_broadcasts.discard)

        orchestrator = Orchestrator(
            source=self._source,
            detector=self._detector,
            store=self._store,
            config=self._config,
            emit=emit,
            survey_id=survey_id,
            line_id=line_id,
            reasoning_engine=self._reasoning_engine,
        )

        record.orchestrator = orchestrator
        record.task = asyncio.create_task(self._run_survey(record, orchestrator))
        return record

    async def stop_survey(self, survey_id: str) -> SurveyRecord:
        record = self._surveys.get(survey_id)
        if record is None:
            raise KeyError(survey_id)
        if record.status != "running":
            raise ValueError(f"survey {survey_id!r} is not running (status={record.status})")
        # start_survey sets record.task synchronously (no await between
        # storing the record and creating its task), so a "running" record
        # always has one — an Optional check here would be permanently dead
        # code, not real defensiveness.
        assert record.task is not None, "a running survey always has a task"
        record.task.cancel()
        try:
            await record.task
        except asyncio.CancelledError:
            pass
        # orchestrator.run() being cancelled skips straight past
        # wait_for_pending_reasoning() in _run_survey — any reasoning task
        # already dispatched for this run would otherwise keep running
        # detached and could emit a stray finding.reasoned under this same
        # survey_id after a caller restarts it. Drain explicitly here so
        # stop_survey doesn't return until that's no longer possible.
        assert record.orchestrator is not None, "a running survey always has an orchestrator"
        await record.orchestrator.cancel_pending_reasoning()
        return record

    async def _run_survey(self, record: SurveyRecord, orchestrator: Orchestrator) -> None:
        try:
            await orchestrator.run()
            await orchestrator.wait_for_pending_reasoning()
            record.status = "completed"
        except asyncio.CancelledError:
            record.status = "stopped"
            raise
        except Exception:
            logger.exception("survey_manager.survey_failed survey_id=%s", record.survey_id)
            record.status = "failed"
        finally:
            record.stopped_at = _now_iso()

    async def _broadcast_finding(
        self, survey_id: str, event_type: str, finding_id: int, finding: Finding
    ) -> None:
        # finding_id lets a client correlate "finding.created" and the later "finding.reasoned"
        # for the same row — without it, the dashboard's live feed can only append both as
        # separate, unmergeable log entries (Session 8's documented scope cut).
        await self._broadcast(
            {
                "type": event_type,
                "survey_id": survey_id,
                "finding_id": finding_id,
                "finding": finding_to_dict(finding),
            }
        )
