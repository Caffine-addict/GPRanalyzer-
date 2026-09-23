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


# ------------------------------------- detector runs replace, never silently union (2026-09-23)


def _detected(**overrides) -> box_store.DetectedBox:
    base = {"channel": "RAD", "x": 10.0, "y": 20.0, "w": 8.0, "h": 6.0}
    base.update(overrides)
    return box_store.DetectedBox(**base)


def test_a_second_run_of_the_same_detector_replaces_its_own_output() -> None:
    """The bug this exists to prevent.

    The old `detect_and_store` only ever appended, skipping coordinate duplicates. Re-running it
    after changing the detector left both versions' output in the file, so `boxes.json` became a
    union across detector versions — 233 stored boxes where the current detector found 166, with
    67 unreproducible and 29% of the set written by a version known to be wrong.
    """
    box_store.replace_detector_boxes(
        "Job_0703", detector="edge-finder", detector_version="v1", boxes=[_detected(x=1.0), _detected(x=2.0)]
    )
    box_store.replace_detector_boxes(
        "Job_0703", detector="edge-finder", detector_version="v2", boxes=[_detected(x=3.0)]
    )

    stored = box_store.load_boxes("Job_0703")
    assert len(stored) == 1, "the second run must replace the first, not add to it"
    assert stored[0].x == 3.0
    assert stored[0].detector_version == "v2"


def test_a_different_detector_keeps_its_own_output() -> None:
    # Replacement is scoped to the detector that ran. Another detector's boxes are not its
    # business to delete.
    box_store.replace_detector_boxes(
        "Job_0703", detector="edge-finder", detector_version="v1", boxes=[_detected(x=1.0)]
    )
    box_store.replace_detector_boxes(
        "Job_0703", detector="other-detector", detector_version="v1", boxes=[_detected(x=9.0)]
    )

    detectors = {b.detector for b in box_store.load_boxes("Job_0703")}
    assert detectors == {"edge-finder", "other-detector"}


def test_human_picks_are_never_touched_by_a_detector_run() -> None:
    human = box_store.add_box("Job_0703", channel="RAD", x=50.0, y=60.0, w=5.0, h=5.0, note="operator: looks like a duct")
    box_store.replace_detector_boxes(
        "Job_0703", detector="edge-finder", detector_version="v1", boxes=[_detected()]
    )
    box_store.replace_detector_boxes(
        "Job_0703", detector="edge-finder", detector_version="v2", boxes=[_detected(x=99.0)]
    )

    stored = box_store.load_boxes("Job_0703")
    assert human.id in {b.id for b in stored}
    kept = next(b for b in stored if b.id == human.id)
    assert kept.note == "operator: looks like a duct"
    assert kept.detector is None  # a person is not a detector


def _write_legacy_boxes(job: str, notes: list[str]) -> None:
    """Write boxes the way the pre-provenance detector did: an "auto:" note, no detector.

    Written straight to disk rather than through `add_box`, because `add_box` now refuses the
    reserved prefix — these files can only be *inherited*, never newly created, which is
    precisely the situation the purge exists for.
    """
    import json

    path = box_store.boxes_path(job)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(path.read_text())["boxes"] if path.exists() else []
    legacy = [
        {"id": f"legacy{i:02d}", "channel": "RAD", "x": 7.0 + i, "y": 7.0, "w": 4.0, "h": 4.0, "note": note}
        for i, note in enumerate(notes)
    ]
    path.write_text(json.dumps({"boxes": existing + legacy}))


