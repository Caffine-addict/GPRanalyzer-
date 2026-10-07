"""Tests for studio/training_export.py — supervisor decisions as training data."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from core.config import load_config
from studio.field_reports import FieldReport
from studio.picks import Pick
from studio.reviews import Review
from studio.training_export import examples_for_job, write_dataset

N_TRACES, N_SAMPLES = 1000, 512


def _review(x: float, status: str, rid: str = "r") -> Review:
    return Review(id=rid, channel="RAD", target_id="c1", target_kind="candidate", x=x, y=200.0, w=20.0, h=30.0,
                  identity="metal pipe", material="steel", confidence="low", why="", layer="L", model="m",
                  created_at="2026-10-04T10:00:00+00:00", status=status, reviewer="Supervisor A" if status != "proposed" else "",
                  decided_at="2026-10-04T11:00:00+00:00" if status != "proposed" else "")


def _pick(trace: float) -> Pick:
    return Pick(id="p1", channel="RAD", trace=trace, sample=150.0, time_ns=12.0, depth_m=0.6, velocity_m_per_ns=0.1,
                velocity_source="fitted", dielectric=9.0, label="suspected duct", note="",
                created_at="2026-10-04T09:00:00+00:00", fit_r2=0.98)


def _examples(reviews: list[Review], picks: list[Pick] | None = None):
    return examples_for_job("Job_1", reviews, picks or [], lambda channel: (N_TRACES, N_SAMPLES))


def test_confirmed_and_picked_targets_become_labels_rejections_become_hard_negatives() -> None:
    examples = _examples([_review(100.0, "confirmed", "a"), _review(600.0, "rejected", "b")], [_pick(800.0)])
    by_source = {e.origin.source_id: e for e in examples}
    assert by_source["a"].origin.positive and len(by_source["a"].labels) == 1
    assert not by_source["b"].origin.positive and by_source["b"].labels == ()
    assert by_source["p1"].origin.box_estimated and len(by_source["p1"].labels) == 1


def test_a_label_is_placed_relative_to_its_tile() -> None:
    (example,) = _examples([_review(100.0, "confirmed")])
    t0, s0, _, _ = example.window
    x, y, w, h = example.labels[0]
    assert (x + t0, y + s0, w, h) == (100.0, 200.0, 20.0, 30.0)


def test_a_tile_touching_an_unreviewed_claim_is_left_out() -> None:
    examples = _examples([_review(100.0, "confirmed", "a"), _review(130.0, "proposed", "b"), _review(700.0, "confirmed", "c")])
    assert [e.origin.source_id for e in examples] == ["c"]


def test_nothing_is_exported_from_undecided_claims_alone() -> None:
    assert _examples([_review(100.0, "proposed")]) == []


def test_tiles_stay_inside_the_record() -> None:
    (edge,) = _examples([_review(990.0, "confirmed")])
    t0, s0, t1, s1 = edge.window
    assert 0 <= t0 < t1 <= N_TRACES and 0 <= s0 < s1 <= N_SAMPLES


def test_the_written_dataset_trains_and_keeps_its_audit_trail(tmp_path: Path) -> None:
    examples = _examples([_review(100.0, "confirmed", "a"), _review(600.0, "rejected", "b")])
    traces = np.random.default_rng(0).normal(size=(N_TRACES, N_SAMPLES))
    counts = write_dataset(examples, lambda job, ch: traces, tmp_path, load_config(Path("config.yaml")).enhancement,
                           ["point_reflector", "linear_reflector", "disturbed_or_void"])
    assert counts == {"examples": 2, "positives": 1, "hard_negatives": 1, "field_verified": 0}
    labels = sorted((tmp_path / "labels").rglob("*.txt"))
    assert [len(p.read_text().splitlines()) for p in labels] == [1, 0]
    cls, cx, cy, w, h = map(float, labels[0].read_text().split())
    assert cls == 0 and 0 < cx < 1 and 0 < cy < 1
    assert (w, h) == pytest.approx((20 / 128, 30 / 128))  # a 128-trace, 128-sample tile
    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert {m["origin_decided_by"] for m in manifest} == {"Supervisor A"}
    assert yaml.safe_load((tmp_path / "data.yaml").read_text())["names"][0] == "point_reflector"


# ---------------------------------------------------------------- field reports (2026-10-07)


def _report(item: str, outcome: str, at: str = "2026-10-07T12:00:00+00:00", depth: float | None = None,
            job: str = "Job_1") -> FieldReport:
    return FieldReport(id=f"F{at[-14:-6]}{item}", item_id=f"{job}#{item}", outcome=outcome, reporter="Crew lead", note="",
                       reported_at=at, lat=None, lon=None, accuracy_m=None, depth_found_m=depth)


def _with_reports(reviews: list[Review], picks: list[Pick], reports: list[FieldReport]):
    return examples_for_job("Job_1", reviews, picks, lambda channel: (N_TRACES, N_SAMPLES), reports)


def test_not_found_is_unknown_never_a_hard_negative() -> None:
    # The dig can miss by the GPS error: excluded, and no other tile may teach it as background.
    examples = _with_reports([_review(100.0, "confirmed", "a"), _review(125.0, "confirmed", "b")], [],
                             [_report("a", "not_found")])
    assert [e.origin.source_id for e in examples] == []  # b's tile overlaps a, so it is skipped too


def test_found_in_the_ground_overrides_a_rejection_and_keeps_the_measured_depth() -> None:
    (example,) = _with_reports([_review(100.0, "rejected", "a")], [], [_report("a", "dug", depth=0.42)])
    assert example.origin.positive and len(example.labels) == 1
    assert example.origin.field_depth_m == 0.42 and example.origin.source == "field (review)"


def test_the_latest_report_stands_and_picks_take_reports_too() -> None:
    reports = [_report("p1", "not_found", "2026-10-07T11:00:00+00:00"), _report("p1", "dug", "2026-10-07T12:00:00+00:00")]
    (example,) = _with_reports([], [_pick(500.0)], list(reversed(reports)))  # timestamps decide, not file order
    assert example.origin.positive and example.origin.field_verified


def test_a_report_about_something_else_changes_nothing() -> None:
    reports = [_report("zzz", "not_found"), _report("a", "not_found", job="Other_job")]
    (example,) = _with_reports([_review(100.0, "confirmed", "a")], [], reports)
    assert example.origin.positive and not example.origin.field_verified


def test_a_supervisor_decision_on_a_pick_replaces_it_and_takes_its_reports() -> None:
    review = _review(484.0, "confirmed", "a")
    on_pick = Review(**{**review.__dict__, "target_kind": "pick", "target_id": "p1"})
    (example,) = _with_reports([on_pick], [_pick(500.0)], [_report("p1", "dug", depth=0.5)])
    assert example.origin.source == "field (review)" and example.origin.field_depth_m == 0.5


def test_a_decision_made_after_the_report_stands() -> None:
    (example,) = _with_reports([_review(100.0, "rejected", "a")], [], [_report("a", "found", "2026-10-04T09:00:00+00:00")])
    assert not example.origin.positive and not example.origin.field_verified  # decided 4 Oct 11:00, reported 4 Oct 09:00


def test_a_report_on_a_claim_still_awaiting_review_labels_nothing() -> None:
    assert _with_reports([_review(100.0, "proposed", "a")], [], [_report("a", "dug")]) == []


def test_field_labels_reach_the_manifest(tmp_path: Path) -> None:
    examples = _with_reports([_review(100.0, "confirmed", "a")], [], [_report("a", "dug", depth=0.42)])
    traces = np.random.default_rng(0).normal(size=(N_TRACES, N_SAMPLES))
    counts = write_dataset(examples, lambda job, ch: traces, tmp_path, load_config(Path("config.yaml")).enhancement,
                           ["point_reflector", "linear_reflector", "disturbed_or_void"])
    assert counts["field_verified"] == 1
    (entry,) = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert entry["origin_field_verified"] is True and entry["origin_field_depth_m"] == 0.42
