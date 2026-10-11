"""Tests for studio/dig_map.py — only what people decided, findable on the ground, nothing guessed."""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import boxes as box_store
from studio import candidates, dig_map, field_reports, imports, session
from studio import picks as pick_store
from studio import reviews as review_store

sys.path.insert(0, str(Path(__file__).parent))
from test_studio_site_map import LAT0, _line_with_gps

DT = 0.2  # the synthetic lines' sample interval (ns); time zero 1 ns, the pipe's apex 8 ns below it
SPACING = 0.025
PIPE_TRACE = 4.0 / SPACING  # the pipe crosses 4 m along each line
PIPE_SAMPLE = (1.0 + 8.0) / DT  # raw sample index of its apex


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.chdir(tmp_path)  # annotations/ is relative to the working directory
    dataset = tmp_path / "Lines"
    dataset.mkdir()
    for k in range(2):
        imports.import_line(dataset, f"Site_L{k}", _line_with_gps(east_m=1.0 * k, pipe_north_m=4.0))
    return dataset


def _pipe_box(site: Path, job: str) -> box_store.Box:
    candidates.ensure_candidates(job, session.resolve_job(job, site))
    return min(box_store.load_boxes(job), key=lambda b: abs(b.x + b.w / 2 - PIPE_TRACE) + abs(b.y - PIPE_SAMPLE) / 10)


def _claim(box: box_store.Box, identity: str = "pipe") -> dict:
    return {"channel": box.channel, "target_id": box.id, "target_kind": "candidate", "x": box.x, "y": box.y,
            "w": box.w, "h": box.h, "identity": identity, "material": "steel", "confidence": "low", "why": "",
            "layer": "test", "model": "test"}


def _confirm(job: str, claim: dict, decision: str = "confirmed", reviewer: str = "Supervisor A") -> review_store.Review:
    (review,) = review_store.propose(job, [claim])
    return review_store.decide(job, review.id, decision, reviewer)


def test_a_confirmed_candidate_is_placed_where_and_as_deep_as_the_pipe(site: Path) -> None:
    _confirm("Site_L0", _claim(_pipe_box(site, "Site_L0")))
    items, attention = dig_map.dig_items(site, "Site")
    assert attention == []
    (item,) = items
    assert (item.status, item.identity, item.decided_by) == ("confirmed", "pipe", "Supervisor A")
    assert item.chainage_m == pytest.approx(4.0, abs=0.05)
    assert (item.lat - LAT0) * 110_574.0 == pytest.approx(4.0, abs=0.1)  # where the line was walked
    assert item.depth_m == pytest.approx(0.4, abs=0.04)  # 8 ns below the direct wave, dielectric 9
    assert item.depth_error_m > 0 and item.horizontal_error_m > 0


def test_proposed_and_rejected_claims_never_appear(site: Path) -> None:
    review_store.propose("Site_L0", [_claim(_pipe_box(site, "Site_L0"), "maybe")])
    _confirm("Site_L1", _claim(_pipe_box(site, "Site_L1")), decision="rejected")
    assert dig_map.dig_items(site, "Site") == ([], [])


def test_a_regenerated_target_needs_reconfirming_rather_than_being_guessed(site: Path) -> None:
    box = _pipe_box(site, "Site_L0")
    _confirm("Site_L0", _claim(box))
    box_store.delete_box("Site_L0", box.id)
    items, attention = dig_map.dig_items(site, "Site")
    assert items == [] and "regenerated or deleted" in attention[0].reason


def test_a_hand_drawn_box_is_not_dig_grade(site: Path) -> None:
    drawn = box_store.add_box("Site_L0", channel="RD3", x=150, y=30, w=20, h=20)
    _confirm("Site_L0", _claim(drawn))
    items, attention = dig_map.dig_items(site, "Site")
    assert items == [] and "apex" in attention[0].reason


