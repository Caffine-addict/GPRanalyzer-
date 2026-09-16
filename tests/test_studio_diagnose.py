"""Tests for studio/diagnose.py — the job-level driver over detect/hyperbola.py.

The driver's own job is small and entirely about not losing or inventing anything: read the
boxes, load only the channels that exist, skip what it cannot measure, and write the file
atomically. Each of those is pinned here. The fitting itself is tested in
tests/test_detect_hyperbola.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from core import boxes as box_store
from core.contracts import ScanFrame
from studio import diagnose, session


@pytest.fixture(autouse=True)
def _isolated_annotations_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(box_store, "_ANNOTATIONS_ROOT", tmp_path / "annotations")
    monkeypatch.setattr(diagnose, "_ANNOTATIONS_ROOT", tmp_path / "annotations")


def _frame(n_traces: int = 120, n_samples: int = 100) -> ScanFrame:
    """A frame with a real hyperbola in it, so a diagnosis has something to measure."""
    rng = np.random.default_rng(0)
    traces = rng.normal(0, 0.01, size=(n_traces, n_samples)).astype(np.float32)
    x0, t0, k = 60.0, 30.0, 0.4
    for x in range(n_traces):
        t = np.sqrt(t0**2 + ((x - x0) / k) ** 2)
        if t < n_samples - 1:
            traces[x, int(t)] += 5.0
    return ScanFrame(
        source_type="spr_file",
        provenance={"raw_header": {"SPR_SHAFT_INTERVAL": "0.025"}},
        traces=traces,
        sample_interval_ns=0.1,
        dielectric_assumed=9.0,
        position_source="unknown",
    )


@pytest.fixture
def _one_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only RAD exists for this job; RA1 and RA2 do not."""
    frame = _frame()
    monkeypatch.setattr(
        session, "channel_path", lambda job_dir, ext: job_dir / f"Single-01.{ext}"
    )
    monkeypatch.setattr(diagnose.session, "load_frame", lambda job_dir, ext: frame)


def test_a_job_with_no_boxes_writes_an_empty_file_rather_than_nothing(tmp_path: Path) -> None:
    # An empty diagnoses.json is a real statement ("nothing to measure"); a missing file is
    # indistinguishable from "the step never ran".
    job_dir = tmp_path / "Job_0001"
    job_dir.mkdir()
    assert diagnose.diagnose_job(job_dir) == []
    written = diagnose._diagnoses_path("Job_0001")
    assert written.exists()
    assert json.loads(written.read_text()) == []


def test_a_box_on_a_missing_channel_is_skipped_not_guessed(tmp_path: Path) -> None:
    job_dir = tmp_path / "Job_0002"
    job_dir.mkdir()
    box_store.add_box("Job_0002", channel="RA2", x=10, y=10, w=20, h=20)
    # No channel files exist at all, so nothing is measurable.
    assert diagnose.diagnose_job(job_dir) == []


def test_a_measurable_box_gets_a_diagnosis_with_its_evidence(
    tmp_path: Path, _one_channel: None
) -> None:
    job_dir = tmp_path / "Job_0003"
    job_dir.mkdir()
    (job_dir / "Single-01.RAD").write_bytes(b"stub")
    box = box_store.add_box("Job_0003", channel="RAD", x=40, y=25, w=40, h=40)

    [diagnosis] = diagnose.diagnose_job(job_dir)
    assert diagnosis.box_id == box.id
    assert diagnosis.shape in {"point", "linear", "disturbed", "ambiguous"}
    # Whatever class it suggests, the numbers behind it must be present for a human to check.
    assert diagnosis.rationale
    assert diagnosis.peak_amplitude > 0
    assert diagnosis.aspect_ratio == pytest.approx(1.0)


def test_the_written_file_is_keyed_by_box_id_and_round_trips(
    tmp_path: Path, _one_channel: None
) -> None:
    job_dir = tmp_path / "Job_0004"
    job_dir.mkdir()
    (job_dir / "Single-01.RAD").write_bytes(b"stub")
    first = box_store.add_box("Job_0004", channel="RAD", x=40, y=25, w=40, h=40)
    second = box_store.add_box("Job_0004", channel="RAD", x=20, y=20, w=15, h=30)

    diagnose.diagnose_job(job_dir)
    written = json.loads(diagnose._diagnoses_path("Job_0004").read_text())
    assert [entry["box_id"] for entry in written] == [first.id, second.id]
    assert all("rationale" in entry and "fit_r2" in entry for entry in written)


