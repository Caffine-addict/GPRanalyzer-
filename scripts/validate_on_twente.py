"""Score the Studio's analysis against professionally surveyed, excavated sites (Twente, CC0).

The dataset (docs/EXTERNAL_DATA.md): 125 utility surveys at 13 Dutch construction sites, 500 MHz
air-launched GPR. Each survey has a trial trench drawing (distance and depth of every utility
found) and the soil's relative permittivity, as the surveyors calibrated it.

Three things are scored, each against a baseline that says what chance alone would give:

1. **Velocity** (studio/velocity.fit_region, unchanged). Hyperbolas are proposed by migrating at
   one fixed neutral velocity, so the survey's own permittivity never leaks in; the fitter then
   measures each one. A survey's median fitted permittivity is compared with the reported one,
   against the baseline of guessing the dataset's median for every survey.
2. **Utility recall, detector** (detect/classical.find_candidate_boxes + fit_region, unchanged).
3. **Utility recall, migration picker**: peaks of the migrated envelope, the textbook method.

For (2) and (3): survey lines run parallel to the trench (survey_map.png), so a line should show
the trench's utilities at the drawn distances and depths. A line's start offset and walking
direction are not recorded, so the best shift (±3 m) and direction are searched; a utility counts
as found if an apex lies within 0.3 m along the line and 0.15 m (or 15%) in depth. The same search
against other surveys' utility patterns is the chance control — the search finds some matches in
any clutter, and only recall above the control means anything.

Depth is timed from the strongest early echo (direct wave and ground surface overlap for an
air-launched antenna: about ±0.5 ns, ±2.5 cm) and converted with the reported permittivity.

Usage: .venv/bin/python scripts/validate_on_twente.py [Dataset/external/twente_utilities]
"""

from __future__ import annotations

import csv
import itertools
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detect.classical import find_candidate_boxes
from parsers.segy import read_segy
from studio.processing import migrate, remove_background
from studio.velocity import fit_region

C = 0.2998  # m/ns
MIN_R2 = 0.85  # detector apexes, as the Studio accepts them
VELOCITY_MIN_R2 = 0.95  # fits that measure a survey's velocity
NEUTRAL_EPS = 12.0  # migration velocity for proposing hyperbolas; mid-range of the dataset
MIGRATION_PEAK_K = 5.0  # a peak must exceed this many times the envelope's median
FITS_PER_LINE = 5
DEPTH_TOLERANCE_M = 0.15
POSITION_TOLERANCE_M = 0.30
MAX_SHIFT_M = 3.0
CONTROL_PATTERNS = 3


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
    """Apex times (from the surface echo) and positions by both methods, and velocity fits."""
    line = read_segy(path)
    traces, dt = line.traces, line.sample_interval_ns
    spacing = line.trace_spacing_m or 0.02
    surface = int(np.argmax(np.abs(traces.mean(axis=0)[: int(10 / dt)])))

    detector = []
    for x, y, w, h in find_candidate_boxes(traces, dt):
        if w < 3 or h < 3:
            continue
        fit = fit_region(traces, trace_start=x, sample_start=y, trace_span=w, sample_span=h,
                         trace_spacing_m=spacing, sample_interval_ns=dt)
        if fit is not None and fit.physically_plausible and fit.r2 >= MIN_R2 and fit.apex_sample > surface:
            detector.append({"x_m": fit.apex_trace * spacing, "t_ns": (fit.apex_sample - surface) * dt})

    peaks = migration_peaks(traces, dt, spacing, surface)
    picker = [{"x_m": x * spacing, "t_ns": (s - surface) * dt} for x, s, _ in peaks]

    half = int(0.8 / spacing)
    fitted_eps = []
    for x, s, _ in peaks[:FITS_PER_LINE]:
        start = max(0, x - half)
        fit = fit_region(traces, trace_start=start, sample_start=max(surface, s - 10),
                         trace_span=min(2 * half, len(traces) - start), sample_span=100,
                         trace_spacing_m=spacing, sample_interval_ns=dt)
        if fit is not None and fit.physically_plausible and fit.r2 >= VELOCITY_MIN_R2 and abs(fit.apex_trace - x) <= 5:
            fitted_eps.append(fit.dielectric)
    return {"length_m": (len(traces) - 1) * spacing, "detector": detector, "picker": picker, "fitted_eps": fitted_eps}


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


def summarise(surveys: list[dict]) -> dict[str, object]:
    result: dict[str, object] = {"surveys": len(surveys), "lines": sum(len(s["lines"]) for s in surveys)}
    for method in ("detector", "picker"):
        own, control = [], []
        for i, s in enumerate(surveys):
            others = [surveys[(i + k) % len(surveys)] for k in range(1, CONTROL_PATTERNS + 1)]
            for line in s["lines"]:
                apexes = _with_depth(line[method], s["eps"])
                own.append(recall(s["truth"], line["length_m"], apexes))
                control.extend(recall(o["truth"], line["length_m"], apexes) for o in others if o is not s)
        result[f"recall_{method}"] = {"per_line": round(statistics.mean(own), 3),
                                      "chance_control": round(statistics.mean(control), 3)}

    measured = [(s["eps"], statistics.median(e)) for s in surveys
                if len(e := [x for line in s["lines"] for x in line["fitted_eps"]]) >= 3]
    if measured:
        reported, fitted = zip(*measured, strict=True)
        guess = statistics.median(reported)
        error = [abs(f / r - 1) for r, f in measured]
        baseline = [abs(guess / r - 1) for r in reported]
        result["velocity"] = {
            "surveys_with_3plus_fits": len(measured),
            "median_abs_error_pct": round(100 * statistics.median(error), 1),
            "baseline_median_abs_error_pct": round(100 * statistics.median(baseline), 1),
            "within_25pct": sum(e <= 0.25 for e in error),
            "baseline_within_25pct": sum(e <= 0.25 for e in baseline),
            "rank_correlation": round(float(np.corrcoef(_ranks(list(reported)), _ranks(list(fitted)))[0, 1]), 2),
        }
    return result


def score(root: Path) -> dict[str, object]:
    meta = metadata(root)
    surveys = []
    for folder in sorted(p for p in root.glob("*/*") if (p / "ground-truth.png").exists()):
        sid = folder.name
        if sid not in meta or not meta[sid]["Ground relative permittivity"]:
            continue
        truth = trench_table(folder / "ground-truth.png")
        if not truth:
            continue
        lines = [analyse_line(sgy) for sgy in sorted((folder / "Radargrams").glob("*.sgy"))]
        surveys.append({"id": sid, "eps": float(meta[sid]["Ground relative permittivity"]),
                        "truth": truth, "lines": lines})
        print(f"{sid}: {len(truth)} utilities, {len(lines)} lines", file=sys.stderr, flush=True)
    return summarise(surveys)


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("Dataset/external/twente_utilities")
    print(json.dumps(score(root), indent=2))