def test_conflicting_decisions_go_to_attention(site: Path) -> None:
    box = _pipe_box(site, "Site_L0")
    _confirm("Site_L0", _claim(box, "water pipe"))
    _confirm("Site_L0", _claim(box, "gas pipe"))
    items, attention = dig_map.dig_items(site, "Site")
    assert items == [] and "conflict" in attention[0].reason


def test_a_confirmation_later_rejected_is_withdrawn(site: Path) -> None:
    box = _pipe_box(site, "Site_L0")
    _confirm("Site_L0", _claim(box))
    _confirm("Site_L0", _claim(box), decision="rejected", reviewer="Supervisor B")
    items, attention = dig_map.dig_items(site, "Site")
    assert items == [] and "conflict" in attention[0].reason


def test_pick_depth_comes_from_the_channel_time_zero_not_the_display_setting(site: Path) -> None:
    # Saved with the Studio's time zero left at 0: its stored depth counts the 1 ns before the
    # direct wave. The dig map measures from the direct wave instead.
    pick = pick_store.add_pick("Site_L1", channel="RD3", trace=PIPE_TRACE, sample=PIPE_SAMPLE, time_ns=9.0,
                               depth_m=0.45, velocity_m_per_ns=0.1, velocity_source="manual", dielectric=9.0,
                               label="duct")
    assert dig_map.dig_items(site, "Site")[0] == []  # interpreted picks only when asked for
    (item,) = dig_map.dig_items(site, "Site", include_interpreted=True)[0]
    assert item.status == "interpreted" and "not supervisor-confirmed" in item.decided_by
    assert item.depth_m == pytest.approx(0.4, abs=0.02)
    claim = {"channel": "RD3", "target_id": pick.id, "target_kind": "pick", "x": PIPE_TRACE - 5, "y": PIPE_SAMPLE,
             "w": 10, "h": 10, "identity": "duct", "material": "pvc", "confidence": "low", "why": "", "layer": "t",
             "model": "t"}
    _confirm("Site_L1", claim)
    (item,) = dig_map.dig_items(site, "Site", include_interpreted=True)[0]  # confirmed, not listed twice
    assert item.status == "confirmed" and item.depth_m == pytest.approx(0.4, abs=0.02)


def test_a_line_without_gps_is_listed_not_dropped(site: Path) -> None:
    _confirm("Site_L0", _claim(_pipe_box(site, "Site_L0")))
    (site / "Site_L0" / "Single-01.gps").unlink()
    items, attention = dig_map.dig_items(site, "Site")
    assert items == [] and "no usable GPS" in attention[0].reason


def test_outputs_carry_items_and_attention_and_defuse_spreadsheet_formulas(site: Path) -> None:
    _confirm("Site_L0", _claim(_pipe_box(site, "Site_L0"), '=HYPERLINK("http://x")'), reviewer="@admin")
    drawn = box_store.add_box("Site_L1", channel="RD3", x=150, y=30, w=20, h=20)
    _confirm("Site_L1", _claim(drawn))
    items, attention = dig_map.dig_items(site, "Site")
    geo = dig_map.to_geojson("Site", items, attention)
    assert geo["metadata"]["confirmed"] == 1 and len(geo["metadata"]["needs_attention"]) == 1
    props = geo["features"][0]["properties"]
    assert props["depth_range_m"][0] < props["depth_m"] < props["depth_range_m"][1]
    rows = list(csv.DictReader(io.StringIO(dig_map.to_csv(items, attention))))
    assert rows[0]["identity"].startswith("'=") and rows[0]["decided_by"] == "'@admin"
    assert rows[1]["status"] == "needs_attention" and rows[1]["attention_reason"]
    assert dig_map.to_pdf("Site", items, attention).startswith(b"%PDF")  # markup in a name must not break it


