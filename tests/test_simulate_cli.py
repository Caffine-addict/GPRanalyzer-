"""Tests for simulate/__main__.py — the generate / run / build command line."""

from __future__ import annotations

from pathlib import Path

import pytest

from simulate import __main__ as cli
from simulate import dataset, runner


def test_generate_prepares_the_requested_scenes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["generate", "--runs", str(tmp_path), "--count", "2", "--seed", "1"]) == 0
    assert sorted(d.name for d in tmp_path.iterdir()) == ["scene_00000", "scene_00001"]
    assert "T cell-updates" in capsys.readouterr().out


def test_generate_can_extend_a_batch_from_a_later_index(tmp_path: Path) -> None:
    cli.main(["generate", "--runs", str(tmp_path), "--count", "1", "--start", "5"])
    assert [d.name for d in tmp_path.iterdir()] == ["scene_00005"]


def test_generate_refuses_a_non_positive_count(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main(["generate", "--runs", str(tmp_path), "--count", "0"])


def test_run_with_nothing_pending_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["run", "--runs", str(tmp_path)]) == 0
    assert "nothing to simulate" in capsys.readouterr().out


def test_run_simulates_each_pending_scene_and_reports_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["generate", "--runs", str(tmp_path), "--count", "3"])
    simulated: list[str] = []
    monkeypatch.setattr(runner, "simulate_scene", lambda scene_dir, image: simulated.append(scene_dir.name) or 120.0)
    assert cli.main(["run", "--runs", str(tmp_path), "--limit", "2"]) == 0
    assert simulated == ["scene_00000", "scene_00001"]
    out = capsys.readouterr().out
    assert "[2/2] scene_00001: 2.0 min" in out and "M cell-updates/s" in out


def test_build_reports_the_dataset_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    summary = dataset.BuildSummary(
        frames=4, by_split={"train": 3, "val": 1}, boxes_by_class={"point_reflector": 6},
        unlabelled_objects=2, skipped=("scene_9: broken",),
    )
    monkeypatch.setattr(dataset, "build_dataset", lambda *args, **kwargs: summary)
    config = Path(__file__).resolve().parent.parent / "config.yaml"
    assert cli.main(["build", "--runs", str(tmp_path), "--out", str(tmp_path / "ds"), "--config", str(config)]) == 0
    captured = capsys.readouterr()
    assert "4 frames" in captured.out and "2 objects left unlabelled" in captured.out
    assert "skipped scene_9" in captured.err


def test_run_reports_a_failed_scene_and_carries_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["generate", "--runs", str(tmp_path), "--count", "3"])
    simulated: list[str] = []

    def fake(scene_dir: Path, image: str) -> float:
        if scene_dir.name == "scene_00001":
            raise runner.SimulationError("gprMax exited 1: Non-physical wave propagation")
        simulated.append(scene_dir.name)
        return 60.0

    monkeypatch.setattr(runner, "simulate_scene", fake)
    assert cli.main(["run", "--runs", str(tmp_path)]) == 1
    assert simulated == ["scene_00000", "scene_00002"]
    captured = capsys.readouterr()
    assert "scene_00001: FAILED" in captured.out and "Non-physical" in captured.out
    assert "1 of 3 scenes failed" in captured.err
