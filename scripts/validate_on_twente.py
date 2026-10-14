"""Score the Studio's analysis against professionally surveyed, excavated sites (Twente, CC0).

The dataset (docs/EXTERNAL_DATA.md): 125 utility surveys at 13 Dutch construction sites, 500 MHz
air-launched GPR. Each survey has a trial trench drawing (distance and depth of every utility
found) and the soil's relative permittivity, as the surveyors calibrated it.

Scored, each against a baseline that says what chance alone would give:

1. **Velocity**, two ways, against guessing the dataset's median permittivity for every survey.
   Arches are proposed by migrating at one fixed neutral velocity, so a survey's own permittivity
   never leaks in. (a) studio/velocity.fit_region on each arch, median per survey. (b) Semblance:
   studio/velocity.semblance_curve per arch, flat events rejected, summed over the survey
   (stacked_permittivity).
2. **Utility recall**: the Studio detector (detect/classical + fit_region) and a migration picker.
   Survey lines run parallel to the trench (survey_map.png), so a line should show the trench's
   utilities at the drawn distances and depths. A line's start offset and walking direction are
   not recorded, so the best shift (±3 m) and direction are searched; a utility counts as found if
   an apex lies within 0.3 m along the line and 0.15 m (or 15%) in depth. That search finds
   matches in any clutter, so the control is the same line's picks at the same depths with their
   positions scrambled: only recall above it shows the picks are where the utilities are.
3. **Utility recall on the map** (survey/mapping): each line placed by its GNSS track, the picker's
   top picks put on the survey's common axis and kept only when they line up on 3+ lines, as an
   interpreter traces a pipe from line to line. One trench position and direction per survey is
   searched, not one per line, so chance matches drop sharply. Same scrambled-position control,
   plus how many surveys beat it and a sign test across surveys.

All times are measured from the strongest early echo (time zero: the direct wave and ground
surface overlap for an air-launched antenna, about ±0.5 ns), depths with the reported permittivity.

Usage: .venv/bin/python scripts/validate_on_twente.py [Dataset/external/twente_utilities]
"""

from __future__ import annotations

import csv
import itertools
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detect.classical import find_candidate_boxes
from parsers.segy import read_segy
from studio.processing import migrate, remove_background
from studio.velocity import fit_region, semblance_curve, stacked_permittivity
from survey.mapping import along_axis, place_line, survey_axis, traced_across_lines

C = 0.2998  # m/ns
MIN_R2 = 0.85  # detector apexes, as the Studio accepts them
VELOCITY_MIN_R2 = 0.95  # fits that measure a survey's velocity
ARCHES_PER_LINE = 8
NEUTRAL_EPS = 12.0  # migration velocity for proposing hyperbolas; mid-range of the dataset
MIGRATION_PEAK_K = 5.0  # a peak must exceed this many times the envelope's median
DEPTH_TOLERANCE_M = 0.15
POSITION_TOLERANCE_M = 0.30
MAX_SHIFT_M = 3.0
CONTROL_SEEDS = 5
MAPPED_PICKS_PER_LINE = 5
MAPPED_MIN_LINES = 3
MAPPED_CONTROL_SEEDS = 10


# --- ground truth ---------------------------------------------------------------------------------

def _ocr(cell: np.ndarray) -> str:
    big = cv2.copyMakeBorder(cv2.resize(cell, None, fx=6, fy=6, interpolation=cv2.INTER_CUBIC),
                             20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "cell.png")
        cv2.imwrite(path, big)
        args = ["tesseract", path, "-", "--psm", "7", "-c", "tessedit_char_whitelist=0123456789."]
        return subprocess.run(args, capture_output=True, text=True, check=False).stdout.strip()


def _longest_run(row: np.ndarray) -> tuple[int, int]:
    best = cur = start = best_start = 0
    for x, ink in enumerate(row):
        if ink:
            if cur == 0:
                start = x
            cur += 1
            if cur > best:
                best, best_start = cur, start
        else:
            cur = 0
    return best, best_start


