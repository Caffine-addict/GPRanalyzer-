"""Tests for studio/georef.py and studio/geo_routes.py — radar lines on the map.

Writes only into a temp annotations root (never the real `annotations/`).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import geo
from studio import georef, session
from studio import picks as pick_store
from studio import reviews as review_store
from studio.server import app

ORIGIN = (12.97, 77.60)


def point(chainage: float, east: float, north: float = 0.0, accuracy: float = 0.05) -> dict[str, object]:
    lat, lon = geo.from_local(east, north, *ORIGIN)
    return {"chainage_m": chainage, "lat": lat, "lon": lon, "accuracy_m": accuracy, "source": "total station"}


# --- validation ---------------------------------------------------------------------------------

@pytest.mark.parametrize(("payload", "message"), [
    ([point(0, 0)], "at least two"),
    ("nope", "at least two"),
    ([point(0, 0), {"chainage_m": 5}], "needs chainage_m, lat, lon and accuracy_m"),
    ([point(0, 0), {**point(5, 5), "lat": 95}], "latitude must be within"),
    ([point(0, 0), point(50, 50)], "is off this"),
    ([point(0, 0), point(5, 5, accuracy=0)], "accuracy must be above 0"),
    ([point(0, 0), {**point(5, 5), "source": " "}], "say where the point came from"),
    ([point(5, 0), point(5, 5)], "share a chainage"),
    ([point(0, 0), {**point(5, 5), "lat": float("nan")}], "finite"),
])
def test_bad_reference_points_are_refused_with_a_reason(payload: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        georef.validate(payload, line_length_m=10.0)


def test_valid_points_come_back_sorted_by_chainage() -> None:
    points = georef.validate([point(9.6, 9.6), point(0, 0)], line_length_m=9.65)
    assert [p.chainage_m for p in points] == [0, 9.6]


# --- placement checks ---------------------------------------------------------------------------

def test_a_straight_line_surveyed_at_its_wheel_length_checks_out() -> None:
    placement = georef.place(georef.validate([point(0, 0), point(5, 5), point(10, 10)], 10.0))
    assert placement.scale_ratio == pytest.approx(1.0, abs=1e-3)
    assert placement.straightness_m == pytest.approx(0.0, abs=1e-3)
    assert placement.warnings() == [] and placement.position_error_m == 0.05


def test_two_points_leave_straightness_unchecked_and_say_so() -> None:
    placement = georef.place(georef.validate([point(0, 0), point(10, 10)], 10.0))
    assert placement.straightness_m is None
    assert any("straightness unchecked" in w for w in placement.warnings())


def test_a_bowed_line_shows_up_as_straightness_error_and_widens_the_error_bar() -> None:
    placement = georef.place(georef.validate([point(0, 0), point(5, 5, north=0.4), point(10, 10)], 10.0))
    assert placement.straightness_m == pytest.approx(0.4, abs=0.01)
    assert placement.position_error_m == pytest.approx(0.45, abs=0.01)


def test_wheel_and_survey_disagreeing_is_flagged() -> None:
    placement = georef.place(georef.validate([point(0, 0), point(10, 10.6)], 10.0))  # survey says 10.6 m
    assert placement.scale_ratio == pytest.approx(1.06, abs=1e-3)
    assert any("differ by 6.0%" in w for w in placement.warnings())


def test_targets_are_placed_at_their_chainage_and_outsiders_counted() -> None:
    placement = georef.place(georef.validate([point(2, 2), point(8, 8)], 10.0))
    collection = georef.line_features("Job_1", 10.0, placement, [
        {"kind": "target", "id": "p1", "chainage_m": 5.0},
        {"kind": "target", "id": "p2", "chainage_m": 9.5},  # beyond the last reference point
    ])
    kinds = [f["properties"]["kind"] for f in collection["features"]]
    assert kinds == ["radar_line", "target"] and collection["metadata"]["targets_outside_reference_points"] == 1
    lon, lat = collection["features"][1]["geometry"]["coordinates"]
    assert geo.to_local(lat, lon, *ORIGIN) == pytest.approx((5.0, 0.0), abs=0.01)
    track = collection["features"][0]["geometry"]["coordinates"]
    assert geo.to_local(track[0][1], track[0][0], *ORIGIN) == pytest.approx((2.0, 0.0), abs=0.01)
    assert collection["features"][1]["properties"]["position_method"] == "chainage_between_reference_points"


# --- endpoints ----------------------------------------------------------------------------------

_DATASET = Path("Dataset/DSU_GPR_Files")
needs_data = pytest.mark.skipif(not _DATASET.exists(), reason="SPR dataset not present")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    root = tmp_path / "annotations"
    for module in (georef, pick_store, review_store):
        monkeypatch.setattr(module, "_ANNOTATIONS_ROOT", root)
    app.state.dataset_dir = _DATASET
    return TestClient(app)


@needs_data
def test_without_surveyed_points_a_frozen_gps_line_is_a_location_never_a_line(client: TestClient) -> None:
    job = "Job_0696"  # its GPS froze (docs/pilot/GPS_DIAGNOSTIC.md)
    body = client.get(f"/api/jobs/{job}/geo.geojson").json()
    assert [f["properties"]["kind"] for f in body["features"]] == ["line_location"]
    assert body["metadata"]["position_method"] == "onboard_gps_location_only"
    georef_body = client.get(f"/api/jobs/{job}/georef").json()
    assert georef_body["placement"] is None and georef_body["gps"]["kind"] == "location"


@needs_data
def test_without_surveyed_points_a_moving_gps_line_is_placed_from_its_track(client: TestClient) -> None:
    body = client.get("/api/jobs/Job_0730/geo.geojson").json()
    line = body["features"][0]["properties"]
    assert line["kind"] == "radar_line" and line["position_method"] == "onboard_gps_track"
    assert any("GPS and wheel disagree" in w for w in line["warnings"])


@needs_data
def test_saving_reference_points_puts_the_line_and_its_candidates_on_the_map(client: TestClient) -> None:
    job = "Job_0696"  # a delivered line with detector candidates (an imported line may have none)
    length = client.get(f"/api/jobs/{job}/georef").json()["line_length_m"]
    saved = client.put(f"/api/jobs/{job}/georef", json={"reference_points": [point(0, 0), point(length, length)]})
    assert saved.status_code == 200 and saved.json()["placement"]["scale_ratio"] == pytest.approx(1.0, abs=1e-3)
    body = client.get(f"/api/jobs/{job}/geo.geojson").json()
    kinds = {f["properties"]["kind"] for f in body["features"]}
    assert "radar_line" in kinds and "target" in kinds
    lines = {line["job"]: line for line in client.get("/api/geo/lines").json()}
    assert lines[job] == {"job": job, "georeferenced": True, "source": "surveyed"}
    # Surveyed points override the GPS: the line is now placed from them.
    assert body["metadata"]["position_method"] == "chainage_between_reference_points"
    download = client.get(f"/api/jobs/{job}/geo.geojson?download=true")
    assert download.headers["content-type"] == "application/geo+json"
    assert "attachment" in download.headers["content-disposition"]


@needs_data
def test_bad_points_are_a_422_and_nothing_is_stored(client: TestClient) -> None:
    job = session.list_jobs(_DATASET)[0]
    response = client.put(f"/api/jobs/{job}/georef", json={"reference_points": [point(0, 0)]})
    assert response.status_code == 422 and "at least two" in response.json()["detail"]
    assert client.get(f"/api/jobs/{job}/georef").json()["reference_points"] == []


@needs_data
def test_vendor_geojson_is_lon_lat_and_links_each_callout_to_its_drawing(client: TestClient) -> None:
    body = client.get("/api/geo/vendor.geojson").json()
    if not body["features"]:
        pytest.skip("processed vendor drawings not present")
    lon, lat = body["features"][0]["geometry"]["coordinates"]
    assert 60 < lon < 100 and 5 < lat < 30  # India: longitude first, as RFC 7946 requires
    assert body["features"][0]["properties"]["marked_pdf"].endswith(" - marked.pdf")


def test_straightness_is_the_sideways_bow_alone_not_the_scale_error() -> None:
    # A 0.30 m sideways bow at mid-line, and the far point surveyed 5% further than the wheel.
    pts = [point(0, 0), point(5, 5, north=0.30), point(10, 10.5)]
    placement = georef.place(georef.validate(pts, 10.0))
    assert placement.straightness_m == pytest.approx(0.30, abs=0.01)
    assert placement.scale_ratio == pytest.approx(1.05, abs=1e-3)


@needs_data
def test_a_frozen_gps_line_reports_estimated_confidence_not_calibrated(client: TestClient) -> None:
    job = "Job_0696"
    body = client.get(f"/api/jobs/{job}/geo.geojson").json()
    assert body["features"][0]["properties"]["position_confidence"] == "estimated"


@needs_data
def test_lines_endpoint_reports_each_jobs_own_source_not_a_fixed_one(client: TestClient) -> None:
    sources = {row["job"]: row["source"] for row in client.get("/api/geo/lines").json()}
    assert sources["Job_0696"] == "gps_location"
    assert sources["Job_0730"] == "gps_track"


@needs_data
def test_lines_scope_export_excludes_vendor_callouts(client: TestClient) -> None:
    all_body = client.get("/api/geo/all.geojson").json()
    if not any(f["properties"]["kind"] == "vendor_callout" for f in all_body["features"]):
        pytest.skip("no processed vendor drawings in this dataset")
    kml = client.get("/api/geo/export.kml?scope=lines").content.decode()
    assert "#vendor_callout" not in kml


@needs_data
def test_export_dxf_route_returns_422_not_500_when_theres_nothing_to_export(tmp_path: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    empty_dataset = tmp_path / "empty_dataset"
    empty_dataset.mkdir()
    for module in (georef, pick_store, review_store):
        monkeypatch.setattr(module, "_ANNOTATIONS_ROOT", tmp_path / "annotations")
    app.state.dataset_dir = empty_dataset
    client = TestClient(app)
    response = client.get("/api/geo/export.dxf?scope=vendor")
    assert response.status_code == 422
    assert "nothing to export" in response.json()["detail"]


@needs_data
def test_channel_cache_is_invalidated_when_the_channel_file_changes(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    # Works on a COPY of one line: an earlier version touched the real dataset's file.
    import shutil

    from studio import geo_routes
    from studio import session as session_mod

    source = session.resolve_job(session.list_jobs(_DATASET)[0], _DATASET)
    job_dir = tmp_path / source.name
    shutil.copytree(source, job_dir)
    calls = []
    real_describe = session_mod.describe_channel

    def counting_describe(frame: object, ext: str) -> object:
        calls.append(ext)
        return real_describe(frame, ext)  # type: ignore[arg-type]

    monkeypatch.setattr(session_mod, "describe_channel", counting_describe)
    geo_routes._CHANNEL_CACHE.clear()
    try:
        geo_routes._channels(job_dir)
        first_calls = len(calls)
        assert first_calls > 0
        geo_routes._channels(job_dir)  # same mtimes: cache hit, no new calls
        assert len(calls) == first_calls
        path = session.channel_path(job_dir, "RAD")
        stat = path.stat()
        import os

        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        geo_routes._channels(job_dir)
        assert len(calls) > first_calls  # cache invalidated by the new mtime
    finally:
        geo_routes._CHANNEL_CACHE.clear()


@needs_data
def test_one_corrupt_georef_file_costs_only_its_own_line_in_the_full_export(client: TestClient) -> None:
    jobs = session.list_jobs(_DATASET)
    length = client.get(f"/api/jobs/{jobs[0]}/georef").json()["line_length_m"]
    client.put(f"/api/jobs/{jobs[0]}/georef", json={"reference_points": [point(0, 0), point(length, length)]})
    broken = georef._ANNOTATIONS_ROOT / jobs[1]
    broken.mkdir(parents=True, exist_ok=True)
    (broken / "georef.json").write_text('{"reference_points": [{"chainage_m": 1}]}')
    body = client.get("/api/geo/all.geojson")
    assert body.status_code == 200
    lines = body.json()["metadata"]["lines"]
    assert jobs[0] in lines and "error" in lines[jobs[1]]
    assert any(f["properties"]["kind"] == "radar_line" for f in body.json()["features"])
