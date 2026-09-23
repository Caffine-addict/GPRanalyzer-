"""Tests for scripts/detect_candidates.py's `detector_version()`.

Only `detector_version()` is covered here. `detect_and_store()` itself is NOT covered by
this file, or by any other test in the suite — see the note in the module docstring above
`detect_and_store` and this session's test-quality report for why (it needs a real job
directory under the gitignored `Dataset/` tree, read through `studio.session.load_frame`'s
real file-format parsing; there is no fixture for that anywhere in this repo).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import detect_candidates as dc


def _fake_run(head: str, dirty: str):
    def run(args, **kwargs):
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(args, 0, stdout=head, stderr="")
        if args[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(args, 0, stdout=dirty, stderr="")
        raise AssertionError(f"unexpected subprocess call: {args}")

    return run


def test_detector_version_reports_clean_head(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run("abc1234\n", ""))
    assert dc.detector_version() == "abc1234"


def test_detector_version_flags_a_dirty_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run("abc1234\n", " M core/boxes.py\n"))
    assert dc.detector_version() == "abc1234-dirty"


def test_detector_version_falls_back_to_unknown_when_git_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_missing_git(args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", raise_missing_git)
    assert dc.detector_version() == "unknown"


def test_detector_version_falls_back_to_unknown_when_git_command_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_called_process_error(args, **kwargs):
        raise subprocess.CalledProcessError(returncode=128, cmd=args)

    monkeypatch.setattr(subprocess, "run", raise_called_process_error)
    assert dc.detector_version() == "unknown"
