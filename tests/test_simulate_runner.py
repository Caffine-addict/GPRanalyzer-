"""Tests for simulate/runner.py — preparing scene folders and running gprMax out of process."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from simulate import instrument, runner
from simulate.scenes import random_scene


def _prepared(tmp_path: Path, index: int = 0) -> Path:
    return runner.prepare_scene(random_scene(index, 3), tmp_path / "runs", 0.006)


def test_prepare_writes_the_description_and_both_inputs(tmp_path: Path) -> None:
    scene_dir = _prepared(tmp_path)
    for name in (runner.SCENE_FILE, runner.SCENE_INPUT, runner.BACKGROUND_INPUT):
        assert (scene_dir / name).exists()
    assert (scene_dir / runner.SCENE_INPUT).read_text().startswith("#title: scene_00000")


def test_a_prepared_scene_loads_back_exactly(tmp_path: Path) -> None:
    scene_dir = _prepared(tmp_path, 4)
    scene, dx = runner.load_scene(scene_dir)
    assert scene == random_scene(4, 3)
    assert dx == 0.006


def test_a_corrupt_description_is_reported_not_guessed_at(tmp_path: Path) -> None:
    scene_dir = _prepared(tmp_path)
    (scene_dir / runner.SCENE_FILE).write_text("{ not json")
    with pytest.raises(ValueError, match="unreadable scene description"):
        runner.load_scene(scene_dir)


def test_a_scene_counts_as_simulated_only_once_both_outputs_exist(tmp_path: Path) -> None:
    scene_dir = _prepared(tmp_path)
    assert not runner.is_simulated(scene_dir)
    (scene_dir / runner.SCENE_OUTPUT).touch()
    assert not runner.is_simulated(scene_dir)
    (scene_dir / runner.BACKGROUND_OUTPUT).touch()
    assert runner.is_simulated(scene_dir)


def test_pending_lists_prepared_unsimulated_scenes_in_order(tmp_path: Path) -> None:
    dirs = [_prepared(tmp_path, i) for i in (2, 0, 1)]
    (tmp_path / "runs" / "stray_folder").mkdir()
    for name in (runner.SCENE_OUTPUT, runner.BACKGROUND_OUTPUT):
        (dirs[1] / name).touch()  # scene_00000 is done
    assert [d.name for d in runner.pending_scenes(tmp_path / "runs")] == ["scene_00001", "scene_00002"]


def test_pending_in_a_missing_folder_is_empty(tmp_path: Path) -> None:
    assert runner.pending_scenes(tmp_path / "nowhere") == []


def test_docker_command_mounts_the_scene_folder_as_the_working_directory(tmp_path: Path) -> None:
    command = runner.docker_command(tmp_path, "img:tag", "gprMax", "/work/scene.in", "-n", "2")
    assert command[:3] == ["docker", "run", "--rm"]
    assert f"{tmp_path.resolve()}:/work" in command
    assert command[-5:] == ["img:tag", "gprMax", "/work/scene.in", "-n", "2"]


class _FakeDocker:
    """Records docker invocations; writes the outputs a real gprMax run would leave behind."""

    def __init__(self, scene_dir: Path, fail_on: str | None = None, write_outputs: bool = True) -> None:
        self.scene_dir, self.fail_on, self.write_outputs = scene_dir, fail_on, write_outputs
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **_kwargs) -> subprocess.CompletedProcess:
        self.calls.append(command)
        if self.fail_on and self.fail_on in command:
            return subprocess.CompletedProcess(command, 1)
        if self.write_outputs:
            if "/work/background.in" in command:
                (self.scene_dir / runner.BACKGROUND_OUTPUT).touch()
            if "tools.outputfiles_merge" in command:
                (self.scene_dir / runner.SCENE_OUTPUT).touch()
        return subprocess.CompletedProcess(command, 0)


def test_simulating_runs_background_then_the_b_scan_then_the_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene_dir = _prepared(tmp_path)
    fake = _FakeDocker(scene_dir)
    monkeypatch.setattr(runner.subprocess, "run", fake)
    assert runner.simulate_scene(scene_dir, "img") >= 0.0
    background, bscan, merge = fake.calls
    assert "/work/background.in" in background and "-n" not in background
    assert bscan[-4:] == ["-n", str(instrument.TRACES_PER_FRAME), "--geometry-fixed"][-4:] or "--geometry-fixed" in bscan
    assert bscan[bscan.index("-n") + 1] == str(instrument.TRACES_PER_FRAME)
    assert merge[-2:] == ["/work/scene", "--remove-files"]
    assert runner.is_simulated(scene_dir)


def test_an_already_simulated_scene_is_not_run_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene_dir = _prepared(tmp_path)
    for name in (runner.SCENE_OUTPUT, runner.BACKGROUND_OUTPUT):
        (scene_dir / name).touch()
    fake = _FakeDocker(scene_dir)
    monkeypatch.setattr(runner.subprocess, "run", fake)
    assert runner.simulate_scene(scene_dir) == 0.0
    assert fake.calls == []


def test_a_failed_run_raises_and_points_at_the_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene_dir = _prepared(tmp_path)
    monkeypatch.setattr(runner.subprocess, "run", _FakeDocker(scene_dir, fail_on="--geometry-fixed"))
    with pytest.raises(runner.SimulationError, match="gprmax.log"):
        runner.simulate_scene(scene_dir)
    assert "$ docker run" in (scene_dir / runner.LOG_FILE).read_text()


def test_a_run_that_leaves_no_outputs_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene_dir = _prepared(tmp_path)
    monkeypatch.setattr(runner.subprocess, "run", _FakeDocker(scene_dir, write_outputs=False))
    with pytest.raises(runner.SimulationError, match="missing its outputs"):
        runner.simulate_scene(scene_dir)
