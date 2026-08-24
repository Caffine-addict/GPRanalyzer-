"""Tests for api/server.py using FastAPI's TestClient (REST + WebSocket).

The app factory (create_app) is pointed at an isolated test config (temp
DuckDB path, fixture replay directory) rather than the real config.yaml, so
these tests never touch the real output/gpr.duckdb. The real Detector and
(absent GROQ_API_KEY) no-reasoning-engine come from the lifespan as normal;
tests that need actual findings/reasoning to flow through swap in a fake
detector/reasoning engine on app.state.survey_manager after startup —
matching how no trained weights or API key exist yet in this project.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient
from PIL import Image

import parsers.image  # noqa: F401 - registers the image parser as a side effect
from api.server import create_app
from core.config import load_config
from core.contracts import Detection
from reason.engine import ReasoningEngine

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_fixture_frames(directory: Path, n: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.fromarray(np.full((64, 64), 100, dtype=np.uint8)).save(directory / f"{i:03d}.jpg")


def _write_test_config(tmp_path: Path, frames_dir: Path, *, step_mode: bool = True) -> Path:
    raw = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw["store"]["path"] = str(tmp_path / "test.duckdb")
    raw["source"]["replay"]["directory"] = str(frames_dir)
    raw["source"]["replay"]["step_mode"] = step_mode
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return config_path


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


def test_cors_allows_cross_origin_requests_from_the_dashboard(tmp_path: Path) -> None:
    # dashboard/ is served from its own origin (a separate Vite dev
    # server/static host) — without CORS headers, a real browser would
    # block every REST call the dashboard makes, even though curl/TestClient
    # (which don't enforce CORS) would never catch that.
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    app = create_app(_write_test_config(tmp_path, frames_dir))

    with TestClient(app) as client:
        response = client.get("/surveys", headers={"Origin": "http://localhost:5173"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_preflight_authorizes_the_post_json_request_start_survey_makes(tmp_path: Path) -> None:
    # dashboard/src/api/client.ts's startSurvey() sends a JSON POST with a
    # Content-Type header, which triggers a real preflighted OPTIONS request
    # in a browser — a plain GET (the other CORS test) never exercises this
    # path, so a future change narrowing allow_methods/allow_headers could
    # silently break every POST the dashboard makes without any test here
    # catching it.
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    app = create_app(_write_test_config(tmp_path, frames_dir))

    with TestClient(app) as client:
        response = client.options(
            "/surveys/survey-1/start",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert "POST" in response.headers["access-control-allow-methods"]


def test_cors_does_not_authorize_an_untrusted_origin(tmp_path: Path) -> None:
    # No auth exists anywhere in this API — this allowlist is the only
    # thing standing between an unrelated webpage the operator's browser
    # visits and this API. A wildcard (or an overly broad allowlist) would
    # let that page read survey data or start/stop surveys through the
    # operator's own browser as a pivot; confirm an origin that was never
    # configured doesn't get authorized.
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    app = create_app(_write_test_config(tmp_path, frames_dir))

    with TestClient(app) as client:
        response = client.get("/surveys", headers={"Origin": "http://evil.example.com"})
        assert response.status_code == 200  # the request itself isn't blocked server-side...
        # ...but no CORS header authorizes it, so a real browser would
        # refuse to hand the response body to the page that made the call.
        assert "access-control-allow-origin" not in response.headers


def test_list_surveys_empty_initially(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    app = create_app(_write_test_config(tmp_path, frames_dir))

    with TestClient(app) as client:
        response = client.get("/surveys")
        assert response.status_code == 200
        assert response.json() == []


def test_start_survey_returns_running_record_with_capabilities(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    app = create_app(_write_test_config(tmp_path, frames_dir))

    with TestClient(app) as client:
        response = client.post("/surveys/survey-1/start")
        assert response.status_code == 200
        body = response.json()
        assert body["survey_id"] == "survey-1"
        assert body["status"] == "running"
        assert body["source_type"] == "replay"
        assert body["capabilities"]["has_calibrated_depth"] is False


def test_start_survey_twice_returns_409(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 3)
    app = create_app(_write_test_config(tmp_path, frames_dir))

    with TestClient(app) as client:
        first = client.post("/surveys/survey-1/start")
        assert first.status_code == 200
        second = client.post("/surveys/survey-1/start")
        assert second.status_code == 409


def test_stop_unknown_survey_returns_404(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    app = create_app(_write_test_config(tmp_path, frames_dir))

    with TestClient(app) as client:
        response = client.post("/surveys/nonexistent/stop")
        assert response.status_code == 404


def test_stop_running_survey_returns_200_and_marks_stopped(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 5)
    # Real pacing (step_mode=False) so the survey is still running when stop
    # is called, rather than having already completed near-instantly.
    app = create_app(_write_test_config(tmp_path, frames_dir, step_mode=False))

    with TestClient(app) as client:
        start = client.post("/surveys/survey-1/start")
        assert start.status_code == 200

        response = client.post("/surveys/survey-1/stop")
        assert response.status_code == 200
        assert response.json()["status"] == "stopped"


def test_stop_already_completed_survey_returns_400(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    app = create_app(_write_test_config(tmp_path, frames_dir))

    with TestClient(app) as client:
        client.post("/surveys/survey-1/start")
        import time

        time.sleep(0.3)  # let the (step_mode, near-instant) survey complete
        response = client.post("/surveys/survey-1/stop")
        assert response.status_code == 400


def test_lifespan_shutdown_stops_running_survey_before_closing_store(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 5)
    # Real pacing so the survey is still "running" when the app shuts down,
    # rather than having already finished on its own.
    app = create_app(_write_test_config(tmp_path, frames_dir, step_mode=False))

    with TestClient(app) as client:
        app.state.survey_manager._detector = _FakeDetector()
        start = client.post("/surveys/survey-1/start")
        assert start.status_code == 200
        record = app.state.survey_manager.get_survey("survey-1")
        assert record.status == "running"
        # Exiting this `with` block runs the app's shutdown (lifespan code
        # after `yield`), which must stop the survey before closing the
        # store — otherwise the survey's background task would keep writing
        # to a connection that's already been closed underneath it.

    assert record.status == "stopped"


def test_lifespan_shutdown_survives_stop_survey_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 5)
    app = create_app(_write_test_config(tmp_path, frames_dir, step_mode=False))

    with caplog.at_level(logging.ERROR, logger="api.server"), TestClient(app) as client:
        app.state.survey_manager._detector = _FakeDetector()
        start = client.post("/surveys/survey-1/start")
        assert start.status_code == 200

        async def _raise(survey_id: str) -> None:
            raise RuntimeError("simulated stop failure")

        monkeypatch.setattr(app.state.survey_manager, "stop_survey", _raise)
        # Exiting this `with` block runs shutdown; a broken stop_survey must
        # not crash the whole shutdown sequence (or leave the store
        # unclosed) — one broken survey can't be allowed to take down every
        # other survey's cleanup.

    messages = [r.getMessage() for r in caplog.records]
    assert any("server.shutdown_stop_survey_failed" in m for m in messages)


def test_survey_findings_and_summary_reflect_real_detections(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 2)
    config_path = _write_test_config(tmp_path, frames_dir)
    app = create_app(config_path)

    with TestClient(app) as client:
        app.state.survey_manager._detector = _FakeDetector()  # no real weights exist yet
        response = client.post("/surveys/survey-1/start")
        assert response.status_code == 200

        import time

        time.sleep(0.3)  # let the near-instant survey finish

        findings_response = client.get("/surveys/survey-1/findings")
        assert findings_response.status_code == 200
        findings = findings_response.json()
        assert len(findings) == 2  # one detection per frame, 2 frames
        assert all(f["evidence"]["detection_class"] == "cavities" for f in findings)

        summary_response = client.get("/surveys/survey-1/summary")
        assert summary_response.status_code == 200
        summary = summary_response.json()
        assert summary["total_findings"] == 2

        line_response = client.get("/lines/line_1/findings")
        assert line_response.status_code == 200
        assert len(line_response.json()) == 2


def test_websocket_disconnect_removes_connection_from_manager(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    app = create_app(_write_test_config(tmp_path, frames_dir))

    with TestClient(app) as client:
        with client.websocket_connect("/ws/live"):
            assert app.state.connections.connection_count == 1

        # Exiting the `with` block above closes the socket from the client
        # side; the server's own handler needs a moment on its event loop
        # to observe the WebSocketDisconnect and call connections.disconnect.
        time.sleep(0.2)
        assert app.state.connections.connection_count == 0


def test_websocket_receives_finding_created_then_reasoned_in_order(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    _write_fixture_frames(frames_dir, 1)
    config_path = _write_test_config(tmp_path, frames_dir)
    app = create_app(config_path)

    with TestClient(app) as client:
        # No real YOLO weights and no real GROQ_API_KEY exist yet — swap in
        # fakes so real findings and reasoning actually flow through the
        # WebSocket, same principle as scripts/orchestrator_heartbeat.py.
        app.state.survey_manager._detector = _FakeDetector()
        cfg = load_config(config_path)
        app.state.survey_manager._reasoning_engine = ReasoningEngine(_FakeLLMClient(), cfg.reasoning)

        with client.websocket_connect("/ws/live") as websocket:
            start_response = client.post("/surveys/survey-1/start")
            assert start_response.status_code == 200

            created = websocket.receive_json()
            reasoned = websocket.receive_json()

        assert created["type"] == "finding.created"
        assert created["survey_id"] == "survey-1"
        assert created["finding"]["evidence"]["detection_class"] == "cavities"
        assert created["finding"]["what"] is None  # no reasoning yet at this point

        assert reasoned["type"] == "finding.reasoned"
        assert reasoned["survey_id"] == "survey-1"
        assert reasoned["finding"]["what"] == _VALID_RESPONSE["what"]
        assert reasoned["finding"]["recommended_action"] == _VALID_RESPONSE["recommended_action"]
