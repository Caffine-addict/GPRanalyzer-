#!/usr/bin/env python3
"""Does adaptive-iteration RANSAC give the same fits as the fixed 2000-iteration loop? No.

This is the evidence behind a **rejected** optimisation, kept so nobody re-proposes it from
theory. `detect/hyperbola.py` still runs the fixed budget; nothing here is wired into
production.

The idea. `fit_hyperbola_ransac` runs a fixed 2000 iterations, and that loop is the single
largest cost in the pipeline (~22 ms/box, ~93% of a line). Standard RANSAC theory says the
iterations needed to draw one outlier-free 3-point sample with probability p are
N = log(1-p)/log(1-w**3). Measured inlier fractions on real data are 0.70 median, 0.36 worst,
which gives N = 17 and 148 at p=0.999 — suggesting a 13-20x saving.

Why it does not hold. That formula bounds the chance of *drawing a clean sample*. It says
nothing about having found the **maximal consensus set**, and this fitter's answer is a
least-squares refit over whichever inlier set was largest. With a 4-sample residual tolerance
admitting borderline ridge points, larger consensus sets keep turning up late in the run, and
each one shifts the refit. So stopping early does not return the same fit sooner — it returns
a different, less-supported fit.

Measured below: equivalence with the fixed budget holds only when the floor is raised to the
cap, i.e. only when the optimisation is switched off entirely.

Usage:
    .venv/bin/python scripts/ransac_adaptive_experiment.py
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from core import boxes as box_store
from detect import hyperbola
from detect.hyperbola import (
    _RANSAC_MIN_INLIER_FRACTION,
    _RANSAC_MIN_INLIERS,
    _RANSAC_RESIDUAL_TOL,
    HyperbolaFit,
)
from studio import session, velocity
from studio.velocity import fit_rejection_reason

_DATASET = Path("Dataset/DSU_GPR_Files")
_CAP = 2000
_CONFIDENCE = 0.999

# Stated tolerances. A fit that moves further than these is a different measurement.
TOL_X0_TRACES = 0.5      # half a trace = 12.5 mm on this instrument
TOL_T0_SAMPLES = 0.5
TOL_VELOCITY_REL = 0.01  # 1%
TOL_DEPTH_M = 0.01       # 1 cm


def _iterations_needed(inlier_fraction: float, floor: int) -> int:
    if inlier_fraction <= 0.0:
        return _CAP
    p_clean = inlier_fraction**3
    if p_clean >= 1.0:
        return floor
    needed = math.log(1.0 - _CONFIDENCE) / math.log(1.0 - p_clean)
    return int(min(_CAP, max(floor, math.ceil(needed))))


def _fit_adaptive(xs: np.ndarray, ts: np.ndarray, seed: int, floor: int) -> HyperbolaFit | None:
    """`fit_hyperbola_ransac` with an early exit. Same RNG stream, same consensus logic.

    A copy rather than a patch of the production function, so the experiment cannot change
    production behaviour by accident. `floor == _CAP` makes the exit unreachable and therefore
    reproduces the shipped fitter exactly — that is the baseline arm.
    """
    n = len(xs)
    if n < 3:
        return None
    min_inliers = max(_RANSAC_MIN_INLIERS, int(np.ceil(_RANSAC_MIN_INLIER_FRACTION * n)))

    rng = np.random.default_rng(seed)
    best_inliers: np.ndarray | None = None
    required = _CAP
    for iteration in range(_CAP):
        idx = rng.choice(n, size=3, replace=False)
        xs3, ts3_sq = xs[idx], ts[idx] ** 2
        design = np.stack([xs3**2, xs3, np.ones(3)], axis=1)
        try:
            c_coef, b_coef, a_coef = np.linalg.solve(design, ts3_sq)
        except np.linalg.LinAlgError:
            continue
        if c_coef <= 1e-9:
            continue
        k = 1 / np.sqrt(c_coef)
        x0 = -b_coef / (2 * c_coef)
        t0_sq = a_coef - b_coef**2 / (4 * c_coef)
        if t0_sq < 0:
            continue
        predicted = np.sqrt(t0_sq + ((xs - x0) / k) ** 2)
        inliers = np.abs(ts - predicted) < _RANSAC_RESIDUAL_TOL
        if inliers.sum() >= min_inliers and (
            best_inliers is None or inliers.sum() > best_inliers.sum()
        ):
            best_inliers = inliers
            required = _iterations_needed(int(inliers.sum()) / n, floor)
        if iteration + 1 >= required:
            break

    if best_inliers is None:
        return None
    xin, tin = xs[best_inliers], ts[best_inliers]
    c_coef, b_coef, a_coef = np.polyfit(xin, tin**2, 2)
    if c_coef <= 1e-9:
        return None
    k = 1 / np.sqrt(c_coef)
    x0 = -b_coef / (2 * c_coef)
    t0_sq = a_coef - b_coef**2 / (4 * c_coef)
    if t0_sq < 0:
        return None
    t0 = np.sqrt(t0_sq)
    predicted = np.sqrt(t0**2 + ((xin - x0) / k) ** 2)
    ss_res = np.sum((tin - predicted) ** 2)
    ss_tot = np.sum((tin - tin.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return HyperbolaFit(x0=x0, t0=t0, k=k, r2=r2, n_inliers=int(best_inliers.sum()), n_total=n)


def _run(floor: int) -> tuple[dict, float]:
    """Fit every stored box with the given floor, through the real `fit_region` path."""
    original = hyperbola.fit_hyperbola_ransac
    velocity.fit_hyperbola_ransac = lambda xs, ts, seed=0: _fit_adaptive(xs, ts, seed, floor)
    results: dict[str, dict] = {}
    elapsed = 0.0
    try:
        for job_dir in sorted(d for d in _DATASET.iterdir() if d.is_dir()):
            for box in box_store.load_boxes(job_dir.name):
                try:
                    frame = session.load_frame(job_dir, box.channel)
                except (session.JobNotFoundError, ValueError):
                    continue
                info = session.describe_channel(frame, box.channel)
                traces = session.raw_traces(frame)
                start = time.perf_counter()
                fit = velocity.fit_region(
                    traces,
                    trace_start=int(box.x), sample_start=int(box.y),
                    trace_span=max(int(box.w), 3), sample_span=max(int(box.h), 3),
                    trace_spacing_m=info.trace_spacing_m,
                    sample_interval_ns=info.sample_interval_ns, seed=0,
                )
                elapsed += (time.perf_counter() - start) * 1000
                key = f"{job_dir.name}/{box.channel}/{box.id}"
                results[key] = {"fit": fit} if fit is None else {
                    "fit": fit,
                    "rejection": fit_rejection_reason(
                        fit, n_samples=info.n_samples, sample_interval_ns=info.sample_interval_ns
                    ),
                }
    finally:
        velocity.fit_hyperbola_ransac = original
    return results, elapsed


def _compare(baseline: dict, candidate: dict) -> tuple[int, int, int, list[str]]:
    disagreements: list[str] = []
    identical = compared = 0
    for key in sorted(baseline):
        b, c = baseline[key], candidate[key]
        if (b["fit"] is None) != (c["fit"] is None):
            disagreements.append(
                f"{key}: accept/reject differs — baseline="
                f"{'fit' if b['fit'] else 'None'}, candidate={'fit' if c['fit'] else 'None'}"
            )
            continue
        if b["fit"] is None:
            continue
        compared += 1
        bf, cf = b["fit"], c["fit"]
        d_x0 = abs(bf.apex_trace - cf.apex_trace)
        d_t0 = abs(bf.apex_sample - cf.apex_sample)
        d_v = abs(bf.velocity_m_per_ns - cf.velocity_m_per_ns) / max(bf.velocity_m_per_ns, 1e-9)
        d_d = abs(bf.depth_m - cf.depth_m)
        if d_x0 == d_t0 == d_v == d_d == 0:
            identical += 1
        if b["rejection"] != c["rejection"]:
            disagreements.append(f"{key}: credibility verdict differs")
        if d_x0 > TOL_X0_TRACES:
            disagreements.append(f"{key}: x0 off by {d_x0:.3f} traces")
        if d_t0 > TOL_T0_SAMPLES:
            disagreements.append(f"{key}: t0 off by {d_t0:.3f} samples")
        if d_v > TOL_VELOCITY_REL:
            disagreements.append(f"{key}: velocity off by {100 * d_v:.2f}%")
        if d_d > TOL_DEPTH_M:
            disagreements.append(f"{key}: depth off by {d_d:.4f} m")
    return compared, identical, len(disagreements), disagreements


def main() -> int:
    if not _DATASET.exists():
        print(f"no dataset at {_DATASET}")
        return 1

    baseline, base_ms = _run(_CAP)
    n_boxes = len(baseline)
    n_lines = len([d for d in _DATASET.iterdir() if d.is_dir()])
    print(f"baseline (fixed {_CAP} iterations): {base_ms:.0f} ms over {n_boxes} boxes "
          f"= {base_ms / n_boxes:.2f} ms/box, {base_ms / n_lines:.0f} ms/line\n")

    print(f"{'floor':>7}{'disagreements':>15}{'identical':>12}{'ms/box':>9}{'ms/line':>9}{'speedup':>9}")
    worst_examples: list[str] = []
    for floor in (50, 100, 250, 500, 1000, 1500, 2000):
        candidate, ms = _run(floor)
        compared, identical, n_dis, examples = _compare(baseline, candidate)
        if floor == 50:
            worst_examples = examples[:8]
        print(f"{floor:>7}{n_dis:>15}{identical:>7}/{compared:<4}"
              f"{ms / n_boxes:>9.2f}{ms / n_lines:>9.0f}{base_ms / ms:>8.1f}x")

    print("\nA sample of the disagreements at floor=50 (the fastest setting):")
    for line in worst_examples:
        print(f"  - {line}")
    print("\nConclusion: equivalence holds only at floor=2000 — i.e. only with the early exit")
    print("disabled. The optimisation cannot be adopted without changing reported measurements.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
