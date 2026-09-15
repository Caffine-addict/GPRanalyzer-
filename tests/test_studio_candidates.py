"""Tests for studio/candidates.py and studio/__main__.py."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from studio import __main__ as entrypoint
from studio import candidates


@dataclass(frozen=True)
class _FakeBox:
    id: str
    channel: str
    x: float
    y: float
    w: float
    h: float
    note: str = ""


@dataclass(frozen=True)
class _FakeDiagnosis:
    box_id: str
    shape: str
    suggested_class: str | None
    rationale: str
    fit_r2: float | None
    implied_dielectric: float | None


def test_no_boxes_means_no_candidates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(candidates.box_store, "load_boxes", lambda job: [])
    assert candidates.load_candidates("Job_0703", tmp_path) == []


def test_a_box_is_joined_to_its_diagnosis(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        candidates.box_store, "load_boxes",
        lambda job: [_FakeBox("b1", "RAD", 10.0, 20.0, 30.0, 40.0, "looks like a duct")],
    )
    monkeypatch.setattr(
        candidates, "diagnose_job",
        lambda job_dir: [_FakeDiagnosis("b1", "point", "clear_point_reflector", "strong fit", 0.97, 8.8)],
    )

    [candidate] = candidates.load_candidates("Job_0703", tmp_path)
    assert candidate.id == "b1"
    assert candidate.channel == "RAD"
    assert (candidate.x, candidate.y, candidate.w, candidate.h) == (10.0, 20.0, 30.0, 40.0)
    assert candidate.note == "looks like a duct"
    assert candidate.suggested_class == "clear_point_reflector"
    assert candidate.fit_r2 == 0.97
    assert candidate.implied_dielectric == 8.8


def test_an_undiagnosed_box_is_still_returned_with_empty_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Dropping it because the measurement step never ran would hide a region a
    # human actually flagged.
    monkeypatch.setattr(
        candidates.box_store, "load_boxes", lambda job: [_FakeBox("b2", "RA1", 1.0, 2.0, 3.0, 4.0)]
    )
    monkeypatch.setattr(candidates, "diagnose_job", lambda job_dir: [])

    [candidate] = candidates.load_candidates("Job_0703", tmp_path)
    assert candidate.id == "b2"
    assert candidate.suggested_class is None
    assert candidate.shape is None
    assert candidate.rationale is None
    assert candidate.fit_r2 is None


def test_candidates_are_frozen_so_a_suggestion_cannot_be_edited_into_a_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        candidates.box_store, "load_boxes", lambda job: [_FakeBox("b3", "RAD", 0.0, 0.0, 5.0, 5.0)]
    )
    monkeypatch.setattr(candidates, "diagnose_job", lambda job_dir: [])
    [candidate] = candidates.load_candidates("Job_0703", tmp_path)
    with pytest.raises(AttributeError):
        candidate.suggested_class = "clear_point_reflector"  # type: ignore[misc]


def test_entrypoint_refuses_a_missing_dataset_directory(capsys: pytest.CaptureFixture[str]) -> None:
    assert entrypoint.main(["studio", "Dataset/does-not-exist"]) == 1
    assert "not found" in capsys.readouterr().err


def test_entrypoint_points_the_app_at_the_requested_dataset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    served: dict[str, object] = {}
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda app, **kwargs: served.update(kwargs))

    assert entrypoint.main(["studio", str(tmp_path)]) == 0
    assert entrypoint.app.state.dataset_dir == tmp_path
    assert served["port"] == entrypoint.DEFAULT_PORT
    # Bound to loopback: this is a desktop tool holding survey data, not a
    # service meant to be reachable from the network.
    assert served["host"] == "127.0.0.1"