def _group(values: list[int]) -> list[int]:
    groups: list[list[int]] = []
    for v in values:
        if groups and v - groups[-1][-1] <= 2:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [int(np.mean(g)) for g in groups]


def trench_table(path: Path) -> list[dict[str, object]]:
    """Distance along the trench and depth of each utility, read from the drawing's table.

    The table's rules are the longest black runs (the trench photographs beside it have none that
    long). A cell that does not read as a number in 0-5 m is left out, never guessed.
    """
    image = cv2.imread(str(path))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    black = gray < 128
    runs = [_longest_run(r) for r in black]
    width = max(b for b, _ in runs)
    rules = _group([r for r, (b, _) in enumerate(runs) if b >= 0.9 * width])
    if len(rules) < 4:
        return []
    top, bottom = rules[-4], rules[-1]
    x_end = next(s for b, s in runs if b == width) + width
    columns = _group([x for x in range(x_end) if black[top:bottom, x].sum() > 0.8 * (bottom - top)])
    table = []
    for a, b in itertools.pairwise(rules[-4:-1]):  # the distance and depth rows
        table.append([_ocr(gray[a + 2:b - 1, x0 + 2:x1 - 1]) for x0, x1 in itertools.pairwise(columns)])
    utilities = [{"distance_m": _number(d), "depth_m": _number(z)}
                 for d, z in list(zip(*table, strict=True))[2:]]  # skip the header and "start trench"
    return [u for u in utilities if u["distance_m"] is not None and u["depth_m"] is not None]


