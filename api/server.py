"""FastAPI app: REST endpoints for surveys/findings, WebSocket feed of orchestrator emissions.

Source type is chosen from config.yaml at startup — switching from replay to
a real source later is a config change here, not a code change (see
sources/factory.py). create_app() is a factory (not a bare module-level
app) so tests can point it at an isolated test config (temp DB path, fixture
replay directory) instead of the real one.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

import parsers.image
import parsers.spr  # noqa: F401 - registers the "rad"/"ra1"/"ra2" parsers as a side effect
from api.connection_manager import ConnectionManager
from api.schemas import finding_to_dict
from api.survey_manager import SurveyManager
from core.config import load_config
from detect.model import Detector
from reason.engine import GroqClient, ReasoningEngine
from reports.generate import generate_report
from sources.factory import create_source
from store.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)

# Same safe-identifier charset core/annotation_io.py's _SAFE_JOB_NAME already established for
# names used as path components — reused here for the same reason (survey_id lands in a
# temp-file prefix and a response header in get_survey_report below).
_SAFE_SURVEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


class StartSurveyRequest(BaseModel):
    line_id: str = "line_1"


def create_app(config_path: Path = _DEFAULT_CONFIG_PATH) -> FastAPI:
    # Loaded once here (in addition to inside lifespan below) purely to
    # read cors_origins before the app object exists — middleware has to be
    # registered at app-creation time, while the rest of app state is built
    # lazily in lifespan so tests can swap in fakes after startup.
    cors_origins = list(load_config(config_path).api.cors_origins)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        load_dotenv()
        config = load_config(config_path)
        store = DuckDBStore(config.store.path)
        source = create_source(config.source)
        detector = Detector(config.detection)

        api_key = os.environ.get("GROQ_API_KEY")
        reasoning_engine = ReasoningEngine(GroqClient(api_key), config.reasoning) if api_key else None

        connections = ConnectionManager()
        survey_manager = SurveyManager(
            source=source,
            source_type=config.source.type,
            detector=detector,
            store=store,
            config=config,
            broadcast=connections.broadcast,
            reasoning_engine=reasoning_engine,
        )

        app.state.store = store
        app.state.connections = connections
        app.state.survey_manager = survey_manager

        yield

        # Any survey still "running" is on a background asyncio task that
        # holds and uses `store` (writes on every frame). Closing the store
        # out from under it would surface as spurious DuckDB errors on
        # every remaining frame instead of a clean stop — drain them first.
        for record in survey_manager.list_surveys():
            if record.status == "running":
                try:
                    await survey_manager.stop_survey(record.survey_id)
                except Exception:
                    logger.exception("server.shutdown_stop_survey_failed survey_id=%s", record.survey_id)

        store.close()

    app = FastAPI(title="gpr-analyzer", lifespan=lifespan)

    # dashboard/ runs on its own origin (a separate Vite dev server / static
    # host) and needs cross-origin REST access. No auth exists anywhere in
    # this API (an accepted internal-tool trust model, not an oversight —
    # see CLAUDE.md), which is exactly why this must be an allowlist of
    # actual known dashboard origins (config.yaml's api.cors_origins) and
    # NOT "*": a wildcard would let any unrelated webpage the operator's
    # browser visits read survey/finding data or start/stop surveys through
    # the operator's own browser as a network pivot, even with no direct
    # network access of its own — the browser's same-origin policy is the
    # only access control this API has today. allow_credentials stays
    # False (the default): there's no cookie/session to protect.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # All four GET handlers are deliberately `async def`, not plain `def`:
    # Starlette runs plain `def` route handlers in a worker thread pool,
    # but DuckDBStore wraps one shared connection with no locking, and
    # SurveyManager/Orchestrator do all their store writes synchronously on
    # the main event-loop thread. A sync handler reading from a threadpool
    # thread while the event-loop thread writes concurrently can silently
    # interleave cursor state and return another query's rows. Staying
    # `async def` keeps every store call on the one event-loop thread,
    # same principle already applied to start_survey/stop_survey below.
    @app.get("/surveys")
    async def list_surveys() -> list[dict[str, Any]]:
        manager: SurveyManager = app.state.survey_manager
        return [s.to_dict() for s in manager.list_surveys()]

    @app.get("/surveys/{survey_id}/findings")
    async def get_survey_findings(survey_id: str) -> list[dict[str, Any]]:
        store: DuckDBStore = app.state.store
        return [finding_to_dict(f) for f in store.get_findings_by_survey(survey_id)]

    @app.get("/surveys/{survey_id}/summary")
    async def get_survey_summary(survey_id: str) -> dict[str, Any]:
        store: DuckDBStore = app.state.store
        return store.get_survey_summary(survey_id)

    @app.get("/lines/{line_id}/findings")
    async def get_line_findings(line_id: str) -> list[dict[str, Any]]:
        store: DuckDBStore = app.state.store
        return [finding_to_dict(f) for f in store.get_findings_for_line_id(line_id)]

    @app.get("/surveys/{survey_id}/report")
    async def get_survey_report(survey_id: str) -> FileResponse:
        # survey_id is client-controlled and goes into a temp-file prefix and a response
        # header below — same safe-identifier charset core/annotation_io.py already
        # established for job names used as path components, applied here too rather than
        # relying on tempfile.mkstemp's incidental protection against path traversal.
        if not _SAFE_SURVEY_ID.fullmatch(survey_id):
            raise HTTPException(status_code=400, detail=f"invalid survey_id: {survey_id!r}")

        store: DuckDBStore = app.state.store
        manager: SurveyManager = app.state.survey_manager
        # Checked two ways: survey_manager's in-memory record covers a survey still running
        # in *this* process (which may have zero frames persisted yet), and store.survey_exists
        # covers any survey that ever ran, including before a server restart — summary/findings
        # can't distinguish "never existed" from "existed, zero findings" on their own.
        if manager.get_survey(survey_id) is None and not store.survey_exists(survey_id):
            raise HTTPException(status_code=404, detail=f"survey not found: {survey_id}")

        # generate_report reads through the Store interface synchronously — same event-loop-
        # thread constraint as every other store-touching handler here (see the comment above
        # list_surveys). reportlab's rendering is CPU-bound and not async-native either way, so
        # there is no safe executor offload that wouldn't also move the store calls off-thread.
        fd, tmp_name = tempfile.mkstemp(suffix=".pdf", prefix=f"report_{survey_id}_")
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            generate_report(survey_id, store, tmp_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return FileResponse(
            tmp_path,
            media_type="application/pdf",
            filename=f"{survey_id}_report.pdf",
            background=BackgroundTask(tmp_path.unlink, missing_ok=True),
        )

    @app.post("/surveys/{survey_id}/start")
    async def start_survey(survey_id: str, body: StartSurveyRequest | None = None) -> dict[str, Any]:
        # Deliberately `async def`, not a plain `def`: FastAPI runs sync
        # route handlers in a worker thread pool, and
        # SurveyManager.start_survey() calls asyncio.create_task()
        # internally, which requires a running event loop in the *current*
        # thread. Staying async keeps this on the main event loop thread
        # where that call is valid.
        manager: SurveyManager = app.state.survey_manager
        line_id = body.line_id if body is not None else "line_1"
        try:
            record = manager.start_survey(survey_id, line_id=line_id)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return record.to_dict()

    @app.post("/surveys/{survey_id}/stop")
    async def stop_survey(survey_id: str) -> dict[str, Any]:
        manager: SurveyManager = app.state.survey_manager
        try:
            record = await manager.stop_survey(survey_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=f"survey not found: {survey_id}") from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return record.to_dict()

    @app.websocket("/ws/live")
    async def websocket_live(websocket: WebSocket) -> None:
        connections: ConnectionManager = app.state.connections
        await connections.connect(websocket)
        try:
            while True:
                # Clients don't send anything meaningful today — this just
                # keeps the connection open and detects disconnects.
                # Role-based filtering (operator/manager/pm) happens
                # client-side on the full finding.created/finding.reasoned
                # payload.
                await websocket.receive_text()
        except WebSocketDisconnect:
            connections.disconnect(websocket)

    return app


app = create_app()
