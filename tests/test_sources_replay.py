"""Tests for ReplaySource: frame count, ordering, timing, and honest capabilities.

Uses a temp fixture directory rather than committed binary fixtures — the
generated images are trivial (a single flat-color array) since only shape/
ordering/count/timing are under test here, not detection quality.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import parsers.base
import parsers.image  # registers the image parser as a side effect
from core.config import ReplaySourceConfig
from core.contracts import ScanFrame
from sources.replay import ReplaySource


def _write_fixture_frames(directory: Path, names: list[str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        Image.fromarray(np.zeros((8, 8), dtype=np.uint8)).save(directory / name)


def test_replay_frame_count_and_ordering(tmp_path: Path) -> None:
    names = ["003.jpg", "001.jpg", "002.jpg"]
    _write_fixture_frames(tmp_path, names)

    source = ReplaySource(
        ReplaySourceConfig(directory=str(tmp_path), playback_rate_hz=1000.0, step_mode=True)
    )
    frames = list(source.frames())

    assert len(frames) == 3
    ordered_paths = [f.provenance["path"] for f in frames]
    assert ordered_paths == sorted(ordered_paths)  # filename order: 001, 002, 003


def test_replay_synthesizes_monotonic_synthetic_position(tmp_path: Path) -> None:
    _write_fixture_frames(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])

    source = ReplaySource(
        ReplaySourceConfig(directory=str(tmp_path), playback_rate_hz=1000.0, step_mode=True)
    )
    frames = list(source.frames())

    assert [f.position for f in frames] == [0.0, 1.0, 2.0]
    assert all(f.position_source == "synthetic" for f in frames)


def test_replay_capabilities_are_honest(tmp_path: Path) -> None:
    _write_fixture_frames(tmp_path, ["a.jpg"])
    source = ReplaySource(
        ReplaySourceConfig(directory=str(tmp_path), playback_rate_hz=1000.0, step_mode=True)
    )
    caps = source.capabilities()

    assert caps.has_calibrated_depth is False
    assert caps.has_real_position is False
    assert caps.has_true_amplitude is False
    assert caps.latency_class == "batch"


def test_replay_step_mode_true_has_no_delay(tmp_path: Path) -> None:
    _write_fixture_frames(tmp_path, [f"{i:03d}.jpg" for i in range(10)])
    source = ReplaySource(
        ReplaySourceConfig(directory=str(tmp_path), playback_rate_hz=1.0, step_mode=True)
    )

    start = time.monotonic()
    list(source.frames())
    elapsed = time.monotonic() - start

    assert elapsed < 0.5  # would take ~9s at 1Hz if step_mode weren't skipping the sleep


def test_replay_paces_playback_within_tolerance(tmp_path: Path) -> None:
    _write_fixture_frames(tmp_path, [f"{i:03d}.jpg" for i in range(5)])
    rate_hz = 20.0
    source = ReplaySource(
        ReplaySourceConfig(directory=str(tmp_path), playback_rate_hz=rate_hz, step_mode=False)
    )

    start = time.monotonic()
    list(source.frames())
    elapsed = time.monotonic() - start

    expected = 5 / rate_hz  # sleep happens after each yield, including the last
    assert expected * 0.5 <= elapsed <= expected * 2.0


def test_replay_missing_directory_raises(tmp_path: Path) -> None:
    source = ReplaySource(
        ReplaySourceConfig(
            directory=str(tmp_path / "does_not_exist"), playback_rate_hz=1.0, step_mode=True
        )
    )
    with pytest.raises(FileNotFoundError, match="not found"):
        list(source.frames())


def test_replay_empty_directory_raises(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    source = ReplaySource(
        ReplaySourceConfig(directory=str(empty_dir), playback_rate_hz=1.0, step_mode=True)
    )
    with pytest.raises(FileNotFoundError, match="no supported scan files"):
        list(source.frames())


def test_replay_ignores_unsupported_extensions(tmp_path: Path) -> None:
    _write_fixture_frames(tmp_path, ["a.jpg"])
    (tmp_path / "readme.txt").write_text("not a scan")

    source = ReplaySource(
        ReplaySourceConfig(directory=str(tmp_path), playback_rate_hz=1000.0, step_mode=True)
    )
    frames = list(source.frames())
    assert len(frames) == 1


def test_replay_derives_supported_extensions_from_the_parser_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # sources/replay.py used to keep its own independent extension allowlist; it now calls
    # parsers.base.registered_extensions() instead, so a newly-registered parser (parsers/spr.py's
    # "rad"/"ra1"/"ra2", or any future format) is automatically replayable with no edit here.
    # Every other test in this file only ever writes .jpg fixtures, which a stale hardcoded
    # fallback list (e.g. {"jpg", "jpeg", "png"}) would satisfy just as well -- registering a
    # throwaway extension nobody else uses and confirming ReplaySource picks it up is the only
    # way to prove the derivation is actually live, not coincidentally still working.
    def _fake_parser(path: Path) -> ScanFrame:
        return ScanFrame(
            source_type="fake", provenance={"path": str(path)}, image=np.zeros((4, 4), dtype=np.uint8)
        )

    monkeypatch.setitem(parsers.base._REGISTRY, "weirdfmt", _fake_parser)
    (tmp_path / "scan.weirdfmt").write_bytes(b"content is irrelevant -- the fake parser ignores it")

    source = ReplaySource(
        ReplaySourceConfig(directory=str(tmp_path), playback_rate_hz=1000.0, step_mode=True)
    )
    frames = list(source.frames())

    assert len(frames) == 1
    assert frames[0].provenance["path"].endswith("scan.weirdfmt")


def test_replay_single_step_via_next(tmp_path: Path) -> None:
    _write_fixture_frames(tmp_path, [f"{i:03d}.jpg" for i in range(3)])
    source = ReplaySource(
        ReplaySourceConfig(directory=str(tmp_path), playback_rate_hz=1000.0, step_mode=True)
    )

    it = source.frames()
    first = next(it)
    second = next(it)
    assert first.position == 0.0
    assert second.position == 1.0
