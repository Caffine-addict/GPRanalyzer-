"""Utility runs: targets on neighbouring lines linked into one buried pipe or cable, for review.

Deterministic, no model of any kind. Built on the 3D scene (studio/site3d.py), so a run uses
exactly the positions and depths the 3D view draws:

1. **Lines.** Only lines placed by surveyed points or a GPS track. A line whose GPS froze has no
   direction, so where a target lies on the ground is unknown; it is left out, and listed.
2. **Pairs.** Lines are grouped by direction (FAMILY_ANGLE_DEG) and ordered across the site. On
   each pair of neighbours, every target pair is scored by plan gap and depth difference, and
   kept only if: gap <= RUN_MAX_GAP_M; depth difference within twice the combined depth error,
   which comes from each line's velocity basis (fitted 5%, assumed or site-measured 15%); classes
   are compatible; echo polarity does not disagree.
3. **Assignment.** One-to-one on each neighbour pair, minimum total score (scipy's Hungarian).
4. **Runs.** Chains crossing RUN_MIN_LINES lines, within RUN_DEPTH_RANGE_M of depth, straight in
   plan by a RANSAC line fit (inliers within RUN_STRAIGHTNESS_M), crossing the lines at
   RUN_MIN_CROSSING_DEG or more.
5. **Chance.** The same targets at random chainages on their own lines, CHANCE_DRAWS times: if
   runs are no rarer at random, every run says so in its evidence.

Every run is a PROPOSED claim (annotations/_sites/<site>/runs.json) until a signed-in person
confirms or rejects it; nothing is confirmed automatically. Its id is a hash of its targets, so
the same run found again is the same claim.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from core.annotation_io import job_lock, safe_job_dir, write_text_atomically
from core.geo import from_local
from evidence.quality import _CEILING, _WHY_UNGRADED
from studio.site_map import (
    DEPTH_FLOOR_ERROR_M,
    DEPTH_RELATIVE_ERROR,
    FAMILY_ANGLE_DEG,
    RUN_DEPTH_RANGE_M,
    RUN_MAX_GAP_M,
    RUN_MIN_CROSSING_DEG,
    RUN_MIN_LINES,
    RUN_STRAIGHTNESS_M,
    contrast_from_votes,
)

_SITES_ROOT = Path("annotations") / "_sites"
FITTED_RELATIVE_ERROR = 0.05  # a velocity from the target's own hyperbola (site_map uses the same)
DEPTH_TOLERANCE_SIGMAS = 2.0
CHANCE_DRAWS = 30
CHANCE_P_MAX = 0.05
_INVALID = 1e6
_DECISIONS = ("confirmed", "rejected")
# Classes a crossing pipe or cable can show as on a line. An elongated target runs *along* the
# line, a disturbed or cluttered zone is not one object: neither can be one point of a crossing run.
_LINKABLE = {None, "clear_point_reflector", "low_snr_point_reflector", "strong_high_contrast_reflector",
             "multiple_point_reflectors", "intersecting_linear_and_point_reflector", "cavities"}


# ---------------------------------------------------------------- targets


def _depth_error(depth: float, velocity_source: str) -> float:
    relative = FITTED_RELATIVE_ERROR if velocity_source == "fitted" else DEPTH_RELATIVE_ERROR
    return relative * depth + DEPTH_FLOOR_ERROR_M


def _class_at(line: dict[str, Any], trace: float, record_ns: float) -> str | None:
    """The suggested class of a detector box on this line holding the target, if one does.

    ponytail: matched by trace span and record time with no per-channel time-zero shift; a box
    on another channel can be a few ns off. Good enough to veto a clearly linear or disturbed box.
    """
    for c in line["targets"]:
        if (c["kind"] == "candidate" and c["class"] and abs(c["trace"] - trace) <= 12
                and abs(c["sample"] * c["dt_ns"] - record_ns) <= 3.0):
            return str(c["class"])
    return None


def _along(line: dict[str, Any], chainage: float) -> tuple[float, float]:
    path = line["path"]
    for (c0, e0, n0), (c1, e1, n1) in itertools.pairwise(path):
        if chainage <= c1 or (c1, e1, n1) == tuple(path[-1]):
            f = 0.0 if c1 == c0 else min(1.0, max(0.0, (chainage - c0) / (c1 - c0)))
            return e0 + f * (e1 - e0), n0 + f * (n1 - n0)
    return path[0][1], path[0][2]


def link_targets(line: dict[str, Any]) -> list[dict[str, Any]]:
    """The line's targets that can join a run: the site picker's and people's picks, at depth below ground."""
    v = line["velocity"]["value"]
    if not v:
        return []
    out = []
    for t in line["targets"]:
        if t["kind"] == "detected":
            time_ns = t["time_ns"]  # from the picker's own time zero: depth below ground
        elif t["kind"] == "pick" and t["channel"] == line["channel"]:  # the velocity and time zero are this channel's
            time_ns = t["sample"] * t["dt_ns"] - line["time_zero_ns"]
        else:
            continue
        if time_ns <= 0:
            continue
        depth = v * time_ns / 2
        east, north = _along(line, t["chainage_m"])
        out.append({"id": t["id"], "job": line["job"], "kind": t["kind"], "chainage_m": t["chainage_m"],
                    "east": east, "north": north, "depth_m": depth,
                    "depth_error_m": _depth_error(depth, line["velocity"]["source"]),
                    "contrast": t.get("contrast", "unreadable"),
                    "class": _class_at(line, t["trace"], time_ns + line["time_zero_ns"])})
    return out