def test_dig_endpoints(site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.server import app

    _confirm("Site_L0", _claim(_pipe_box(site, "Site_L0")))
    monkeypatch.setattr(app.state, "dataset_dir", site, raising=False)  # restored after the test
    client = TestClient(app)
    assert client.get("/api/sites/Site/dig.geojson").json()["metadata"]["confirmed"] == 1
    assert client.get("/api/sites/Site/dig.csv").text.startswith("id,status")
    assert client.get("/api/sites/Site/dig.pdf").content.startswith(b"%PDF")
    assert client.get("/api/sites/Nope/dig.geojson").status_code == 404
    assert client.get("/api/sites/Site/dig.geojson", params={"antenna_offset_m": -1}).status_code == 422


# ---------------------------------------------------------------- field reports (2026-10-07)


def test_a_field_report_is_attached_to_its_dig_item(site: Path) -> None:
    _confirm("Site_L0", _claim(_pipe_box(site, "Site_L0")))
    (item,) = dig_map.dig_items(site, "Site")[0]
    assert item.field_outcome == ""
    field_reports.add_report("Site_L0", item_id=item.id, outcome="dug", reporter="Crew lead", depth_found_m=0.42,
                             lat=12.97, lon=77.64, accuracy_m=4.0)
    (item,) = dig_map.dig_items(site, "Site")[0]
    assert item.field_outcome == "dug" and "Crew lead" in item.field_report and "0.42 m" in item.field_report
    row = next(csv.DictReader(io.StringIO(dig_map.to_csv([item], []))))
    assert row["field_outcome"] == "dug"


@pytest.mark.parametrize(("change", "message"), [
    ({"outcome": "maybe"}, "outcome"), ({"reporter": " "}, "name"), ({"item_id": "Other_L9#R1"}, "belong"),
    ({"lat": 123.0}, "lat"), ({"accuracy_m": float("nan")}, "accuracy"), ({"depth_found_m": True}, "depth"),
])
def test_bad_field_reports_are_refused(site: Path, change: dict, message: str) -> None:
    report = {"item_id": "Site_L0#R1", "outcome": "found", "reporter": "Crew lead"} | change
    with pytest.raises(ValueError, match=message):
        field_reports.add_report("Site_L0", **report)


def test_field_view_and_report_endpoint(site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.server import app

    _confirm("Site_L0", _claim(_pipe_box(site, "Site_L0")))
    monkeypatch.setattr(app.state, "dataset_dir", site, raising=False)
    client = TestClient(app)
    page = client.get("/field")
    assert page.status_code == 200 and "field.js" in page.text and page.headers["cache-control"] == "no-cache"
    item_id = client.get("/api/sites/Site/dig.geojson").json()["features"][0]["properties"]["id"]
    ok = client.post("/api/sites/Site/field_reports", json={"item_id": item_id, "outcome": "found", "reporter": "Crew"})
    assert ok.status_code == 200 and ok.json()["outcome"] == "found"
    assert client.get("/api/sites/Site/dig.geojson").json()["features"][0]["properties"]["field_outcome"] == "found"
    assert client.post("/api/sites/Site/field_reports", json={"item_id": "Nope_L1#R1", "outcome": "found",
                                                              "reporter": "x"}).status_code == 404
    assert client.post("/api/sites/Site/field_reports", json={"item_id": item_id, "outcome": "?",
                                                              "reporter": "x"}).status_code == 422
    cross = client.post("/api/sites/Site/field_reports", json={"item_id": item_id, "outcome": "found", "reporter": "x"},
                        headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403  # another site open in the phone's browser cannot plant reports


def test_a_report_for_an_item_not_on_the_dig_map_is_refused(site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.server import app

    _confirm("Site_L0", _claim(_pipe_box(site, "Site_L0")))
    monkeypatch.setattr(app.state, "dataset_dir", site, raising=False)
    client = TestClient(app)
    invented = client.post("/api/sites/Site/field_reports", json={"item_id": "Site_L0#Rmadeup", "outcome": "found",
                                                                  "reporter": "Crew"})
    assert invented.status_code == 404


def test_reports_on_interpreted_picks_are_accepted_and_shown(site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.server import app

    pick = pick_store.add_pick("Site_L1", channel="RD3", trace=PIPE_TRACE, sample=PIPE_SAMPLE, time_ns=8.0,
                               depth_m=0.4, velocity_m_per_ns=0.1, velocity_source="manual", dielectric=9.0, label="duct")
    monkeypatch.setattr(app.state, "dataset_dir", site, raising=False)
    client = TestClient(app)
    ok = client.post("/api/sites/Site/field_reports", json={"item_id": f"Site_L1#{pick.id}", "outcome": "not_found",
                                                            "reporter": "Crew"})
    assert ok.status_code == 200
    (item,) = dig_map.dig_items(site, "Site", include_interpreted=True)[0]
    assert (item.status, item.field_outcome) == ("interpreted", "not_found")


def test_a_corrupt_reports_file_is_flagged_not_fatal(site: Path) -> None:
    _confirm("Site_L0", _claim(_pipe_box(site, "Site_L0")))
    path = field_reports._path("Site_L0")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json")
    items, attention = dig_map.dig_items(site, "Site")
    assert len(items) == 1 and items[0].field_outcome == ""
    assert "unreadable" in attention[0].reason and str(path) not in attention[0].reason


def test_the_whole_loop_from_dig_map_to_a_field_verified_training_label(site: Path, tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from studio import training_export
    from studio.server import app

    _confirm("Site_L0", _claim(_pipe_box(site, "Site_L0")))
    monkeypatch.setattr(app.state, "dataset_dir", site, raising=False)
    client = TestClient(app)
    item_id = client.get("/api/sites/Site/dig.geojson").json()["features"][0]["properties"]["id"]
    assert client.post("/api/sites/Site/field_reports", json={"item_id": item_id, "outcome": "dug", "reporter": "Crew",
                                                              "depth_found_m": 0.42}).status_code == 200
    out = tmp_path / "labels"
    config = Path(__file__).resolve().parents[1] / "config.yaml"  # the fixture works from a temp folder
    assert training_export.main(["--out", str(out), "--dataset", str(site), "--config", str(config)]) == 0
    (entry,) = [json.loads(line) for line in (out / "manifest.jsonl").read_text().splitlines()
                if json.loads(line)["origin_field_verified"]]
    assert entry["origin_positive"] is True and entry["origin_field_depth_m"] == 0.42


# ---------------------------------------------------------------- confirming from the field map (2026-10-07)

from studio import site_map


def _pipe_suggestion(site: Path) -> dict:
    features = site_map.site_geojson(site, "Site")["features"]
    targets = [f["properties"] for f in features if f["properties"]["kind"] == "target" and f["properties"]["job"] == "Site_L0"]
    return min(targets, key=lambda t: abs(t["chainage_m"] - 4.0) + abs(t["depth_m"] - 0.4))


def _seen(suggestion: dict) -> dict:
    """Where the caller saw the suggestion, as the UI sends it."""
    return {"seen_trace": suggestion["trace"], "seen_time_ns": suggestion["time_ns"]}


def test_a_supervisor_confirms_a_suggestion_straight_onto_the_dig_map(site: Path) -> None:
    suggestion = _pipe_suggestion(site)
    review = dig_map.confirm_suggestion(site, "Site", suggestion["id"], identity="water pipe", material="",
                                        reviewer="Supervisor A", **_seen(suggestion))
    assert (review.status, review.layer, review.reviewer) == ("confirmed", "field map", "Supervisor A")
    (item,) = dig_map.dig_items(site, "Site")[0]
    assert item.id == f"Site_L0#{review.id}" and item.identity == "water pipe"
    assert item.chainage_m == pytest.approx(4.0, abs=0.05) and item.depth_m == pytest.approx(0.4, abs=0.04)


@pytest.mark.parametrize(("target", "kwargs", "error"), [
    ("Site_L0#T999", {}, KeyError), ("Elsewhere_L1#T1", {}, KeyError),
    (None, {"reviewer": " "}, ValueError), (None, {"identity": ""}, ValueError),
])
def test_bad_confirmations_are_refused(site: Path, target: str | None, kwargs: dict, error: type) -> None:
    args = {"identity": "pipe", "material": "", "reviewer": "Supervisor A", **_seen(_pipe_suggestion(site))} | kwargs
    with pytest.raises(error):
        dig_map.confirm_suggestion(site, "Site", target or _pipe_suggestion(site)["id"], **args)


@pytest.mark.parametrize("seen", [{"seen_trace": None}, {"seen_trace": PIPE_TRACE + 40}, {"seen_time_ns": 2.0}])
def test_a_suggestion_that_moved_since_it_was_seen_is_not_confirmed(site: Path, seen: dict) -> None:
    # ids are ranks: after a re-run, T<n> can be another target; confirming it blind would put the wrong one on the map
    suggestion = _pipe_suggestion(site)
    with pytest.raises(ValueError, match="reload|which suggestion"):
        dig_map.confirm_suggestion(site, "Site", suggestion["id"], identity="pipe", material="", reviewer="Sup",
                                   **(_seen(suggestion) | seen))
    assert dig_map.dig_items(site, "Site")[0] == []


def test_a_near_surface_suggestion_needs_the_radargram_checked(site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    suggestion = _pipe_suggestion(site) | {"near_surface": True}
    monkeypatch.setattr(site_map, "site_geojson", lambda *a, **k: {"features": [{"properties": suggestion}]})
    args = {"identity": "pipe", "material": "", "reviewer": "Sup", **_seen(suggestion)}
    with pytest.raises(ValueError, match="near the surface"):
        dig_map.confirm_suggestion(site, "Site", suggestion["id"], **args)
    dig_map.confirm_suggestion(site, "Site", suggestion["id"], **args, acknowledge_near_surface=True)
    assert len(dig_map.dig_items(site, "Site")[0]) == 1


@pytest.mark.parametrize("earlier", ["confirmed", "rejected"])
def test_a_decided_target_is_not_confirmed_again(site: Path, earlier: str) -> None:
    # a second confirm would duplicate the item, and a confirm after a rejection would silently override it
    box = _pipe_box(site, "Site_L0")
    _confirm("Site_L0", _claim(box, "cable"), decision=earlier, reviewer="Supervisor B")
    suggestion = _pipe_suggestion(site)
    with pytest.raises(dig_map.AlreadyDecided, match=f"already {earlier} by Supervisor B"):
        dig_map.confirm_suggestion(site, "Site", suggestion["id"], identity="pipe", material="", reviewer="Sup",
                                   **_seen(suggestion))
    assert len([r for r in review_store.load_reviews("Site_L0") if r.status == "confirmed"]) == (earlier == "confirmed")


def test_confirm_and_report_endpoints(site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.server import app

    monkeypatch.setattr(app.state, "dataset_dir", site, raising=False)
    client = TestClient(app)
    suggestion = _pipe_suggestion(site)
    target, seen = suggestion["id"], {"trace": suggestion["trace"], "time_ns": suggestion["time_ns"]}
    assert client.post("/api/sites/Site/confirm", json={"target_id": target, "identity": "pipe",
                                                        "reviewer": "Sup"}).status_code == 422  # not said where
    made = client.post("/api/sites/Site/confirm", json={"target_id": target, "identity": "pipe", "reviewer": "Sup", **seen})
    assert made.status_code == 200
    item_id = made.json()["dig_item_id"]
    assert made.json()["review"]["reviewer"] == "tester"  # the signed-in account, not the typed "Sup"
    assert client.get("/api/sites/Site/dig.geojson").json()["features"][0]["properties"]["id"] == item_id
    client.post("/api/sites/Site/field_reports", json={"item_id": item_id, "outcome": "found", "reporter": "Crew"})
    (report,) = client.get("/api/sites/Site/field_reports").json()
    assert report["item_id"] == item_id and report["outcome"] == "found" and report["reporter"] == "tester"
    assert client.post("/api/sites/Site/confirm", json={"target_id": "Site_L0#T999", "identity": "x",
                                                        "reviewer": "Sup"}).status_code == 404
    again = client.post("/api/sites/Site/confirm", json={"target_id": target, "identity": "x", "reviewer": "B", **seen})
    assert again.status_code == 409 and "Interpret" in again.json()["detail"]
