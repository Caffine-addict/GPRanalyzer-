"""Tests for studio/dig_map.py — only what people decided, findable on the ground, nothing guessed."""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import boxes as box_store
from studio import candidates, dig_map, imports, session
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
