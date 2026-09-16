"""Tests for studio/server.py using FastAPI's TestClient.

Runs against the real SPR dataset (the app's whole job is serving it), but
writes picks into a temp annotations root so a test run never touches the
interpreter's real target lists.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import picks as pick_store
from studio import session
from studio.server import app

_DATASET = Path("Dataset/DSU_GPR_Files")

pytestmark = pytest.mark.skipif(
    not _DATASET.exists() or not any(_DATASET.iterdir()),
    reason="SPR dataset not present",
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(pick_store, "_ANNOTATIONS_ROOT", tmp_path / "annotations")
    app.state.dataset_dir = _DATASET
    return TestClient(app)


@pytest.fixture
def job(client: TestClient) -> str:
    return session.list_jobs(_DATASET)[0]


def test_index_serves_the_workstation_page(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "GPR Studio" in response.text


def test_jobs_endpoint_lists_the_dataset(client: TestClient) -> None:
    response = client.get("/api/jobs")
    assert response.status_code == 200
    assert response.json() == session.list_jobs(_DATASET)


def test_job_detail_carries_channels_gps_header_and_axis_provenance(client: TestClient, job: str) -> None:
    body = client.get(f"/api/jobs/{job}").json()
    assert body["job"] == job
    assert body["channels"]
    assert "distance" in body["axis_provenance"]
    # The depth axis must never reach a client without its caveat attached.
    assert "inferred" in body["axis_provenance"]["depth"]


def test_an_unknown_job_is_a_404(client: TestClient) -> None:
    assert client.get("/api/jobs/Job_9999").status_code == 404


def test_path_traversal_in_a_job_name_is_refused(client: TestClient) -> None:
    assert client.get("/api/jobs/..%2F..%2Fetc").status_code in (404, 422)


def test_image_endpoint_returns_a_png_at_the_requested_size(client: TestClient, job: str) -> None:
    response = client.get(
        f"/api/jobs/{job}/channels/RAD/image.png",
        params={"width": 400, "height": 200, "palette": "seismic"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_image_endpoint_accepts_the_whole_processing_chain(client: TestClient, job: str) -> None:
    response = client.get(
        f"/api/jobs/{job}/channels/RAD/image.png",
        params={
            "dewow": "true", "background_removal": "mean", "bandpass": "true",
            "migrate": "true", "gain": "agc", "stack_traces": 2,
        },
    )
    assert response.status_code == 200


def test_a_checkbox_switched_off_is_not_read_as_on(client: TestClient, job: str) -> None:
    # "false" is a truthy Python string; taking it as True would silently apply
    # a step the operator switched off, with nothing on screen to say so.
    off = client.get(f"/api/jobs/{job}/channels/RAD/image.png", params={"dewow": "false"})
    raw = client.get(f"/api/jobs/{job}/channels/RAD/image.png")
    assert off.content == raw.content


def test_a_misspelled_processing_parameter_is_rejected_by_name(client: TestClient, job: str) -> None:
    response = client.get(f"/api/jobs/{job}/channels/RAD/image.png", params={"dewowe": "true"})
    assert response.status_code == 422
    assert "dewowe" in response.json()["detail"]


def test_an_unknown_palette_is_rejected(client: TestClient, job: str) -> None:
    response = client.get(f"/api/jobs/{job}/channels/RAD/image.png", params={"palette": "chartreuse"})
    assert response.status_code == 422


def test_trace_endpoint_returns_one_a_scan(client: TestClient, job: str) -> None:
    body = client.get(f"/api/jobs/{job}/channels/RAD/trace", params={"index": 10}).json()
    assert body["trace"] == 10
    assert body["distance_m"] == pytest.approx(10 * 0.025)
    assert len(body["samples"]) > 0


def test_trace_endpoint_reflects_the_processing_chain(client: TestClient, job: str) -> None:
    # The wiggle has to show the same signal as the picture above it.
    raw = client.get(f"/api/jobs/{job}/channels/RAD/trace", params={"index": 10}).json()
    gained = client.get(
        f"/api/jobs/{job}/channels/RAD/trace", params={"index": 10, "gain": "agc"}
    ).json()
    assert raw["samples"] != gained["samples"]


def test_an_out_of_range_trace_is_rejected(client: TestClient, job: str) -> None:
    assert client.get(f"/api/jobs/{job}/channels/RAD/trace", params={"index": 999999}).status_code == 422


def test_fit_endpoint_reports_no_fit_without_inventing_one(client: TestClient, job: str) -> None:
    body = client.post(
        f"/api/jobs/{job}/channels/RAD/fit",
        json={"trace_start": 0, "sample_start": 0, "trace_span": 4, "sample_span": 4},
    ).json()
    if body["fit"] is None:
        assert body["reason"]
    else:
        assert 0.0 <= body["fit"]["r2"] <= 1.0


def test_fit_endpoint_returns_physical_units_and_a_drawable_curve(client: TestClient, job: str) -> None:
    body = client.post(
        f"/api/jobs/{job}/channels/RAD/fit",
        json={"trace_start": 100, "sample_start": 112, "trace_span": 19, "sample_span": 80},
    ).json()
    if body["fit"] is None:
        pytest.skip("no hyperbola in this region of the sample data")
    fit = body["fit"]
    assert fit["velocity_m_per_ns"] > 0
    assert fit["dielectric"] >= 1.0
    assert body["curve"]
    assert {"trace", "time_ns"} == set(body["curve"][0])


def test_a_malformed_fit_region_is_rejected(client: TestClient, job: str) -> None:
    response = client.post(f"/api/jobs/{job}/channels/RAD/fit", json={"trace_start": 0})
    assert response.status_code == 422


def test_curve_endpoint_returns_the_depth_and_permittivity_it_implies(client: TestClient, job: str) -> None:
    body = client.get(
        f"/api/jobs/{job}/channels/RAD/curve",
        params={"apex_trace": 100, "apex_time_ns": 8.0, "velocity_m_per_ns": 0.1},
    ).json()
    assert body["depth_m"] == pytest.approx(0.4)
    assert body["dielectric"] == pytest.approx(8.99, abs=0.05)


def test_picks_round_trip_through_the_api(client: TestClient, job: str) -> None:
    assert client.get(f"/api/jobs/{job}/picks").json() == []

    created = client.post(
        f"/api/jobs/{job}/picks",
        json={
            "channel": "RAD", "trace": 101, "sample": 125, "time_ns": 12.5,
            "depth_m": 0.63, "velocity_m_per_ns": 0.1011, "velocity_source": "fitted",
            "dielectric": 8.79, "label": "suspected duct", "fit_r2": 0.98,
        },
    )
    assert created.status_code == 200
    pick_id = created.json()["id"]
    assert [p["id"] for p in client.get(f"/api/jobs/{job}/picks").json()] == [pick_id]

    assert client.delete(f"/api/jobs/{job}/picks/{pick_id}").status_code == 200
    assert client.get(f"/api/jobs/{job}/picks").json() == []


def test_a_pick_with_an_uncheckable_fitted_velocity_is_rejected(client: TestClient, job: str) -> None:
    response = client.post(
        f"/api/jobs/{job}/picks",
        json={
            "channel": "RAD", "trace": 1, "sample": 1, "time_ns": 1.0, "depth_m": 0.1,
            "velocity_m_per_ns": 0.1, "velocity_source": "fitted", "dielectric": 9.0,
        },
    )
    assert response.status_code == 422


def test_deleting_an_unknown_pick_is_a_404(client: TestClient, job: str) -> None:
    assert client.delete(f"/api/jobs/{job}/picks/nope").status_code == 404


def test_csv_export_names_the_file_after_the_job(client: TestClient, job: str) -> None:
    response = client.get(f"/api/jobs/{job}/picks.csv")
    assert response.status_code == 200
    assert f"{job}_targets.csv" in response.headers["content-disposition"]
    assert "velocity_source" in response.text


def test_csv_export_carries_chainage_class_and_grade_but_no_coordinate(
    client: TestClient, job: str
) -> None:
    import csv as csv_module
    import io

    pick = _make_pick(client, job, velocity_source="fitted", fit_r2=0.98)
    response = client.get(f"/api/jobs/{job}/picks.csv")
    assert response.status_code == 200

    rows = list(csv_module.reader(io.StringIO(response.text)))
    header, data_row = rows[0], rows[1]
    assert header == [
        "id", "channel", "trace", "sample", "chainage_m", "depth_m", "depth_confidence",
        "taxonomy_class", "class_rule", "corroborating_channels",
        "risk_level", "risk_score", "quality_level", "quality_rationale",
        "velocity_m_per_ns", "velocity_source", "dielectric", "fit_r2",
        "label", "note", "created_at",
    ]
    # No lat/lon or any coordinate column — the export must not carry a position this
    # project's GPS data cannot back up (see interpret.export_target_list's docstring).
    assert not any("lat" in col or "lon" in col or "coord" in col for col in header)

    row = dict(zip(header, data_row, strict=True))
    assert row["id"] == pick["id"]
    # trace=150.0 * trace_spacing_m=0.025 (SPR_SHAFT_INTERVAL on all four delivered lines)
    assert float(row["chainage_m"]) == pytest.approx(150.0 * 0.025)
    assert row["depth_confidence"] == "calibrated"  # velocity_source="fitted"
    assert row["taxonomy_class"] in {
        "cavities", "elongated_linear_target", "intersecting_linear_and_point_reflector",
        "strong_high_contrast_reflector", "multiple_point_reflectors", "low_snr_point_reflector",
        "cluttered_multi_target", "disturbed_zone", "clear_point_reflector",
    }
    assert row["quality_level"]  # a real PAS 128 label, not empty
    assert int(row["corroborating_channels"]) >= 1


def test_candidates_endpoint_joins_boxes_to_their_diagnoses(client: TestClient, job: str) -> None:
    body = client.get(f"/api/jobs/{job}/candidates").json()
    assert isinstance(body, list)
    for candidate in body:
        assert {"id", "channel", "x", "y", "w", "h", "suggested_class"} <= set(candidate)


def test_reference_library_is_served_without_asserting_a_class(client: TestClient) -> None:
    crops = client.get("/api/reference").json()
    assert len(crops) == 16
    assert all(crop["label_class"] is None for crop in crops)

    image = client.get(f"/api/reference/crops/{crops[0]['id']}.png")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"


def test_an_unknown_reference_crop_is_a_404(client: TestClient) -> None:
    assert client.get("/api/reference/crops/sheet9-99.png").status_code == 404


def test_reference_sheets_are_served_and_unknown_ones_are_not(client: TestClient) -> None:
    sheet = client.get("/api/reference").json()[0]["sheet"]
    assert client.get(f"/api/reference/sheets/{sheet}").status_code == 200
    assert client.get("/api/reference/sheets/not-a-sheet.jpg").status_code == 404


def test_palettes_endpoint_declares_which_are_diverging(client: TestClient) -> None:
    palettes = client.get("/api/palettes").json()
    assert palettes
    assert any(p["is_diverging"] for p in palettes)
    assert any(not p["is_diverging"] for p in palettes)


# --- interpretation (studio/interpret.py + reason/engine.py) -----------------

_FAKE_ANSWER = {
    "what": "A point reflector consistent with a small service duct or cable.",
    "where": "3.75 m along the line, 0.45 m deep.",
    "why": "Depth is calibrated because the velocity was measured on this target's own hyperbola.",
    "how": "Moderate confidence; a crossing line would confirm it runs as a service.",
    "recommended_action": "Run a perpendicular line over 3.75 m before excavating.",
}


class _FakeEngine:
    """Stands in for ReasoningEngine — no network, no key, no Groq."""

    def __init__(self, result=None, latency_ms: float = 12.5) -> None:
        from reason.schema import ReasoningResult

        self._result = ReasoningResult(**_FAKE_ANSWER) if result is None else result
        self._latency = latency_ms
        self.seen: list = []

    def reason(self, evidence, risk):
        self.seen.append((evidence, risk))
        return self._result, self._latency


@pytest.fixture
def fake_engine():
    from studio.server import app as studio_app

    engine = _FakeEngine()
    studio_app.state.reasoning_engine = engine
    # The app object is module-level and shared across this whole test session. An interpretation
    # cache left behind by one test would silently answer another test's request and make results
    # depend on ordering, so it is cleared on both sides rather than just after.
    studio_app.state.interpretation_cache = {}
    yield engine
    del studio_app.state.reasoning_engine
    studio_app.state.interpretation_cache = {}


def _make_pick(client: TestClient, job: str, **overrides) -> dict:
    channel = client.get(f"/api/jobs/{job}").json()["channels"][0]["extension"]
    payload = {
        "channel": channel, "trace": 150.0, "sample": 90.0, "time_ns": 9.0, "depth_m": 0.45,
        "velocity_m_per_ns": 0.1011, "velocity_source": "fitted", "dielectric": 8.79,
        "label": "definitely a cavity", "note": "", "fit_r2": 0.98,
    }
    payload.update(overrides)
    return client.post(f"/api/jobs/{job}/picks", json=payload).json()


def test_interpreting_an_unknown_pick_is_a_404(client: TestClient, job: str, fake_engine) -> None:
    response = client.post(f"/api/jobs/{job}/picks/nope/interpret")
    assert response.status_code == 404


def test_without_a_configured_key_the_studio_says_so_instead_of_failing_quietly(
    client: TestClient, job: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Hermetic: a real .env on the developer's machine must not decide this test.
    import studio.server as studio_server

    monkeypatch.setattr(studio_server, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(studio_server.app.state, "reasoning_engine", None, raising=False)

    pick = _make_pick(client, job)
    response = client.post(f"/api/jobs/{job}/picks/{pick['id']}/interpret")
    assert response.status_code == 503
    assert "GROQ_API_KEY" in response.json()["detail"]


def test_interpretation_returns_what_where_why_how_and_an_action(
    client: TestClient, job: str, fake_engine
) -> None:
    pick = _make_pick(client, job)
    body = client.post(f"/api/jobs/{job}/picks/{pick['id']}/interpret").json()
    assert set(body["reasoning"]) == {"what", "where", "why", "how", "recommended_action"}
    assert body["reasoning"]["what"] == _FAKE_ANSWER["what"]
    assert body["reasoning_error"] is None


def test_interpretation_carries_the_measured_class_and_depth_provenance(
    client: TestClient, job: str, fake_engine
) -> None:
    pick = _make_pick(client, job, velocity_source="fitted", fit_r2=0.98)
    body = client.post(f"/api/jobs/{job}/picks/{pick['id']}/interpret").json()
    assert body["depth_confidence"] == "calibrated"
    assert body["class_rule"]
    assert body["evidence"]  # exactly what the model was shown, for the operator to check


def test_an_assumed_velocity_is_reported_as_estimated_not_calibrated(
    client: TestClient, job: str, fake_engine
) -> None:
    pick = _make_pick(client, job, velocity_source="assumed", fit_r2=None)
    body = client.post(f"/api/jobs/{job}/picks/{pick['id']}/interpret").json()
    assert body["depth_confidence"] == "estimated"


def test_the_interpreters_free_text_label_is_never_sent_as_the_class(
    client: TestClient, job: str, fake_engine
) -> None:
    # The label said "definitely a cavity". Only measurement decides a taxonomy class.
    pick = _make_pick(client, job, label="definitely a cavity")
    body = client.post(f"/api/jobs/{job}/picks/{pick['id']}/interpret").json()
    assert body["class"] != "cavities"
    evidence, _risk = fake_engine.seen[0]
    assert evidence.detection_class == body["class"]
    assert "definitely a cavity" not in evidence.detection_class


def test_a_model_failure_still_returns_the_measured_facts(client: TestClient, job: str) -> None:
    # Reasoning is best-effort everywhere else in this project; the measured class and
    # depth are real work and must not be thrown away because the model timed out.
    from studio.server import app as studio_app

    class _FailingEngine:
        def reason(self, evidence, risk):
            return None, 42.0

    studio_app.state.reasoning_engine = _FailingEngine()
    try:
        pick = _make_pick(client, job)
        body = client.post(f"/api/jobs/{job}/picks/{pick['id']}/interpret").json()
        assert body["reasoning"] is None
        assert body["reasoning_error"]
        assert body["class"]
        assert body["depth_confidence"] == "calibrated"
    finally:
        del studio_app.state.reasoning_engine


# --- interpretation cache + survey quality level (2026-09-14) ----------------


def test_an_identical_interpretation_is_served_without_asking_the_model_again(
    client: TestClient, job: str, fake_engine
) -> None:
    # Every call is billed, and a reviewer clicks the same target repeatedly while comparing.
    pick = _make_pick(client, job)
    first = client.post(f"/api/jobs/{job}/picks/{pick['id']}/interpret").json()
    second = client.post(f"/api/jobs/{job}/picks/{pick['id']}/interpret").json()

    assert len(fake_engine.seen) == 1, "the second request must not reach the model"
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["reasoning"] == first["reasoning"]
    assert second["class"] == first["class"]


def test_a_new_pick_on_the_same_line_is_not_served_from_another_picks_cache(
    client: TestClient, job: str, fake_engine
) -> None:
    first = _make_pick(client, job, trace=150.0)
    client.post(f"/api/jobs/{job}/picks/{first['id']}/interpret")
    second = _make_pick(client, job, trace=260.0)

    response = client.post(f"/api/jobs/{job}/picks/{second['id']}/interpret").json()
    assert response["cached"] is False
    assert len(fake_engine.seen) == 2


def test_a_model_failure_is_not_cached_so_the_next_click_retries(
    client: TestClient, job: str
) -> None:
    # Caching a failure would pin it in place for the rest of the session, and the next click is
    # exactly when a retry should happen.
    from studio.server import app as studio_app

    class _FailingEngine:
        """Reasoning that returns nothing usable — the runtime-failure path."""

        def __init__(self) -> None:
            self.seen: list = []

        def reason(self, evidence, risk):
            self.seen.append((evidence, risk))
            return None, 8.0

    engine = _FailingEngine()
    studio_app.state.reasoning_engine = engine
    studio_app.state.interpretation_cache = {}
    try:
        pick = _make_pick(client, job)
        first = client.post(f"/api/jobs/{job}/picks/{pick['id']}/interpret").json()
        second = client.post(f"/api/jobs/{job}/picks/{pick['id']}/interpret").json()
        assert first["reasoning"] is None
        assert first["reasoning_error"]
        assert second["cached"] is False
        assert len(engine.seen) == 2
    finally:
        del studio_app.state.reasoning_engine
        studio_app.state.interpretation_cache = {}


def test_interpretation_carries_a_survey_quality_level(
    client: TestClient, job: str, fake_engine
) -> None:
    # A fitted velocity on one channel is QL-B2: depth measured, not corroborated.
    pick = _make_pick(client, job, velocity_source="fitted", fit_r2=0.98)
    body = client.post(f"/api/jobs/{job}/picks/{pick['id']}/interpret").json()

    assert body["quality_level"] == "QL-B2"
    assert body["corroborating_channels"] == 1
    assert "not independently corroborated" in body["quality_rationale"]


def test_an_assumed_velocity_grades_lower_than_a_fitted_one(
    client: TestClient, job: str, fake_engine
) -> None:
    # The header's permittivity is an assumption, and an assumption caps the grade at QL-B4.
    pick = _make_pick(client, job, velocity_source="assumed", fit_r2=None)
    body = client.post(f"/api/jobs/{job}/picks/{pick['id']}/interpret").json()

    assert body["quality_level"] == "QL-B4"
    assert "assumed velocity" in body["quality_rationale"]
