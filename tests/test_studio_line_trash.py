"""Removing a line from the project keeps every byte of it, and restoring puts it back as it was.

Runs on a throwaway dataset in a temp folder: these tests never touch real survey data.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import line_trash, session
from studio import picks as pick_store

JOB = "Set9_LINE001"


@pytest.fixture
def dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(pick_store, "_ANNOTATIONS_ROOT", tmp_path / "annotations")
    lines = tmp_path / "Dataset" / "DSU_GPR_Files"
    (lines / JOB).mkdir(parents=True)
    (lines / JOB / "Single-01.RAD").write_bytes(b"radar bytes")
    (tmp_path / "annotations" / JOB).mkdir(parents=True)
    (tmp_path / "annotations" / JOB / "picks.json").write_text('{"picks": []}')
    return lines


def test_removing_moves_the_radar_files_and_annotations_together(dataset: Path) -> None:
    manifest = line_trash.remove(dataset, JOB, "Admin")
    assert JOB not in session.list_jobs(dataset)
    entry = dataset.parent / "removed_lines" / manifest["id"]
    assert (entry / "line" / "Single-01.RAD").read_bytes() == b"radar bytes"
    assert (entry / "annotations" / "picks.json").read_text() == '{"picks": []}'
    assert not (pick_store._ANNOTATIONS_ROOT / JOB).exists()
    assert [m["removed_by"] for m in line_trash.removed(dataset)] == ["Admin"]


def test_restoring_puts_the_line_back_as_it_was(dataset: Path) -> None:
    manifest = line_trash.remove(dataset, JOB, "Admin")
    line_trash.restore(dataset, manifest["id"])
    assert JOB in session.list_jobs(dataset)
    assert (dataset / JOB / "Single-01.RAD").read_bytes() == b"radar bytes"
    assert (pick_store._ANNOTATIONS_ROOT / JOB / "picks.json").read_text() == '{"picks": []}'
    assert line_trash.removed(dataset) == []


def test_a_line_with_no_annotations_leaves_no_empty_folder_behind(dataset: Path) -> None:
    (pick_store._ANNOTATIONS_ROOT / JOB / "picks.json").unlink()
    (pick_store._ANNOTATIONS_ROOT / JOB).rmdir()
    manifest = line_trash.remove(dataset, JOB, "Admin")
    assert manifest["had_annotations"] is False
    assert not (pick_store._ANNOTATIONS_ROOT / JOB).exists()
    line_trash.restore(dataset, manifest["id"])
    assert JOB in session.list_jobs(dataset)


def test_restore_refuses_when_the_name_is_taken_again(dataset: Path) -> None:
    manifest = line_trash.remove(dataset, JOB, "Admin")
    (dataset / JOB).mkdir()
    (dataset / JOB / "Single-01.RAD").write_bytes(b"a new line")
    with pytest.raises(ValueError, match="in the project again"):
        line_trash.restore(dataset, manifest["id"])
    assert (dataset / JOB / "Single-01.RAD").read_bytes() == b"a new line"  # nothing overwritten
    assert line_trash.removed(dataset)[0]["id"] == manifest["id"]  # and the removed copy still waits


def test_only_lines_in_the_project_can_be_removed_and_only_listed_entries_restored(dataset: Path) -> None:
    with pytest.raises(session.JobNotFoundError):
        line_trash.remove(dataset, "../../etc", "Admin")
    with pytest.raises(KeyError):
        line_trash.restore(dataset, "../annotations")


def test_the_route_needs_the_line_name_repeated(dataset: Path) -> None:
    from studio.server import app

    previous = getattr(app.state, "dataset_dir", None)
    app.state.dataset_dir = dataset
    try:
        client = TestClient(app)
        refused = client.request("DELETE", f"/api/jobs/{JOB}", json={"confirm": "something else"})
        assert refused.status_code == 422 and JOB in session.list_jobs(dataset)
        removed = client.request("DELETE", f"/api/jobs/{JOB}", json={"confirm": JOB})
        assert removed.status_code == 200 and JOB not in session.list_jobs(dataset)
        (entry,) = client.get("/api/removed-lines").json()
        assert client.post(f"/api/removed-lines/{entry['id']}/restore").status_code == 200
        assert JOB in session.list_jobs(dataset)
    finally:
        if previous is not None:
            app.state.dataset_dir = previous
