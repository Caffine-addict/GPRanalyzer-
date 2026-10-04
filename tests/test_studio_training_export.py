"""Tests for studio/training_export.py — supervisor decisions as training data."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from core.config import load_config
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
    assert counts == {"examples": 2, "positives": 1, "hard_negatives": 1}
    labels = sorted((tmp_path / "labels").rglob("*.txt"))
    assert [len(p.read_text().splitlines()) for p in labels] == [1, 0]
    cls, cx, cy, w, h = map(float, labels[0].read_text().split())
    assert cls == 0 and 0 < cx < 1 and 0 < cy < 1
    assert (w, h) == pytest.approx((20 / 128, 30 / 128))  # a 128-trace, 128-sample tile
    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert {m["origin_decided_by"] for m in manifest} == {"Supervisor A"}
    assert yaml.safe_load((tmp_path / "data.yaml").read_text())["names"][0] == "point_reflector"