# ---------------------------------------------------------------- lines


def _direction(line: dict[str, Any]) -> np.ndarray:
    (_, e0, n0), (_, e1, n1) = line["path"][0], line["path"][-1]
    d = np.array([e1 - e0, n1 - n0], dtype=float)
    return d / max(float(np.linalg.norm(d)), 1e-9)


def families(lines: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Lines grouped by direction (either way along), each ordered across the site."""
    groups: list[list[dict[str, Any]]] = []
    cos_limit = math.cos(math.radians(FAMILY_ANGLE_DEG))
    for line in lines:
        home = next((g for g in groups if abs(float(_direction(g[0]) @ _direction(line))) >= cos_limit), None)
        if home is None:
            groups.append([line])
        else:
            home.append(line)
    ordered = []
    for group in groups:
        mean = _family_direction(group)
        across = np.array([-mean[1], mean[0]])
        ordered.append(sorted(group, key=lambda ln: float(np.mean([[p[1], p[2]] for p in ln["path"]], axis=0) @ across)))
    return ordered


def _family_direction(group: Sequence[dict[str, Any]]) -> np.ndarray:
    ref = _direction(group[0])
    mean = np.mean([d if d @ ref >= 0 else -d for d in map(_direction, group)], axis=0)
    return mean / max(float(np.linalg.norm(mean)), 1e-9)


# ---------------------------------------------------------------- linking


def _compatible(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a["class"] not in _LINKABLE or b["class"] not in _LINKABLE:
        return False
    return {a["contrast"], b["contrast"]} != {"denser", "lighter"}  # polarity may be unreadable, never opposite


def _pair_cost(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, dict[str, float]] | None:
    gap = math.hypot(a["east"] - b["east"], a["north"] - b["north"])
    delta = abs(a["depth_m"] - b["depth_m"])
    tolerance = DEPTH_TOLERANCE_SIGMAS * math.hypot(a["depth_error_m"], b["depth_error_m"])
    if gap > RUN_MAX_GAP_M or delta > tolerance or not _compatible(a, b):
        return None
    cost = gap / RUN_MAX_GAP_M + delta / tolerance
    return cost, {"gap_m": round(gap, 3), "depth_delta_m": round(delta, 3), "tolerance_m": round(tolerance, 3),
                  "score": round(1 - cost / 2, 3)}


def _assign(here: list[dict[str, Any]], there: list[dict[str, Any]]) -> list[tuple[int, int, dict[str, float]]]:
    """Minimum-total-cost one-to-one matching between two neighbouring lines' targets."""
    if not here or not there:
        return []
    costs = np.full((len(here), len(there)), _INVALID)
    detail: dict[tuple[int, int], dict[str, float]] = {}
    for (i, a), (j, b) in itertools.product(enumerate(here), enumerate(there)):
        found = _pair_cost(a, b)
        if found:
            costs[i, j], detail[(i, j)] = found
    rows, cols = linear_sum_assignment(costs)
    return [(int(i), int(j), detail[(int(i), int(j))]) for i, j in zip(rows, cols, strict=True) if (i, j) in detail]


def _ransac(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, float] | None:
    """(inlier mask, unit direction, rms of inliers) of the straight line most points lie on."""
    best: tuple[int, float, np.ndarray] | None = None
    for i, j in itertools.combinations(range(len(points)), 2):
        d = points[j] - points[i]
        if np.linalg.norm(d) < 1e-6:
            continue
        normal = np.array([-d[1], d[0]]) / np.linalg.norm(d)
        off = np.abs((points - points[i]) @ normal)
        mask = off <= RUN_STRAIGHTNESS_M
        rms = float(np.sqrt(np.mean(off[mask] ** 2)))
        if best is None or (mask.sum(), -rms) > (best[0], -best[1]):
            best = (int(mask.sum()), rms, mask)
    if best is None:
        return None
    mask = best[2]
    inliers = points[mask]
    centred = inliers - inliers.mean(axis=0)
    direction = np.linalg.svd(centred, full_matrices=False)[2][0]  # least squares through the inliers
    normal = np.array([-direction[1], direction[0]])
    return mask, direction, float(np.sqrt(np.mean((centred @ normal) ** 2)))


def _chains(family: Sequence[dict[str, Any]], targets: dict[str, list[dict[str, Any]]]) -> list[list[tuple[dict, dict | None]]]:
    """Linked chains as [(target, link to the next or None)], from neighbour-by-neighbour assignment."""
    successor: dict[str, tuple[dict[str, Any], dict[str, float]]] = {}
    has_predecessor: set[str] = set()
    for here, there in itertools.pairwise(family):
        a, b = targets[here["job"]], targets[there["job"]]
        for i, j, detail in _assign(a, b):
            successor[a[i]["id"]] = (b[j], detail)
            has_predecessor.add(b[j]["id"])
    chains = []
    for start in (t for line in family for t in targets[line["job"]]):
        if start["id"] in has_predecessor or start["id"] not in successor:
            continue
        chain: list[tuple[dict, dict | None]] = []
        current: dict[str, Any] | None = start
        while current is not None:
            step = successor.get(current["id"])
            chain.append((current, None if step is None else {"to": step[0]["id"], **step[1]}))
            current = None if step is None else step[0]
        chains.append(chain)
    return chains


def _run(chain: list[tuple[dict, dict | None]], family_direction: np.ndarray) -> dict[str, Any] | None:
    members = [t for t, _ in chain]
    fit = _ransac(np.array([[t["east"], t["north"]] for t in members]))
    if fit is None:
        return None
    mask, direction, rms = fit
    kept = [t for t, keep in zip(members, mask, strict=True) if keep]
    depths = [t["depth_m"] for t in kept]
    crossing = math.degrees(math.acos(min(1.0, abs(float(direction @ family_direction)))))
    if (len({t["job"] for t in kept}) < RUN_MIN_LINES or max(depths) - min(depths) > RUN_DEPTH_RANGE_M
            or crossing < RUN_MIN_CROSSING_DEG):
        return None
    kept_ids = {t["id"] for t in kept}
    links = [{"from": t["id"], **link} for t, link in chain if link and t["id"] in kept_ids and link["to"] in kept_ids]
    if len(links) < RUN_MIN_LINES - 1:
        return None  # the outlier RANSAC dropped broke the chain
    ids = sorted(kept_ids)
    straightness = max(0.0, 1 - rms / RUN_STRAIGHTNESS_M)
    return {
        "id": "run-" + hashlib.sha1("|".join(ids).encode()).hexdigest()[:10],
        "targets": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in t.items()} for t in kept],
        "links": links,
        "dropped_by_fit": [t["id"] for t, keep in zip(members, mask, strict=True) if not keep],
        "lines": len({t["job"] for t in kept}),
        "depth_m": round(float(np.median(depths)), 3),
        "depth_range_m": [round(min(depths), 3), round(max(depths), 3)],
        "fit": {"bearing_deg": round(math.degrees(math.atan2(direction[0], direction[1])) % 180, 1),
                "rms_m": round(rms, 3), "crossing_deg": round(crossing, 1)},
        "contrast": contrast_from_votes([None if t["contrast"] == "unreadable" else t["contrast"] == "lighter" for t in kept]),
        "confidence": round(float(np.mean([link["score"] for link in links])) * straightness, 3),
    }


