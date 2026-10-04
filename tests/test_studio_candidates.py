"""Tests for studio/candidates.py and studio/__main__.py."""

from __future__ import annotations

import json
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


def test_a_new_line_gets_migration_candidates_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import numpy as np

    from studio import imports, session

    monkeypatch.chdir(tmp_path)  # annotations/ is written relative to the working directory
    dataset = tmp_path / "Lines"
    dataset.mkdir()
    rng = np.random.default_rng(0)
    traces = rng.normal(size=(120, 128)) * 5
    v, t = 0.1, np.arange(128) * 0.2
    for x in range(120):  # one hyperbola, apex at trace 60, 8 ns
        arrival = np.sqrt(8.0**2 + (2 * (x - 60) * 0.025 / v) ** 2)
        a = (np.pi * 0.4 * (t - arrival)) ** 2
        traces[x] += 400 * (1 - 2 * a) * np.exp(-a)
    header = b"SAMPLES:128\r\nFREQUENCY:5000\r\nDISTANCE INTERVAL:0.025\r\n"
    imports.import_line(dataset, "Line_1", [("l.rd3", traces.astype("<i2").tobytes()), ("l.rad", header)])

    job_dir = dataset / "Line_1"
    candidates.ensure_candidates("Line_1", job_dir)
    first = candidates.box_store.load_boxes("Line_1")
    assert first and {b.detector for b in first} == {candidates.MIGRATION_DETECTOR}
    assert any(b.x <= 60 <= b.x + b.w and b.y <= 40 <= b.y + b.h for b in first)  # 8 ns / 0.2 ns = sample 40
    candidates.ensure_candidates("Line_1", job_dir)
    assert [b.id for b in candidates.box_store.load_boxes("Line_1")] == [b.id for b in first]  # not regenerated
    assert session.available_channels(job_dir)[0].extension == "RD3"


def test_a_run_that_found_nothing_is_not_repeated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    runs = []

    def fake_generate(job_name: str, job_dir: Path) -> list:
        runs.append(job_name)
        marker = candidates.box_store.boxes_path(job_name).parent / candidates._MARKER
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"detector": candidates.MIGRATION_DETECTOR,
                                      "version": candidates.MIGRATION_DETECTOR_VERSION, "boxes": 0}))
        return []

    monkeypatch.setattr(candidates, "generate_candidates", fake_generate)
    candidates.ensure_candidates("Quiet_line", tmp_path)
    candidates.ensure_candidates("Quiet_line", tmp_path)
    assert runs == ["Quiet_line"]


def test_deeper_targets_get_wider_boxes() -> None:
    shallow = candidates.hyperbola_half_width_m(apex_ns=2.0, velocity_m_per_ns=0.1)
    deep = candidates.hyperbola_half_width_m(apex_ns=20.0, velocity_m_per_ns=0.1)
    assert shallow < deep
    assert deep == pytest.approx(0.5 * 0.1 * ((23.0**2 - 20.0**2) ** 0.5))  # where the limb is 3 ns below the apex


_REAL_SPR = Path("Dataset/DSU_GPR_Files/Job_0703")


@pytest.mark.skipif(not _REAL_SPR.exists(), reason="SPR dataset not present")
def test_three_channel_candidates_carry_their_corroboration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    job_dir = _REAL_SPR.resolve()  # before leaving the repository
    monkeypatch.chdir(tmp_path)
    candidates.generate_candidates("Job_0703", job_dir)
    boxes = candidates.box_store.load_boxes("Job_0703")
    assert {b.channel for b in boxes} == {"RAD", "RA1", "RA2"}
    assert all(b.corroboration in (1, 2, 3) for b in boxes)
    assert any(b.corroboration and b.corroboration >= 2 for b in boxes)  # the same targets show on several channels
