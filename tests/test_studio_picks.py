"""Tests for studio/picks.py — interpreted targets and their velocity provenance."""

from __future__ import annotations

from pathlib import Path

import pytest

from studio import picks


@pytest.fixture(autouse=True)
def _isolated_annotations_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(picks, "_ANNOTATIONS_ROOT", tmp_path / "annotations")


def _add(job: str = "Job_0703", **overrides) -> picks.Pick:
    payload = {
        "channel": "RAD",
        "trace": 101.0,
        "sample": 125.0,
        "time_ns": 12.5,
        "depth_m": 0.63,
        "velocity_m_per_ns": 0.1011,
        "velocity_source": "fitted",
        "dielectric": 8.79,
        "fit_r2": 0.98,
    }
    payload.update(overrides)
    return picks.add_pick(job, **payload)


def test_no_picks_yet_is_not_an_error() -> None:
    assert picks.load_picks("Job_9999") == []


def test_a_pick_round_trips_with_every_field_intact() -> None:
    pick = _add(label="suspected service duct", note="crosses the drain")
    loaded = picks.load_picks("Job_0703")
    assert loaded == [pick]
    assert loaded[0].label == "suspected service duct"
    assert loaded[0].velocity_source == "fitted"
    assert loaded[0].fit_r2 == 0.98


def test_picks_accumulate_in_order() -> None:
    first = _add(label="one")
    second = _add(label="two")
    assert [p.id for p in picks.load_picks("Job_0703")] == [first.id, second.id]


def test_picks_are_scoped_per_job() -> None:
    _add("Job_0703", label="in 0703")
    assert picks.load_picks("Job_0720") == []


def test_a_fitted_velocity_without_its_r2_is_refused() -> None:
    # A measured velocity nobody can check is indistinguishable from a guess.
    with pytest.raises(ValueError, match="must carry the fit's r2"):
        _add(velocity_source="fitted", fit_r2=None)


def test_an_unfitted_velocity_carrying_an_r2_is_refused() -> None:
    with pytest.raises(ValueError, match="meaningless"):
        _add(velocity_source="assumed", fit_r2=0.9)


def test_manual_and_assumed_sources_are_accepted_without_an_r2() -> None:
    for source in ("manual", "assumed"):
        pick = _add(velocity_source=source, fit_r2=None)
        assert pick.velocity_source == source
        assert pick.fit_r2 is None


def test_an_unknown_velocity_source_is_refused() -> None:
    with pytest.raises(ValueError, match="velocity_source must be one of"):
        _add(velocity_source="vibes", fit_r2=None)


def test_a_non_positive_velocity_is_refused() -> None:
    with pytest.raises(ValueError, match="velocity must be positive"):
        _add(velocity_m_per_ns=0.0)


def test_deleting_a_pick_removes_only_that_one() -> None:
    keep = _add(label="keep")
    drop = _add(label="drop")
    assert picks.delete_pick("Job_0703", drop.id) is True
    assert [p.id for p in picks.load_picks("Job_0703")] == [keep.id]


def test_deleting_an_unknown_pick_reports_false_rather_than_raising() -> None:
    assert picks.delete_pick("Job_0703", "nope") is False


def test_a_corrupt_pick_file_raises_instead_of_being_silently_overwritten(tmp_path: Path) -> None:
    # Overwriting would destroy an interpreter's work without anyone noticing.
    _add()
    picks._picks_path("Job_0703").write_text("{ not json")
    with pytest.raises(ValueError, match="corrupt pick file"):
        picks.load_picks("Job_0703")


def test_an_unsafe_job_name_cannot_escape_the_annotations_directory() -> None:
    for unsafe in ("../etc", "", ".hidden", "a\\..\\evil"):
        with pytest.raises(ValueError, match="unsafe job name"):
            picks._picks_path(unsafe)


def test_csv_export_puts_velocity_provenance_on_every_row() -> None:
    _add(velocity_source="fitted", fit_r2=0.98, label="measured one")
    _add(velocity_source="assumed", fit_r2=None, label="assumed one")
    rows = picks.to_csv_rows(picks.load_picks("Job_0703"))

    header = rows[0]
    assert "velocity_source" in header
    source_column = header.index("velocity_source")
    assert [row[source_column] for row in rows[1:]] == ["fitted", "assumed"]


def test_csv_export_of_no_picks_is_still_a_header_row() -> None:
    assert len(picks.to_csv_rows([])) == 1


def test_concurrent_saves_to_one_job_all_land() -> None:
    # The Studio serves requests from a thread pool; without the lock two saves read
    # the same file and the later write erases the earlier pick.
    import threading

    threads = [threading.Thread(target=_add, kwargs={"label": f"t{i}"}) for i in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(p.label for p in picks.load_picks("Job_0703")) == sorted(f"t{i}" for i in range(16))