def find_runs(lines: Sequence[dict[str, Any]], targets: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    runs = []
    for family in families(lines):
        direction = _family_direction(family)
        for chain in _chains(family, targets):
            run = _run(chain, direction)
            if run:
                runs.append(run)
    return runs


def _chance(lines: Sequence[dict[str, Any]], targets: dict[str, list[dict[str, Any]]], found: int) -> dict[str, Any]:
    """How many runs the same targets make at random chainages on their own lines."""
    rng = np.random.default_rng(0)
    by_job = {line["job"]: line for line in lines}
    counts = []
    for _ in range(CHANCE_DRAWS):
        moved: dict[str, list[dict[str, Any]]] = {}
        for job, items in targets.items():
            lo, hi = by_job[job]["covered_m"]
            moved[job] = []
            for t in items:
                chainage = float(rng.uniform(lo, hi))
                east, north = _along(by_job[job], chainage)
                moved[job].append({**t, "chainage_m": chainage, "east": east, "north": north})
        counts.append(len(find_runs(lines, moved)))
    p = sum(c >= found for c in counts) / CHANCE_DRAWS if found else 1.0
    return {"runs": found, "chance_mean": round(float(np.mean(counts)), 2), "p_chance": round(p, 3),
            "beats_chance": bool(found and p <= CHANCE_P_MAX),
            "verdict": "runs beat chance on this site" if found and p <= CHANCE_P_MAX
            else "unconfirmed: runs here are no more common than at random positions"}


def site_runs(scene: dict[str, Any]) -> dict[str, Any]:
    """Runs on a site's scene (site3d.scene, with the picker's targets), plus what was left out and why."""
    lines = [line for line in scene["lines"] if line["velocity"]["value"] and len(line["path"]) >= 2]
    skipped = [{"job": line["job"], "reason": "no velocity" if not line["velocity"]["value"] else "too short to place"}
               for line in scene["lines"] if line not in lines]
    targets = {line["job"]: link_targets(line) for line in lines}
    runs = find_runs(lines, targets)
    origin = scene["origin"]
    for run in runs:
        sources = {line["job"]: line for line in lines}
        run["position_sources"] = sorted({sources[t["job"]]["source"] for t in run["targets"]})
        run["velocity_sources"] = sorted({sources[t["job"]]["velocity"]["source"] for t in run["targets"]})
        run["lonlat"] = [list(reversed(from_local(t["east"], t["north"], origin["lat"], origin["lon"])))
                         for t in run["targets"]] if origin else []
    return {
        "site": scene["site"], "runs": runs, "chance": _chance(lines, targets, len(runs)),
        "excluded_lines": [{"job": s["job"], "reason": "location only (GPS froze): direction unknown"} for s in scene["located"]]
                          + [{"job": u["job"], "reason": u["reason"]} for u in scene["unplaced"]] + skipped,
    }


# ---------------------------------------------------------------- claims


def _path(site: str) -> Path:
    return safe_job_dir(_SITES_ROOT, site) / "runs.json"


def _load(site: str) -> dict[str, dict[str, Any]]:
    path = _path(site)
    if not path.exists():
        return {}
    try:
        return dict(json.loads(path.read_text())["runs"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"{path} is not a valid run-claims file ({exc}); fix or remove it") from exc


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def claims(site: str, found: dict[str, Any]) -> list[dict[str, Any]]:
    """Every run on the site with its review status.

    A new run is stored as proposed. A still-proposed run's evidence is refreshed from the current
    data (positions or velocities may have changed); a decided run keeps the evidence it was decided
    on, and is marked `found_now: false` if the current data no longer produces it.
    """
    with job_lock(safe_job_dir(_SITES_ROOT, site)):
        stored = _load(site)
        changed = False
        for run in found["runs"]:
            claim = stored.get(run["id"])
            if claim is None:
                stored[run["id"]] = {"status": "proposed", "proposed_at": _now(), "reviewer": "", "note": "",
                                     "decided_at": "", "history": [], "evidence": run, "chance": found["chance"]}
                changed = True
            elif claim["status"] == "proposed" and (claim["evidence"], claim["chance"]) != (run, found["chance"]):
                stored[run["id"]] = {**claim, "evidence": run, "chance": found["chance"]}
                changed = True
        if changed:
            write_text_atomically(_path(site), json.dumps({"runs": stored}, indent=2) + "\n")
    now = {run["id"] for run in found["runs"]}
    return [{**claim["evidence"], "status": claim["status"], "reviewer": claim["reviewer"], "note": claim["note"],
             "decided_at": claim["decided_at"], "proposed_at": claim["proposed_at"], "chance": claim["chance"],
             "found_now": run_id in now} for run_id, claim in stored.items()]


def decide(site: str, run_id: str, status: str, reviewer: str, note: str = "") -> dict[str, Any]:
    if status not in _DECISIONS:
        raise ValueError(f"status must be one of {_DECISIONS}")
    if not reviewer.strip():
        raise ValueError("a decision needs the reviewer's name")
    with job_lock(safe_job_dir(_SITES_ROOT, site)):
        stored = _load(site)
        if run_id not in stored:
            raise KeyError(run_id)
        decision = {"status": status, "reviewer": reviewer.strip(), "note": note.strip()[:500], "decided_at": _now()}
        stored[run_id] = {**stored[run_id], **decision, "history": [*stored[run_id].get("history", []), decision]}
        write_text_atomically(_path(site), json.dumps({"runs": stored}, indent=2) + "\n")
    return {"id": run_id, **stored[run_id]}


QUALITY = {"pas128": "ungraded", "reason": _WHY_UNGRADED, "provisional_ceiling": _CEILING}


def confirmed_geojson(site: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Confirmed runs as 3D lines. Z is metres relative to the ground surface (negative = below)."""
    features = [{
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[round(lon, 8), round(lat, 8), -round(t["depth_m"], 3)]
                                                           for (lon, lat), t in zip(run["lonlat"], run["targets"], strict=True)]},
        "properties": {"kind": "utility_run", "id": run["id"], "site": site, "status": run["status"],
                       "reviewer": run["reviewer"], "decided_at": run["decided_at"], "lines": run["lines"],
                       "depth_m": run["depth_m"], "depth_range_m": run["depth_range_m"], "contrast": run["contrast"],
                       "confidence": run["confidence"], "position_sources": run["position_sources"],
                       "velocity_sources": run["velocity_sources"], "chance": run["chance"]["verdict"],
                       "z": "metres below the ground surface (negative), from each line's measured time zero",
                       "quality_level": QUALITY,
                       "targets": [t["id"] for t in run["targets"]]},
    } for run in runs if run["status"] == "confirmed" and run["lonlat"] and run["found_now"]]
    return {"type": "FeatureCollection", "features": features,
            "metadata": {"site": site, "asce38": "not given: this project has no valid ASCE 38 mapping (evidence/quality.py)",
                         "not_exported": [r["id"] for r in runs if r["status"] == "confirmed" and not r["found_now"]]}}
