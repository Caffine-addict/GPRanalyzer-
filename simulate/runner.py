"""Running gprMax as a separate program, in its own container.

gprMax is GPL-3.0, so it runs out of process: this module writes a scene's input files
into the scene's own folder, runs the container against that folder, and leaves gprMax's
output files beside the inputs. Nothing here imports gprMax.

Runs are resumable. A scene whose outputs already exist is skipped, so a long batch that
stops halfway — a laptop lid closed overnight — picks up where it left off rather than
starting again.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from simulate import gprmax_input, instrument
from simulate.scenes import Scene

DEFAULT_IMAGE = "gpr-analyzer-gprmax:950d0e19"

SCENE_FILE = "scene.json"
SCENE_INPUT = "scene.in"
BACKGROUND_INPUT = "background.in"
SCENE_OUTPUT = "scene_merged.out"
BACKGROUND_OUTPUT = "background.out"  # gprMax names a single run's output after its input
LOG_FILE = "gprmax.log"
_CONTAINER_DIR = "/work"


class SimulationError(RuntimeError):
    """gprMax exited with an error, or finished without writing the expected output."""


def prepare_scene(scene: Scene, runs_dir: Path, dx_m: float = gprmax_input.DEFAULT_DX_M) -> Path:
    """Write a scene's description and both gprMax inputs into `runs_dir/<scene_id>/`."""
    scene_dir = runs_dir / scene.scene_id
    scene_dir.mkdir(parents=True, exist_ok=True)
    grid = gprmax_input.grid_for(scene, dx_m)
    (scene_dir / SCENE_FILE).write_text(json.dumps({"scene": scene.to_dict(), "dx_m": dx_m}, indent=2) + "\n")
    (scene_dir / SCENE_INPUT).write_text(gprmax_input.scene_input(scene, grid))
    (scene_dir / BACKGROUND_INPUT).write_text(gprmax_input.background_input(scene, grid))
    return scene_dir


def load_scene(scene_dir: Path) -> tuple[Scene, float]:
    """The scene and grid spacing a folder was prepared with."""
    try:
        raw = json.loads((scene_dir / SCENE_FILE).read_text())
        return Scene.from_dict(raw["scene"]), float(raw["dx_m"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"unreadable scene description in {scene_dir}: {exc}") from exc


def is_simulated(scene_dir: Path) -> bool:
    return (scene_dir / SCENE_OUTPUT).exists() and (scene_dir / BACKGROUND_OUTPUT).exists()


def pending_scenes(runs_dir: Path) -> list[Path]:
    """Prepared scene folders that have not been simulated yet, in name order."""
    if not runs_dir.exists():
        return []
    return sorted(d for d in runs_dir.iterdir() if (d / SCENE_FILE).exists() and not is_simulated(d))


def docker_command(scene_dir: Path, image: str, module: str, *args: str) -> list[str]:
    """`docker run` with the scene folder mounted as the container's working directory."""
    command = ["docker", "run", "--rm", "-v", f"{scene_dir.resolve()}:{_CONTAINER_DIR}", "-e", "MPLCONFIGDIR=/tmp"]
    if hasattr(os, "getuid"):  # POSIX: write outputs as the calling user, not root
        command += ["--user", f"{os.getuid()}:{os.getgid()}"]
    return [*command, image, module, *args]


def _run(command: list[str], log_path: Path) -> None:
    with log_path.open("a") as log:
        log.write("$ " + " ".join(command) + "\n")
        log.flush()
        completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
    if completed.returncode != 0:
        # gprMax's own last line names the problem ("Non-physical wave propagation: ..."),
        # which says far more than an exit code does.
        last_line = next((line.strip() for line in reversed(log_path.read_text().splitlines()) if line.strip()), "")
        raise SimulationError(f"gprMax exited {completed.returncode}: {last_line[:200]} (log: {log_path})")


def simulate_scene(scene_dir: Path, image: str = DEFAULT_IMAGE) -> float:
    """Run a prepared scene through gprMax. Returns wall-clock seconds (0 if already done)."""
    if is_simulated(scene_dir):
        return 0.0
    log_path = scene_dir / LOG_FILE
    start = time.monotonic()
    _run(docker_command(scene_dir, image, "gprMax", f"{_CONTAINER_DIR}/{BACKGROUND_INPUT}"), log_path)
    # --geometry-fixed: only the antenna moves between B-scan runs, so the model is built once.
    _run(
        docker_command(
            scene_dir, image, "gprMax", f"{_CONTAINER_DIR}/{SCENE_INPUT}",
            "-n", str(instrument.TRACES_PER_FRAME), "--geometry-fixed",
        ),
        log_path,
    )
    stem = SCENE_INPUT.removesuffix(".in")
    _run(docker_command(scene_dir, image, "tools.outputfiles_merge", f"{_CONTAINER_DIR}/{stem}", "--remove-files"), log_path)
    if not is_simulated(scene_dir):
        raise SimulationError(f"gprMax finished but {scene_dir} is missing its outputs — see {log_path}")
    return time.monotonic() - start