def _number(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if 0 < value < 5 else None


def metadata(root: Path) -> dict[str, dict[str, str]]:
    with (root / "_Metadata.csv").open(encoding="utf-8-sig") as handle:
        return {row["LocationID"]: row for row in csv.DictReader(handle, delimiter=";")}


# --- radar ----------------------------------------------------------------------------------------

def _envelope(data: np.ndarray) -> np.ndarray:
    """Instantaneous amplitude along time (axis 0), via the analytic signal."""
    n = data.shape[0]
    weights = np.zeros(n)
    weights[0] = 1
    weights[1:(n + 1) // 2] = 2
    if n % 2 == 0:
        weights[n // 2] = 1
    return np.abs(np.fft.ifft(np.fft.fft(data, axis=0) * weights[:, None], axis=0))


def migration_peaks(traces: np.ndarray, dt: float, spacing: float, surface: int) -> list[tuple[int, int, float]]:
    """(trace, sample, strength) of focused points after migrating at the neutral velocity."""
    data = remove_background(traces.T[surface:], "mean", 0)
    migrated = migrate(data, trace_spacing_m=spacing, sample_interval_ns=dt,
                       velocity_m_per_ns=C / np.sqrt(NEUTRAL_EPS), aperture_traces=int(1.5 / spacing))
    env = (_envelope(migrated) * (np.arange(migrated.shape[0])[:, None] + 1)).astype(np.float32)  # spreading
    env[: int(2.0 / dt)] = 0  # antenna ringing
    env[int(0.85 * len(env)):] = 0  # migration edge, where the gain would also blow up
    local_max = cv2.dilate(env, np.ones((int(4 / dt), int(0.5 / spacing)), np.uint8))
    rows, cols = np.nonzero((env == local_max) & (env > MIGRATION_PEAK_K * np.median(env)))
    return sorted(((int(c), int(r) + surface, float(env[r, c])) for r, c in zip(rows, cols, strict=True)),
                  key=lambda p: -p[2])


def analyse_line(path: Path) -> dict[str, object]:
    """Apexes by both methods, and per-arch velocity measurements, all timed from time zero."""
    line = read_segy(path)
    traces, dt = line.traces, line.sample_interval_ns
    spacing = line.trace_spacing_m or 0.02
    surface = int(np.argmax(np.abs(traces.mean(axis=0)[: int(10 / dt)])))
    # Wheel distance per trace: the encoder's own record, which can drift from index * spacing.
    distances = line.distances_m if line.distances_m is not None else np.arange(len(traces)) * spacing
    index = np.arange(len(traces))

    detector = []
    for x, y, w, h in find_candidate_boxes(traces, dt):
        if w < 3 or h < 3:
            continue
        fit = fit_region(traces, trace_start=x, sample_start=y, trace_span=w, sample_span=h,
                         trace_spacing_m=spacing, sample_interval_ns=dt, time_zero_sample=surface)
        if fit is not None and fit.physically_plausible and fit.r2 >= MIN_R2 and fit.apex_time_ns > 0:
            detector.append({"x_m": float(np.interp(fit.apex_trace, index, distances)), "t_ns": fit.apex_time_ns})

    peaks = migration_peaks(traces, dt, spacing, surface)
    picker = [{"x_m": float(distances[x]), "t_ns": (s - surface) * dt} for x, s, _ in peaks]

    data = remove_background(traces.T[surface:], "mean", 0)
    half = int(0.8 / spacing)
    fitted_eps, curves = [], []
    for x, s, _ in peaks[:ARCHES_PER_LINE]:
        curves.append(semblance_curve(data, apex_trace=x, apex_sample=s - surface,
                                      trace_spacing_m=spacing, sample_interval_ns=dt))
        start = max(0, x - half)
        fit = fit_region(traces, trace_start=start, sample_start=max(surface + 1, s - 10),
                         trace_span=min(2 * half, len(traces) - start), sample_span=100,
                         trace_spacing_m=spacing, sample_interval_ns=dt, time_zero_sample=surface)
        if fit is not None and fit.physically_plausible and fit.r2 >= VELOCITY_MIN_R2 \
                and fit.apex_time_ns > 0 and abs(fit.apex_trace - x) <= 5:
            fitted_eps.append(fit.dielectric)
    placement = place_line(line.positions_m, distances) if line.positions_m is not None else None
    return {"length_m": float(distances[-1]), "detector": detector, "picker": picker,
            "fitted_eps": fitted_eps, "curves": curves, "placement": placement}


# --- scoring --------------------------------------------------------------------------------------

def _matches(truth: list[dict], length: float, apexes: list[dict], shift: float, flip: bool) -> int:
    """Utilities with an unused apex within tolerance, each taking its nearest."""
    used: set[int] = set()
    for u in truth:
        best = None
        for i, a in enumerate(apexes):
            dx = abs((length - a["x_m"] if flip else a["x_m"]) + shift - u["distance_m"])
            dz = abs(a["depth_m"] - u["depth_m"])
            if i in used or dx > POSITION_TOLERANCE_M or dz > max(DEPTH_TOLERANCE_M, 0.15 * u["depth_m"]):
                continue
            if best is None or dx + dz < best[0]:
                best = (dx + dz, i)
        if best is not None:
            used.add(best[1])
    return len(used)


def recall(truth: list[dict], length: float, apexes: list[dict]) -> float:
    """Best fraction of the utilities found over every start offset and walking direction."""
    shifts = np.arange(-MAX_SHIFT_M, MAX_SHIFT_M + 1e-9, 0.05)
    found = max(_matches(truth, length, apexes, float(s), flip) for flip in (False, True) for s in shifts)
    return found / len(truth)


def _with_depth(apexes: list[dict], eps: float) -> list[dict]:
    velocity = C / np.sqrt(eps)
    return [{"x_m": a["x_m"], "depth_m": velocity * a["t_ns"] / 2} for a in apexes]


def _ranks(values: list[float]) -> np.ndarray:
    return np.argsort(np.argsort(values)).astype(float)


def _scrambled(apexes: list[dict], length: float, seed: int) -> list[dict]:
    """The same picks at the same depths, at random positions along the line."""
    xs = np.random.default_rng(seed).uniform(0, length, len(apexes))
    return [{"x_m": float(x), "depth_m": a["depth_m"]} for x, a in zip(xs, apexes, strict=True)]


def _velocity_scores(pairs: list[tuple[float, float]], guess: float) -> dict[str, object]:
    reported, measured = zip(*pairs, strict=True)
    error = [abs(m / r - 1) for r, m in pairs]
    return {"surveys_measured": len(pairs),
            "median_abs_error_pct": round(100 * statistics.median(error), 1),
            "within_25pct": sum(e <= 0.25 for e in error),
            "rank_correlation": round(float(np.corrcoef(_ranks(list(reported)), _ranks(list(measured)))[0, 1]), 2),
            "baseline_median_abs_error_pct": round(100 * statistics.median(abs(guess / r - 1) for r in reported), 1),
            "baseline_within_25pct": sum(abs(guess / r - 1) <= 0.25 for r in reported)}


def _mapped_found(truth: list[dict], lines: list[tuple[float, list[float], list[float]]], axis_lines: list) -> int:
    """Utility-line matches for the best single trench position and direction on the survey axis."""
    along = [[float(a) for a in along_axis(pl, np.asarray(xs), axis_lines[1])] if xs else []
             for pl, (_, xs, _) in zip(axis_lines[0], lines, strict=True)]
    points = [list(zip(a, depths, strict=True)) for a, (_, _, depths) in zip(along, lines, strict=True)]
    keep = traced_across_lines(points, min_lines=MAPPED_MIN_LINES)
    points = [[p for p, k in zip(row, ks, strict=True) if k] for row, ks in zip(points, keep, strict=True)]
    flat = [a for row in points for a, _ in row]
    if not flat:
        return 0
    best = 0
    for sign in (1.0, -1.0):
        for origin in np.arange(min(flat) - 4.0, max(flat) + 4.0, 0.05):
            best = max(best, sum(_count(truth, row, float(origin), sign) for row in points))
    return best


def _count(truth: list[dict], points: list[tuple[float, float]], origin: float, sign: float) -> int:
    """Utilities with an unused point within tolerance of origin + sign * trench distance."""
    used: set[int] = set()
    for u in truth:
        best = None
        for i, (along, depth) in enumerate(points):
            dx, dz = abs(along - (origin + sign * u["distance_m"])), abs(depth - u["depth_m"])
            if i in used or dx > POSITION_TOLERANCE_M or dz > max(DEPTH_TOLERANCE_M, 0.15 * u["depth_m"]):
                continue
            if best is None or dx + dz < best[0]:
                best = (dx + dz, i)
        if best is not None:
            used.add(best[1])
    return len(used)


def _mapped_detection(surveys: list[dict]) -> dict[str, object] | None:
    """Picker picks on the GNSS map, traced across lines, against the same picks scrambled."""
    found = scrambled = total = better = worse = 0
    scored = 0
    for s in surveys:
        placed = [line for line in s["lines"] if line["placement"] is not None]
        if not s["truth"] or len(placed) < 2:
            continue
        axis = survey_axis([line["placement"] for line in placed])
        velocity = C / np.sqrt(s["eps"])
        top = [line["picker"][:MAPPED_PICKS_PER_LINE] for line in placed]
        lines = [(line["length_m"], [p["x_m"] for p in picks], [velocity * p["t_ns"] / 2 for p in picks])
                 for line, picks in zip(placed, top, strict=True)]
        frame = ([line["placement"] for line in placed], axis)
        own = _mapped_found(s["truth"], lines, frame)
        rng = np.random.default_rng(0)
        control = statistics.mean(
            _mapped_found(s["truth"], [(length, list(rng.uniform(0, length, len(xs))), depths)
                                       for length, xs, depths in lines], frame)
            for _ in range(MAPPED_CONTROL_SEEDS))
        found, scrambled, total = found + own, scrambled + control, total + len(s["truth"]) * len(placed)
        better, worse, scored = better + (own > control), worse + (own < control), scored + 1
    if not scored:
        return None
    n = better + worse
    sign_p = 2 * sum(math.comb(n, k) for k in range(min(better, worse) + 1)) / 2**n if n else 1.0
    return {"surveys": scored, "found_per_line": round(found / total, 3),
            "positions_scrambled": round(scrambled / total, 3),
            "surveys_better": better, "surveys_worse": worse, "sign_test_p": round(min(sign_p, 1.0), 4)}


def summarise(surveys: list[dict]) -> dict[str, object]:
    with_truth = [s for s in surveys if s["truth"]]
    result: dict[str, object] = {"surveys": len(surveys), "lines": sum(len(s["lines"]) for s in surveys),
                                 "surveys_with_trench_truth": len(with_truth)}
    for method in ("detector", "picker"):
        own, control = [], []
        for s in with_truth:
            for line in s["lines"]:
                apexes = _with_depth(line[method], s["eps"])
                own.append(recall(s["truth"], line["length_m"], apexes))
                control.append(statistics.mean(recall(s["truth"], line["length_m"], _scrambled(apexes, line["length_m"], k))
                                               for k in range(CONTROL_SEEDS)))
        result[f"recall_{method}"] = {"per_line": round(statistics.mean(own), 3),
                                      "positions_scrambled": round(statistics.mean(control), 3)}

    result["recall_picker_mapped_traced"] = _mapped_detection(with_truth)

    guess = statistics.median(s["eps"] for s in surveys)  # in-sample: flatters the baseline, if anything
    fits = [(s["eps"], statistics.median(e)) for s in surveys
            if len(e := [x for line in s["lines"] for x in line["fitted_eps"]]) >= 3]
    stacked = [(s["eps"], e) for s in surveys
               if (e := stacked_permittivity([c for line in s["lines"] for c in line["curves"]])) is not None]
    result["velocity_hyperbola_fit"] = _velocity_scores(fits, guess) if fits else None
    result["velocity_semblance_stacked"] = _velocity_scores(stacked, guess) if stacked else None
    result["semblance_pairs"] = [[round(r, 2), round(m, 2)] for r, m in stacked]  # (reported, measured), for bias checks
    return result


def score(root: Path) -> dict[str, object]:
    meta = metadata(root)
    surveys, jobs, owners = {}, [], []
    for folder in sorted(p for p in root.glob("*/*") if (p / "Radargrams").is_dir()):
        sid = folder.name
        if sid not in meta or not meta[sid]["Ground relative permittivity"]:
            continue
        truth = trench_table(folder / "ground-truth.png") if (folder / "ground-truth.png").exists() else []
        surveys[sid] = {"id": sid, "eps": float(meta[sid]["Ground relative permittivity"]), "truth": truth, "lines": []}
        for sgy in sorted((folder / "Radargrams").glob("*.sgy")):
            jobs.append(sgy)
            owners.append(sid)
    print(f"{len(surveys)} surveys, {len(jobs)} lines", file=sys.stderr, flush=True)
    with Pool(max(1, (os.cpu_count() or 2) // 2)) as pool:
        for sid, line in zip(owners, pool.imap(_safe_analyse, jobs, chunksize=4), strict=True):
            if line is not None:
                surveys[sid]["lines"].append(line)
    return summarise([s for s in surveys.values() if s["lines"]])


def _safe_analyse(path: Path) -> dict[str, object] | None:
    try:
        return analyse_line(path)
    except ValueError as exc:  # an unreadable file is reported and left out, never guessed
        print(f"skipped {path}: {exc}", file=sys.stderr, flush=True)
        return None


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("Dataset/external/twente_utilities")
    print(json.dumps(score(root), indent=2))