def test_rerunning_replaces_rather_than_appends(tmp_path: Path, _one_channel: None) -> None:
    job_dir = tmp_path / "Job_0005"
    job_dir.mkdir()
    (job_dir / "Single-01.RAD").write_bytes(b"stub")
    box_store.add_box("Job_0005", channel="RAD", x=40, y=25, w=40, h=40)

    diagnose.diagnose_job(job_dir)
    diagnose.diagnose_job(job_dir)
    written = json.loads(diagnose._diagnoses_path("Job_0005").read_text())
    assert len(written) == 1


def test_second_call_reads_the_cache_instead_of_recomputing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # studio/candidates.py calls diagnose_job on every /candidates request with no cache of
    # its own — this is the fix: reopening an unchanged job must not re-run the RANSAC fit.
    frame = _frame()
    calls = {"n": 0}

    def _counting_load_frame(job_dir, ext):
        calls["n"] += 1
        return frame

    monkeypatch.setattr(session, "channel_path", lambda job_dir, ext: job_dir / f"Single-01.{ext}")
    monkeypatch.setattr(diagnose.session, "load_frame", _counting_load_frame)

    job_dir = tmp_path / "Job_0006"
    job_dir.mkdir()
    (job_dir / "Single-01.RAD").write_bytes(b"stub")
    box_store.add_box("Job_0006", channel="RAD", x=40, y=25, w=40, h=40)

    first = diagnose.diagnose_job(job_dir)
    calls_after_first = calls["n"]
    second = diagnose.diagnose_job(job_dir)

    assert calls["n"] == calls_after_first  # no new channel load on the second call
    assert second == first


def test_adding_a_box_invalidates_the_cache(tmp_path: Path, _one_channel: None) -> None:
    job_dir = tmp_path / "Job_0007"
    job_dir.mkdir()
    (job_dir / "Single-01.RAD").write_bytes(b"stub")
    box_store.add_box("Job_0007", channel="RAD", x=40, y=25, w=40, h=40)

    first = diagnose.diagnose_job(job_dir)
    assert len(first) == 1

    box_store.add_box("Job_0007", channel="RAD", x=20, y=20, w=15, h=30)
    second = diagnose.diagnose_job(job_dir)
    assert len(second) == 2  # the new box must actually be measured, not hidden by stale cache


def test_force_true_recomputes_even_when_the_cache_is_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = _frame()
    calls = {"n": 0}

    def _counting_load_frame(job_dir, ext):
        calls["n"] += 1
        return frame

    monkeypatch.setattr(session, "channel_path", lambda job_dir, ext: job_dir / f"Single-01.{ext}")
    monkeypatch.setattr(diagnose.session, "load_frame", _counting_load_frame)

    job_dir = tmp_path / "Job_0008"
    job_dir.mkdir()
    (job_dir / "Single-01.RAD").write_bytes(b"stub")
    box_store.add_box("Job_0008", channel="RAD", x=40, y=25, w=40, h=40)

    diagnose.diagnose_job(job_dir)
    calls_after_first = calls["n"]
    diagnose.diagnose_job(job_dir, force=True)
    assert calls["n"] > calls_after_first  # force=True must not read the cache


def test_a_corrupted_cache_file_falls_back_to_recomputing_rather_than_raising(
    tmp_path: Path, _one_channel: None
) -> None:
    job_dir = tmp_path / "Job_0009"
    job_dir.mkdir()
    (job_dir / "Single-01.RAD").write_bytes(b"stub")
    box_store.add_box("Job_0009", channel="RAD", x=40, y=25, w=40, h=40)

    diagnose.diagnose_job(job_dir)
    diagnose._diagnoses_path("Job_0009").write_text("not valid json at all {{{")

    # Must not raise — a speed path failing must degrade to the correct, slower path.
    result = diagnose.diagnose_job(job_dir)
    assert len(result) == 1


def test_an_unsafe_job_name_cannot_escape_the_annotations_directory() -> None:
    for unsafe in ("../etc", "", ".hidden", "a\\..\\evil"):
        with pytest.raises(ValueError, match="unsafe job name"):
            diagnose._diagnoses_path(unsafe)
