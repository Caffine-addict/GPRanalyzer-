"""Tests for core/annotation_io.py — safe, locked, atomic annotation files."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.annotation_io import safe_job_dir, write_text_atomically


@pytest.mark.parametrize("name", ["Job_0696", "job-1", "a.b"])
def test_ordinary_job_names_are_accepted(tmp_path: Path, name: str) -> None:
    assert safe_job_dir(tmp_path, name) == tmp_path / name


@pytest.mark.parametrize("name", ["", ".hidden", "..", "../etc", "a/b", "a\\..\\evil", "C:\\x", " job"])
def test_names_that_could_leave_the_folder_are_refused(tmp_path: Path, name: str) -> None:
    # A backslash is a path separator on Windows, where this may be deployed.
    with pytest.raises(ValueError, match="unsafe job name"):
        safe_job_dir(tmp_path, name)


def test_a_non_string_name_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_job_dir(tmp_path, None)  # type: ignore[arg-type]


def test_atomic_write_replaces_the_file_and_leaves_no_temporary_behind(tmp_path: Path) -> None:
    target = tmp_path / "job" / "picks.json"
    write_text_atomically(target, "first")
    write_text_atomically(target, "second")
    assert target.read_text() == "second"
    assert sorted(p.name for p in target.parent.iterdir()) == ["picks.json"]
