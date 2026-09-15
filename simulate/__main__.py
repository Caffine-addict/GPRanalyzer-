"""Command line for the synthetic-data pipeline.

    python -m simulate generate --runs sim_runs/batch1 --count 20 --seed 1
    python -m simulate run      --runs sim_runs/batch1              # resumable; Ctrl-C is safe
    python -m simulate build    --runs sim_runs/batch1 --out datasets/synthetic/batch1
    python -m simulate validate --runs sim_runs/batch1              # check the cavity rule on known voids/trenches

`run` needs the gprMax image: docker build -t gpr-analyzer-gprmax:950d0e19 simulate/docker
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.config import load_config
from simulate import dataset, gprmax_input, runner, validate
from simulate.scenes import random_scene


def _generate(args: argparse.Namespace) -> int:
    total_work = 0
    for index in range(args.start, args.start + args.count):
        scene = random_scene(index, args.seed)
        runner.prepare_scene(scene, args.runs, args.dx)
        total_work += gprmax_input.grid_for(scene, args.dx).cell_updates
    print(f"prepared {args.count} scenes in {args.runs} ({total_work / 1e12:.2f} T cell-updates to simulate)")
    return 0


def _run(args: argparse.Namespace) -> int:
    pending = runner.pending_scenes(args.runs)
    if args.limit is not None:
        pending = pending[: args.limit]
    if not pending:
        print(f"nothing to simulate in {args.runs}")
        return 0
    done_seconds: list[float] = []
    failures: list[str] = []
    for position, scene_dir in enumerate(pending, start=1):
        scene, dx_m = runner.load_scene(scene_dir)
        work = gprmax_input.grid_for(scene, dx_m).cell_updates
        try:
            seconds = runner.simulate_scene(scene_dir, args.image)
        except runner.SimulationError as exc:
            # One scene gprMax rejects must not cost the rest of an overnight batch.
            failures.append(scene_dir.name)
            print(f"[{position}/{len(pending)}] {scene_dir.name}: FAILED — {exc}", flush=True)
            continue
        done_seconds.append(seconds)
        remaining = (len(pending) - position) * (sum(done_seconds) / len(done_seconds))
        rate = f"{work / seconds / 1e6:.0f} M cell-updates/s" if seconds > 0 else "already simulated"
        print(
            f"[{position}/{len(pending)}] {scene_dir.name}: {seconds / 60:.1f} min, {rate}, "
            f"~{remaining / 3600:.1f} h left",
            flush=True,
        )
    if failures:
        print(f"{len(failures)} of {len(pending)} scenes failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


def _build(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    summary = dataset.build_dataset(args.runs, args.out, config.enhancement, val_fraction=args.val_fraction, seed=args.seed)
    print(f"{summary.frames} frames -> {args.out}  split {summary.by_split}  boxes {summary.boxes_by_class}")
    print(f"{summary.unlabelled_objects} objects left unlabelled (not visible above the noise) — reasons in manifest.jsonl")
    for line in summary.skipped:
        print(f"skipped {line}", file=sys.stderr)
    return 0


def _validate(args: argparse.Namespace) -> int:
    report = validate.cavity_rule_report(args.runs, seed=args.seed)
    print(f"voids called cavity:            {report.voids_called_cavity}/{report.voids_total}")
    print(f"trenches wrongly called cavity: {report.zones_called_cavity}/{report.zones_total}")
    print(f"boxes the rule declined to call: {report.undecided}")
    if report.voids_total == 0 or report.zones_total == 0:
        print("not enough simulated voids and trenches to judge the rule yet — simulate more scenes", file=sys.stderr)
    for line in report.skipped:
        print(f"skipped {line}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m simulate", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="write random scenes and their gprMax inputs")
    generate.add_argument("--runs", type=Path, required=True)
    generate.add_argument("--count", type=int, required=True)
    generate.add_argument("--seed", type=int, default=0)
    generate.add_argument("--start", type=int, default=0, help="first scene index, to extend a batch")
    generate.add_argument("--dx", type=float, default=gprmax_input.DEFAULT_DX_M, help="grid spacing in metres")
    generate.set_defaults(handler=_generate)

    run = commands.add_parser("run", help="simulate every prepared scene not yet simulated")
    run.add_argument("--runs", type=Path, required=True)
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--image", default=runner.DEFAULT_IMAGE)
    run.set_defaults(handler=_run)

    build = commands.add_parser("build", help="render simulated scenes into a YOLO dataset")
    build.add_argument("--runs", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--config", type=Path, default=Path("config.yaml"))
    build.add_argument("--val-fraction", type=float, default=0.15)
    build.add_argument("--seed", type=int, default=0)
    build.set_defaults(handler=_build)

    check = commands.add_parser("validate", help="check the cavity polarity rule on simulated voids and trenches")
    check.add_argument("--runs", type=Path, required=True)
    check.add_argument("--seed", type=int, default=0)
    check.set_defaults(handler=_validate)

    args = parser.parse_args(argv)
    if getattr(args, "count", 1) <= 0:
        parser.error("--count must be positive")
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
