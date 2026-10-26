"""Linking targets across lines into utility runs, and the run claims a supervisor decides."""

from __future__ import annotations

from typing import Any

import pytest

from studio import runs

V = 0.1  # m/ns


def _line(job: str, east: float, targets: list[tuple[float, float, str]], velocity: str = "assumed") -> dict[str, Any]:
    """A 10 m line running north at `east`; targets are (chainage, depth, contrast) from the site picker."""
    return {
        "job": job, "channel": "RAD", "source": "gps_track", "covered_m": [0.0, 10.0], "path": [[0.0, east, 0.0], [10.0, east, 10.0]],
        "velocity": {"value": V, "source": velocity}, "time_zero_ns": 2.0,
        "targets": [{"kind": "detected", "id": f"{job}#T{k + 1}", "job": job, "trace": c / 0.025, "chainage_m": c,
                     "time_ns": 2 * d / V, "channels": ["RAD", "RA1"], "contrast": contrast}
                    for k, (c, d, contrast) in enumerate(targets)],
    }


def _scene(lines: list[dict[str, Any]]) -> dict[str, Any]:
    return {"site": "S", "origin": {"lat": 12.97, "lon": 77.6}, "lines": lines, "located": [], "unplaced": []}


def test_a_pipe_crossing_four_lines_is_one_run() -> None:
    lines = [_line(f"S_L{i}", float(i), [(5.0 + 0.05 * i, 0.50 + 0.01 * i, "denser"), (1.0 + 2.0 * i, 0.9, "unreadable")])
             for i in range(4)]
    found = runs.site_runs(_scene(lines))
    assert len(found["runs"]) == 1
    run = found["runs"][0]
    assert sorted(t["id"] for t in run["targets"]) == ["S_L0#T1", "S_L1#T1", "S_L2#T1", "S_L3#T1"]  # in across order
    assert run["lines"] == 4 and len(run["links"]) == 3
    assert run["fit"]["crossing_deg"] > 80  # an east-west pipe across north-running lines
    assert run["contrast"] == "denser"
    assert 0 < run["confidence"] <= 1
    assert all(link["depth_delta_m"] <= link["tolerance_m"] for link in run["links"])


def test_opposite_polarity_breaks_the_link() -> None:
    contrasts = ["denser", "denser", "lighter", "denser"]
    lines = [_line(f"S_L{i}", float(i), [(5.0, 0.5, contrasts[i])]) for i in range(4)]
    assert runs.site_runs(_scene(lines))["runs"] == []  # 2 lines either side of the break: too short


def test_depth_tolerance_follows_the_velocity_basis() -> None:
    # 0.5 m apart in depth from one line to the next is beyond any tolerance at this depth.
    lines = [_line(f"S_L{i}", float(i), [(5.0, 0.4 + 0.5 * (i % 2), "unreadable")]) for i in range(4)]
    assert runs.site_runs(_scene(lines))["runs"] == []
    fitted = runs._depth_error(1.0, "fitted")
    assumed = runs._depth_error(1.0, "assumed")
    assert fitted < assumed


def test_a_target_off_the_line_is_dropped_by_the_straight_fit() -> None:
    norths = [5.0, 5.0, 5.0, 5.0, 5.9]  # the last one is 0.9 m off the line the others make
    lines = [_line(f"S_L{i}", float(i), [(n, 0.5, "unreadable")]) for i, n in enumerate(norths)]
    run = runs.site_runs(_scene(lines))["runs"][0]
    assert run["dropped_by_fit"] == ["S_L4#T1"]
    assert run["lines"] == 4


