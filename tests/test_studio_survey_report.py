"""The site survey report: what it says from the data, and who may sign it off."""

from __future__ import annotations

from typing import Any

import pytest

from core.geo import from_local
from studio import survey_content, survey_report

ORIGIN = {"lat": 12.97, "lon": 77.6}
V = 0.12


def _line(job: str, east: float, detected: list[tuple[float, float]]) -> dict[str, Any]:
    """A 10 m GPS-track line running north at `east`; detected targets as (chainage, depth below ground)."""
    return {"job": job, "source": "gps_track", "channel": "RAD", "covered_m": [0.0, 10.0], "time_zero_ns": 2.0,
            "path": [[0.0, east, 0.0], [10.0, east, 10.0]], "position_error_m": 3.0,
            "velocity": {"value": V, "source": "site", "basis": "measured"},
            "targets": [{"kind": "detected", "id": f"{job}#T{k + 1}", "chainage_m": c, "time_ns": 2 * d / V,
                         "channels": ["RAD"], "trace": c / 0.025} for k, (c, d) in enumerate(detected)]}


def _record(east: float, north: float, depth: float, utility: str = "WATER") -> dict[str, Any]:
    lat, lon = from_local(east, north, ORIGIN["lat"], ORIGIN["lon"])
    return {"geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"utility": utility, "depth_m": depth, "position_error_m": 1.0, "source": "drawing A"}}


def _content(records: list[dict[str, Any]]) -> dict[str, Any]:
    scene = {"origin": ORIGIN, "lines": [_line("S_L1", 0.0, [(5.0, 0.5)]), _line("S_L2", 2.0, [])],
             "located": [], "unplaced": [{"job": "S_L3", "reason": "no usable GPS"}]}
    found = {"runs": [], "chance": {"runs": 0, "chance_mean": 0.0, "p_chance": 1.0, "beats_chance": False,
                                    "verdict": "unconfirmed: runs here are no more common than at random positions"}}
    ground = {"lines": [{"job": "S_L1", "dielectric": 6.2, "wet": False}, {"job": "S_L2", "dielectric": None, "wet": False}],
              "sections": [], "typical_error": 0.22}
    meta = {job: {"header": {"SPR_MEDIUM_DIELECTRIC": "01.00", "INSTRUMENT": "SPRScan"}, "channels": [],
                  "usable": {"time_ns": 22.0, "window_ns": 25.6}} for job in ("S_L1", "S_L2", "S_L3")}
    software = {"version": "0.1.0", "commit": "abc1234", "detector": "migration-picker v4", "models": ["m"]}
    return survey_content.build("S", scene, found, [], ground, records, meta, software)


def test_records_are_matched_to_detections_within_their_combined_position_error() -> None:
    content = _content([_record(0.5, 5.0, 0.5), _record(0.5, 5.0, 1.6, "GAS"), _record(1.0, 9.0, 0.8, "ELECTRIC")])
    # Same place, same depth: matched, nothing to report. Same place, 1.1 m deeper: a conflict.
    assert len(content["records"]["conflicts"]) == 1 and "GAS" in content["records"]["conflicts"][0]
    # 4 m from the only detection, beyond the 1 m record + 3 m line error: expected but not detected.
    assert len(content["records"]["not_detected"]) == 1 and "ELECTRIC" in content["records"]["not_detected"][0]


def test_records_far_from_the_survey_are_not_in_the_area() -> None:
    content = _content([_record(500.0, 500.0, 0.5)])
    assert content["plan"]["records"] == [] and content["records"]["not_detected"] == []


def test_penetration_uses_each_line_s_own_velocity_and_says_when_there_is_none() -> None:
    rows = {row[0]: row for row in _content([])["penetration"]["rows"]}
    assert rows["S_L1"][1] == f"{V * (22.0 - 2.0) / 2:.2f} m (22.0 ns)"
    assert rows["S_L3"][1] == "22.0 ns" and "no velocity" in rows["S_L3"][2]  # unplaced: no velocity, no depth


def test_an_unusable_header_dielectric_is_labelled_not_reported_as_a_value() -> None:
    assert all("not usable" in row[5] for row in _content([])["metadata"])


def test_nothing_is_graded() -> None:
    assert _content([])["quality"]["note"].startswith("Quality level: ungraded")


@pytest.fixture
def sites_root(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(survey_report, "_SITES_ROOT", tmp_path / "_sites")


def test_only_another_account_can_approve_and_only_the_content_it_saw(sites_root) -> None:
    content = _content([])
    with pytest.raises(ValueError, match="project form"):
        survey_report.approve("S", "bob", "PAS 128 trained", "x")
    state = survey_report.save_form("S", {"client": "City", "surveyors": "Asha, GPR level 2"}, "asha")
    digest = survey_report.content_hash(content, state)
    with pytest.raises(ValueError, match="cannot approve"):
        survey_report.approve("S", "Asha", "PAS 128 trained", digest)  # same person, different case
    with pytest.raises(ValueError, match="qualifies"):
        survey_report.approve("S", "bob", "  ", digest)
    survey_report.approve("S", "bob", "PAS 128 trained", digest)
    state = survey_report.load("S")
    assert survey_report.status(state, digest)["state"] == "APPROVED"
    changed = survey_report.content_hash({**content, "area": "changed"}, state)
    assert survey_report.status(state, changed) == {"state": "DRAFT", "approval": None, "why": "changed since it was approved"}


def test_the_form_takes_only_its_own_fields(sites_root) -> None:
    with pytest.raises(ValueError, match="only these fields"):
        survey_report.save_form("S", {"client": "x", "grade": "QL-B1"}, "asha")


def test_the_pdf_renders_as_a_draft(sites_root) -> None:
    content = _content([])
    state = survey_report.load("S")
    pdf = survey_report.pdf("S", content, state, survey_report.content_hash(content, state))
    assert pdf.startswith(b"%PDF") and len(pdf) > 5000
