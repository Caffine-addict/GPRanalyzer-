"""Tests for core.boxes — phase-1 (box-only) annotation persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import boxes as box_store


@pytest.fixture(autouse=True)
def _isolated_annotations_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(box_store, "_ANNOTATIONS_ROOT", tmp_path / "annotations")


def test_load_boxes_returns_empty_list_when_no_file_exists() -> None:
    assert box_store.load_boxes("Job_9999") == []


def test_add_box_persists_and_round_trips_exact_values() -> None:
    box = box_store.add_box("Job_0703", channel="RAD", x=12.5, y=30.0, w=8.0, h=15.5, note="candidate hyperbola")

    loaded = box_store.load_boxes("Job_0703")
    assert loaded == [box]
    assert loaded[0].channel == "RAD"
    assert loaded[0].x == 12.5
    assert loaded[0].y == 30.0
    assert loaded[0].w == 8.0
    assert loaded[0].h == 15.5
    assert loaded[0].note == "candidate hyperbola"


def test_add_box_has_no_class_field() -> None:
    box = box_store.add_box("Job_0703", channel="RAD", x=0, y=0, w=1, h=1)
    assert not hasattr(box, "class_name")
    assert not hasattr(box, "label")


def test_add_box_assigns_unique_ids() -> None:
    b1 = box_store.add_box("Job_0703", channel="RAD", x=0, y=0, w=1, h=1)
    b2 = box_store.add_box("Job_0703", channel="RAD", x=5, y=5, w=1, h=1)
    assert b1.id != b2.id


def test_add_box_rejects_non_positive_dimensions() -> None:
    with pytest.raises(ValueError, match="positive"):
        box_store.add_box("Job_0703", channel="RAD", x=0, y=0, w=0, h=5)
    with pytest.raises(ValueError, match="positive"):
        box_store.add_box("Job_0703", channel="RAD", x=0, y=0, w=5, h=-1)


def test_delete_box_removes_only_the_matching_box() -> None:
    b1 = box_store.add_box("Job_0703", channel="RAD", x=0, y=0, w=1, h=1)
    b2 = box_store.add_box("Job_0703", channel="RA1", x=5, y=5, w=2, h=2)

    result = box_store.delete_box("Job_0703", b1.id)

    assert result is True
    remaining = box_store.load_boxes("Job_0703")
    assert remaining == [b2]


def test_delete_box_returns_false_for_unknown_id() -> None:
    box_store.add_box("Job_0703", channel="RAD", x=0, y=0, w=1, h=1)
    assert box_store.delete_box("Job_0703", "does-not-exist") is False


def test_boxes_are_isolated_per_job() -> None:
    box_store.add_box("Job_A", channel="RAD", x=0, y=0, w=1, h=1)
    box_store.add_box("Job_B", channel="RAD", x=0, y=0, w=1, h=1)

    assert len(box_store.load_boxes("Job_A")) == 1
    assert len(box_store.load_boxes("Job_B")) == 1


def test_a_job_name_that_could_leave_the_annotations_folder_is_refused() -> None:
    for unsafe in ("../etc", "a\\..\\evil", ".hidden", ""):
        with pytest.raises(ValueError, match="unsafe job name"):
            box_store.load_boxes(unsafe)


def test_concurrent_adds_to_one_job_all_land() -> None:
    import threading

    threads = [
        threading.Thread(target=box_store.add_box, args=("Job_0703",), kwargs={"channel": "RAD", "x": i, "y": 0.0, "w": 1.0, "h": 1.0})
        for i in range(16)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(box_store.load_boxes("Job_0703")) == 16