def test_runs_are_proposed_never_confirmed_and_a_decision_is_recorded(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runs, "_SITES_ROOT", tmp_path / "_sites")
    lines = [_line(f"S_L{i}", float(i), [(5.0, 0.5, "unreadable")]) for i in range(4)]
    found = runs.site_runs(_scene(lines))
    (claim,) = runs.claims("S", found)
    assert claim["status"] == "proposed" and claim["reviewer"] == ""
    with pytest.raises(ValueError, match="reviewer"):
        runs.decide("S", claim["id"], "confirmed", "  ")
    runs.decide("S", claim["id"], "confirmed", "Admin", "dug, found")
    (again,) = runs.claims("S", found)  # finding it again keeps the decision
    assert (again["status"], again["reviewer"], again["note"]) == ("confirmed", "Admin", "dug, found")
    with pytest.raises(KeyError):
        runs.decide("S", "run-missing", "rejected", "Admin")


def test_only_confirmed_runs_export_with_depth_below_ground_and_no_grade(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runs, "_SITES_ROOT", tmp_path / "_sites")
    found = runs.site_runs(_scene([_line(f"S_L{i}", float(i), [(5.0, 0.5, "unreadable")]) for i in range(4)]))
    assert runs.confirmed_geojson("S", runs.claims("S", found))["features"] == []
    runs.decide("S", found["runs"][0]["id"], "confirmed", "Admin")
    (feature,) = runs.confirmed_geojson("S", runs.claims("S", found))["features"]
    assert [round(c[2], 2) for c in feature["geometry"]["coordinates"]] == [-0.5] * 4
    assert feature["properties"]["quality_level"]["pas128"] == "ungraded"


def test_a_proposed_run_follows_the_data_but_a_decided_one_keeps_what_it_was_decided_on(
        tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runs, "_SITES_ROOT", tmp_path / "_sites")

    def found(depth: float) -> dict[str, Any]:
        return runs.site_runs(_scene([_line(f"S_L{i}", float(i), [(5.0, depth, "unreadable")]) for i in range(4)]))

    (first,) = runs.claims("S", found(0.5))
    (moved,) = runs.claims("S", found(0.6))  # e.g. a fitted velocity arrived: same targets, new depth
    assert first["id"] == moved["id"] and moved["depth_m"] == pytest.approx(0.6)
    runs.decide("S", first["id"], "confirmed", "Admin")
    runs.decide("S", first["id"], "rejected", "Admin", "second look")
    (decided,) = runs.claims("S", found(0.7))
    assert decided["depth_m"] == pytest.approx(0.6)  # what it was decided on
    stored = runs._load("S")[first["id"]]
    assert [h["status"] for h in stored["history"]] == ["confirmed", "rejected"]  # nothing overwritten silently


def test_a_confirmed_run_the_data_no_longer_produces_is_not_exported(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runs, "_SITES_ROOT", tmp_path / "_sites")
    found = runs.site_runs(_scene([_line(f"S_L{i}", float(i), [(5.0, 0.5, "unreadable")]) for i in range(4)]))
    runs.claims("S", found)
    runs.decide("S", found["runs"][0]["id"], "confirmed", "Admin")
    gone = runs.site_runs(_scene([_line(f"S_L{i}", float(i), []) for i in range(4)]))
    exported = runs.confirmed_geojson("S", runs.claims("S", gone))
    assert exported["features"] == [] and exported["metadata"]["not_exported"] == [found["runs"][0]["id"]]


def test_a_corrupt_claims_file_is_reported_not_crashed_on(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runs, "_SITES_ROOT", tmp_path / "_sites")
    (tmp_path / "_sites" / "S").mkdir(parents=True)
    (tmp_path / "_sites" / "S" / "runs.json").write_text("{not json")
    with pytest.raises(ValueError, match="not a valid run-claims file"):
        runs.claims("S", {"runs": [], "chance": {}})


def test_picks_from_another_channel_are_not_linked() -> None:
    line = _line("S_L0", 0.0, [])
    line["targets"] = [{"kind": "pick", "id": "p1", "channel": "RA2", "trace": 200, "sample": 100, "dt_ns": 0.4, "chainage_m": 5.0}]
    assert runs.link_targets(line) == []
    line["targets"][0]["channel"] = "RAD"
    assert [t["id"] for t in runs.link_targets(line)] == ["p1"]