def test_legacy_unattributed_auto_boxes_are_purged_by_a_detector_run() -> None:
    """An auto box with no detector recorded cannot be attributed, so it cannot be trusted.

    These are exactly the 233-box union's contents: an "auto:" note and no provenance. Keeping
    them rebuilds the union this function exists to end. Several real note shapes are used, so
    the purge is pinned to the reserved prefix rather than to one exact historical string.
    """
    _write_legacy_boxes("Job_0703", [
        "auto: background-removal + energy-envelope candidate, unclassified",
        "auto: legacy detector output",
        "auto:",
    ])
    box_store.replace_detector_boxes(
        "Job_0703", detector="edge-finder", detector_version="v1", boxes=[_detected()]
    )

    stored = box_store.load_boxes("Job_0703")
    assert len(stored) == 1, f"legacy boxes survived: {[b.note for b in stored]}"
    assert stored[0].detector == "edge-finder"


def test_every_detector_box_records_who_made_it_and_in_which_run() -> None:
    box_store.replace_detector_boxes(
        "Job_0703", detector="edge-finder", detector_version="abc1234", boxes=[_detected(), _detected(x=2.0)]
    )
    stored = box_store.load_boxes("Job_0703")

    assert {b.detector for b in stored} == {"edge-finder"}
    assert {b.detector_version for b in stored} == {"abc1234"}
    run_ids = {b.run_id for b in stored}
    assert len(run_ids) == 1 and next(iter(run_ids))  # one run, and it is not empty


def test_two_runs_get_different_run_ids() -> None:
    box_store.replace_detector_boxes("Job_0703", detector="d", detector_version="v1", boxes=[_detected()])
    first = box_store.load_boxes("Job_0703")[0].run_id
    box_store.replace_detector_boxes("Job_0703", detector="d", detector_version="v1", boxes=[_detected()])
    second = box_store.load_boxes("Job_0703")[0].run_id
    assert first != second


def test_a_run_finding_nothing_clears_the_previous_run() -> None:
    # An empty result is a real answer. Leaving the old boxes would report a finding the
    # current detector does not stand behind.
    box_store.replace_detector_boxes("Job_0703", detector="d", detector_version="v1", boxes=[_detected()])
    box_store.replace_detector_boxes("Job_0703", detector="d", detector_version="v2", boxes=[])
    assert box_store.load_boxes("Job_0703") == []


def test_a_boxes_file_written_before_provenance_existed_still_loads(tmp_path: Path) -> None:
    import json

    path = box_store.boxes_path("Job_0703")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"boxes": [
        {"id": "old12345", "channel": "RAD", "x": 1.0, "y": 2.0, "w": 3.0, "h": 4.0, "note": "auto: old"}
    ]}))

    loaded = box_store.load_boxes("Job_0703")
    assert len(loaded) == 1
    assert loaded[0].detector is None
    assert loaded[0].detector_version is None
    assert loaded[0].run_id is None


def test_a_human_cannot_write_a_note_that_would_get_their_box_purged() -> None:
    """The "auto:" prefix is reserved, because a detector run deletes boxes that carry it.

    `_is_unattributed_auto_box` decides what to purge by sniffing note text, and the Studio
    passes an operator's free-typed note straight through. An operator writing
    "auto: probably the same duct as before" would have created a box indistinguishable from
    legacy machine output and lost it on the next detector run — irreplaceable data, destroyed
    silently. Refusing the prefix at the boundary is what makes the "human boxes are never
    touched" guarantee true rather than merely usually true.
    """
    with pytest.raises(ValueError, match="reserved"):
        box_store.add_box("Job_0703", channel="RAD", x=1, y=1, w=5, h=5, note="auto: same duct as before")
    # Case and leading space must not be a way around it.
    with pytest.raises(ValueError, match="reserved"):
        box_store.add_box("Job_0703", channel="RAD", x=1, y=1, w=5, h=5, note="  AUTO: sneaky")
    assert box_store.load_boxes("Job_0703") == []


def test_a_note_merely_containing_the_word_auto_is_fine() -> None:
    # Only the reserved prefix is refused. "auto" is an ordinary English word and an operator
    # must not have to think about our storage internals to write a note.
    box = box_store.add_box(
        "Job_0703", channel="RAD", x=1, y=1, w=5, h=5, note="looks automatic, maybe rebar"
    )
    assert box.note == "looks automatic, maybe rebar"
