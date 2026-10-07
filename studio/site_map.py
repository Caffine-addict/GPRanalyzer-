"""Field mode: every target of a site at its real position, as GeoJSON a crew can take into the field.

A site is the jobs sharing a name prefix (`Set1_LINE001`, `Set1_LINE002`, ... form site `Set1`).
Each line is placed by its own GPS fixes, interpolated per trace, so a curved walk keeps its shape.
On each line the migration picker runs on every channel and the picks are merged across channels
(detect/channel_fusion.py); an interpreter's picks are added as interpreted targets. Targets on
neighbouring lines at a similar depth are linked into utility runs: a pipe crosses every line.

Every position carries its uncertainty, because a crew will dig by it:
- GPS: each line's own jitter, measured as the spread of its fixes about a straight fit against
  the wheel (company Set 1: a median 7 cm; some lines 0.6 m),
- the pick's position along the line (a median 5 cm from the operator's tags),
- the offset between the GPS antenna and the radar antenna, unknown until measured on the rig
  (`antenna_offset_uncertainty_m`; 0.15 m by default, stated, not hidden),
- depth: from the line's dielectric, assumed unless measured, about 15% either way.
Nothing here is dig-grade on its own: the dig map is for supervisor-confirmed targets, and phone GPS
(3-5 m) only gets a crew to the area.
"""

from __future__ import annotations

import copy
import dataclasses
import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from detect.channel_fusion import ChannelPicks, fuse, time_zero_ns
from detect.migration_picker import NEAR_SURFACE_NS, pick_targets
from studio import gps_georef, session
from studio import picks as pick_store
from studio.processing import SPEED_OF_LIGHT_M_PER_NS, remove_background
from studio.velocity import semblance_curve, stacked_permittivity

PICK_POSITION_ERROR_M = 0.05
DEPTH_RELATIVE_ERROR = 0.15
DEPTH_FLOOR_ERROR_M = 0.04
RUN_MAX_GAP_M = 1.5  # between targets on neighbouring lines
RUN_DEPTH_STEP_M = 0.2  # depth change allowed from one line to the next
RUN_DEPTH_RANGE_M = 0.4  # over the whole run: a pipe does not dive a metre between lines
RUN_MIN_LINES = 3
RUN_STRAIGHTNESS_M = 0.3  # every point of a run within this of its straight fit: pipes do not zigzag
RUN_MIN_CROSSING_DEG = 30.0  # a run must cross its lines, not lie along one of them
MIN_FIXES = 5
LINE_EDGE_M = 0.3  # migration leaves edge echoes at a line's ends; the nearest company tag is 0.5 m in
CHANCE_DRAWS = 30  # runs are compared with the same targets at random positions along their lines
CHANCE_P_MAX = 0.05
TARGETS_PER_LINE = 10
_DEFAULT_DIELECTRIC = 9.0
_M_PER_DEG_LAT = 110_574.0
_M_PER_DEG_LON_EQUATOR = 111_320.0


@dataclass(frozen=True)
class PlacedLine:
    job: str
    traces: np.ndarray  # fix trace indices
    lats: np.ndarray
    lons: np.ndarray
    gps_jitter_m: float  # RMS spread of the fixes about a straight fit against the wheel
    trace_spacing_m: float
    direction: tuple[float, float]  # unit (east, north) of travel, from the same fit

    def at(self, trace: float) -> tuple[float, float]:
        return float(np.interp(trace, self.traces, self.lats)), float(np.interp(trace, self.traces, self.lons))


@dataclass(frozen=True)
class SiteTarget:
    id: str
    job: str
    trace: float
    lat: float
    lon: float
    depth_m: float
    depth_error_m: float
    horizontal_error_m: float
    channels: tuple[str, ...]
    origin: str  # "suggested" (detector) or "interpreted" (a person's pick)
    depth_basis: str
    time_ns: float | None = None  # two-way time from time zero: what ties a suggestion back to its box


def site_of(job: str) -> str:
    return job.split("_", 1)[0]


def site_jobs(dataset_dir: Path, site: str) -> list[str]:
    return [j for j in session.list_jobs(dataset_dir) if site_of(j) == site and "_" in j]


def _metres(lat0: float, lon0: float, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    return np.c_[(lon - lon0) * _M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat0)), (lat - lat0) * _M_PER_DEG_LAT]


def place_line(job: str, job_dir: Path, trace_spacing_m: float) -> PlacedLine | None:
    """The line's track from its GPS log, or None when the log cannot place it (too few fixes)."""
    fixes = gps_georef.read_fixes(job_dir)
    if len({(f.lat, f.lon) for f in fixes}) < MIN_FIXES:
        return None
    traces = np.array([f.trace for f in fixes], dtype=float)
    lats, lons = np.array([f.lat for f in fixes]), np.array([f.lon for f in fixes])
    xy = _metres(lats.mean(), lons.mean(), lats, lons)
    design = np.c_[np.ones(len(traces)), traces * trace_spacing_m]
    coef, *_ = np.linalg.lstsq(design, xy, rcond=None)
    residual = xy - design @ coef
    jitter = float(np.sqrt(np.mean(np.sum(residual**2, axis=1))))
    step = coef[1] / max(float(np.linalg.norm(coef[1])), 1e-9)
    return PlacedLine(job, traces, lats, lons, jitter, trace_spacing_m, (float(step[0]), float(step[1])))


@lru_cache(maxsize=256)
def _detected(job_dir_str: str, _mtime_ns: int) -> tuple[tuple[float, float, tuple[str, ...], float | None], ...]:
    """(trace, time from time zero, channels, header dielectric or None) of the line's merged targets.

    Cached per file state. Picking migrates at the header's dielectric when it can describe ground,
    else at the default; the header value goes back so depth can use a measured one instead.
    """
    job_dir = Path(job_dir_str)
    channels: dict[str, ChannelPicks] = {}
    spacing, header = None, session.line_dielectric(job_dir)
    for info in session.available_channels(job_dir):
        frame = session.load_frame(job_dir, info.extension)
        traces = session.raw_traces(frame)
        spacing = info.trace_spacing_m
        channels[info.extension] = ChannelPicks(
            pick_targets(traces, sample_interval_ns=info.sample_interval_ns, trace_spacing_m=spacing,
                         dielectric=header or _DEFAULT_DIELECTRIC),
            info.sample_interval_ns, time_zero_ns(traces, info.sample_interval_ns))
    if not channels or spacing is None:
        return ()
    return tuple((t.trace, t.time_ns, t.channels, header) for t in fuse(channels, spacing)[:TARGETS_PER_LINE])


ARCHES_PER_LINE = 8


@lru_cache(maxsize=256)
def _semblance_curves(job_dir_str: str, _mtime_ns: int) -> tuple[tuple[float, ...], ...]:
    """Semblance-versus-permittivity of the line's strongest arches, on its first channel."""
    job_dir = Path(job_dir_str)
    info = session.available_channels(job_dir)[0]
    frame = session.load_frame(job_dir, info.extension)
    traces = session.raw_traces(frame)
    dt, spacing = info.sample_interval_ns, info.trace_spacing_m
    zero = round(time_zero_ns(traces, dt) / dt)
    data = remove_background(np.asarray(traces, dtype=np.float64).T[zero:], "mean", 0)
    picks = pick_targets(traces, sample_interval_ns=dt, trace_spacing_m=spacing,
                         dielectric=session.usable_dielectric(frame) or _DEFAULT_DIELECTRIC, max_picks=ARCHES_PER_LINE)
    return tuple(tuple(float(v) for v in semblance_curve(data, apex_trace=p.trace, apex_sample=p.sample - zero,
                                                         trace_spacing_m=spacing, sample_interval_ns=dt))
                 for p in picks if p.sample > zero)


def _mtime(job_dir: Path) -> int:
    return max(p.stat().st_mtime_ns for p in job_dir.iterdir())


def site_dielectric(dataset_dir: Path, site: str) -> tuple[float, str]:
    """The site's permittivity measured from its own hyperbolas, for lines whose header is unusable.

    Semblance summed over every clearly peaked arch on the site (studio/velocity.stacked_permittivity:
    a median 22% error on 116 excavated Twente surveys). Falls back to the default 9, saying so.
    """
    curves = []
    for job in site_jobs(dataset_dir, site):
        job_dir = session.resolve_job(job, dataset_dir)
        curves += [np.array(c) for c in _semblance_curves(str(job_dir), _mtime(job_dir))]
    measured = stacked_permittivity(curves)
    if measured is None:
        return _DEFAULT_DIELECTRIC, "default 9 (header unusable; too few clear arches to measure the site)"
    return measured, f"measured on this site ({len(curves)} arches; header unusable)"


def line_targets(job: str, job_dir: Path, line: PlacedLine, antenna_offset_uncertainty_m: float,
                 n_traces: int, fallback: tuple[float, str] | None = None) -> list[SiteTarget]:
    """`fallback` is the site's (dielectric, basis) for a line whose header cannot describe ground."""
    horizontal = math.sqrt(line.gps_jitter_m**2 + PICK_POSITION_ERROR_M**2 + antenna_offset_uncertainty_m**2)
    mtime = max(p.stat().st_mtime_ns for p in job_dir.iterdir())
    edge = LINE_EDGE_M / line.trace_spacing_m
    found = []
    for k, (trace, time_ns, channels, header) in enumerate(_detected(str(job_dir), mtime)):
        if time_ns <= 0 or trace < edge or trace > n_traces - 1 - edge:
            continue
        dielectric, basis = (header, f"header dielectric {header:g}") if header else (fallback or (_DEFAULT_DIELECTRIC, "default 9"))
        depth = SPEED_OF_LIGHT_M_PER_NS / math.sqrt(dielectric) * time_ns / 2
        lat, lon = line.at(trace)
        found.append(SiteTarget(f"{job}#T{k + 1}", job, trace, lat, lon, depth,
                                DEPTH_RELATIVE_ERROR * depth + DEPTH_FLOOR_ERROR_M, horizontal, channels,
                                "suggested", f"{basis}: {dielectric:.1f}" if not header else basis, time_ns))
    for pick in pick_store.load_picks(job):
        lat, lon = line.at(pick.trace)
        relative = 0.05 if pick.velocity_source == "fitted" else DEPTH_RELATIVE_ERROR
        found.append(SiteTarget(f"{job}#{pick.id}", job, pick.trace, lat, lon, pick.depth_m,
                                relative * pick.depth_m + DEPTH_FLOOR_ERROR_M, horizontal, (pick.channel,),
                                "interpreted", f"velocity {pick.velocity_source}"))
    return found


def _is_pipe_like(points: np.ndarray, line_direction: np.ndarray | None) -> bool:
    centred = points - points.mean(axis=0)
    direction = np.linalg.svd(centred, full_matrices=False)[2][0]
    off_line = np.abs(centred @ np.array([-direction[1], direction[0]]))
    if off_line.max() > RUN_STRAIGHTNESS_M:
        return False
    if line_direction is None:
        return True
    crossing = math.degrees(math.acos(min(1.0, abs(float(direction @ line_direction)))))
    return crossing >= RUN_MIN_CROSSING_DEG


def utility_runs(targets: Sequence[SiteTarget], line_order: Sequence[str],
                 line_direction: np.ndarray | None = None) -> list[list[SiteTarget]]:
    """Track targets from line to neighbouring line, the way a pipe crosses a set of survey lines.

    `line_order` lists the lines in order across the site. Between each pair of neighbours, targets
    are matched one-to-one, nearest first, when within RUN_MAX_GAP_M on the map and RUN_DEPTH_STEP_M
    in depth; matches chain into runs. A run must cross RUN_MIN_LINES lines and stay within
    RUN_DEPTH_RANGE_M of depth: linking every close pair instead chains clutter across a whole site
    into one "pipe" that dives a metre (it did, on company Set 1).
    """
    if not targets:
        return []
    lat0, lon0 = float(np.mean([t.lat for t in targets])), float(np.mean([t.lon for t in targets]))
    xy = _metres(lat0, lon0, np.array([t.lat for t in targets]), np.array([t.lon for t in targets]))
    by_line: dict[str, list[int]] = {}
    for i, t in enumerate(targets):
        by_line.setdefault(t.job, []).append(i)
    successor: dict[int, int] = {}
    has_predecessor: set[int] = set()
    for here, there in itertools.pairwise(line_order):
        pairs = sorted(
            (float(np.hypot(*(xy[i] - xy[j]))) + abs(targets[i].depth_m - targets[j].depth_m), i, j)
            for i in by_line.get(here, []) for j in by_line.get(there, [])
            if float(np.hypot(*(xy[i] - xy[j]))) <= RUN_MAX_GAP_M
            and abs(targets[i].depth_m - targets[j].depth_m) <= RUN_DEPTH_STEP_M)
        for _, i, j in pairs:
            if i not in successor and j not in has_predecessor:
                successor[i] = j
                has_predecessor.add(j)
    runs = []
    for start in (i for i in range(len(targets)) if i in successor and i not in has_predecessor):
        chain = [start]
        while chain[-1] in successor:
            chain.append(successor[chain[-1]])
        depths = [targets[i].depth_m for i in chain]
        if (len(chain) >= RUN_MIN_LINES and max(depths) - min(depths) <= RUN_DEPTH_RANGE_M
                and _is_pipe_like(xy[chain], line_direction)):
            runs.append([targets[i] for i in chain])
    return runs


FAMILY_ANGLE_DEG = 30.0


def line_families(lines: Sequence[PlacedLine]) -> list[list[PlacedLine]]:
    """Lines grouped by heading (either way of walking counts the same).

    A site is often surveyed as a grid: one family of lines crosses pipes running one way, the
    other family crosses pipes running the other way, and runs are tracked within a family only.
    """
    families: list[list[PlacedLine]] = []
    for line in sorted(lines, key=lambda candidate: candidate.job):
        heading = math.degrees(math.atan2(line.direction[1], line.direction[0])) % 180
        for family in families:
            ref = math.degrees(math.atan2(family[0].direction[1], family[0].direction[0])) % 180
            if min(abs(heading - ref), 180 - abs(heading - ref)) <= FAMILY_ANGLE_DEG:
                family.append(line)
                break
        else:
            families.append([line])
    return families


def _runs(placed: Sequence[PlacedLine], targets: Sequence[SiteTarget]) -> list[list[SiteTarget]]:
    return [run for family in line_families(placed)
            for run in utility_runs(targets, _across_order(family), _mean_direction(family))]


def _run_evidence(placed: Sequence[PlacedLine], lengths: dict[str, int], targets: Sequence[SiteTarget],
                  found: int) -> dict[str, Any]:
    """Do runs beat chance here? The same targets at random positions along their own lines.

    Short lines, a coarse grid or missed targets can make chance chains as common as real ones
    (company Set 1: 6 runs against 4.4 by chance), and a crew must not dig a coincidence.
    """
    lines = {line.job: line for line in placed}
    rng = np.random.default_rng(0)
    counts = []
    for _ in range(CHANCE_DRAWS):
        moved = []
        for t in targets:
            trace = float(rng.uniform(0, lengths[t.job] - 1))
            lat, lon = lines[t.job].at(trace)
            moved.append(dataclasses.replace(t, trace=trace, lat=lat, lon=lon))
        counts.append(len(_runs(placed, moved)))
    p = sum(c >= found for c in counts) / CHANCE_DRAWS if found else 1.0
    verdict = ("runs beat chance on this site" if found and p <= CHANCE_P_MAX
               else "unconfirmed: runs here are no more common than chance")
    return {"runs": found, "chance_mean": round(float(np.mean(counts)), 2), "p_chance": round(p, 3), "verdict": verdict}


def _mean_direction(lines: Sequence[PlacedLine]) -> np.ndarray:
    ref = np.array(lines[0].direction)
    mean = np.mean([d if d @ ref >= 0 else -d for d in (np.array(line.direction) for line in lines)], axis=0)
    return mean / max(float(np.linalg.norm(mean)), 1e-9)


def _across_order(lines: Sequence[PlacedLine]) -> list[str]:
    """Lines in order across the site: by where each sits perpendicular to the mean direction."""
    if not lines:
        return []
    ref = np.array(lines[0].direction)
    mean = np.mean([d if d @ ref >= 0 else -d for d in (np.array(line.direction) for line in lines)], axis=0)
    across = np.array([-mean[1], mean[0]]) / max(float(np.linalg.norm(mean)), 1e-9)
    lat0, lon0 = float(np.mean([line.lats.mean() for line in lines])), float(np.mean([line.lons.mean() for line in lines]))
    centre = {line.job: _metres(lat0, lon0, np.array([line.lats.mean()]), np.array([line.lons.mean()]))[0] for line in lines}
    return sorted(centre, key=lambda job: float(centre[job] @ across))


def _point(lat: float, lon: float) -> list[float]:
    return [round(lon, 8), round(lat, 8)]


def site_geojson(dataset_dir: Path, site: str, antenna_offset_uncertainty_m: float = 0.15) -> dict[str, Any]:
    """The site's field map, rebuilt only when one of its files changes (lines, GPS, tags, picks)."""
    stamp = tuple((job, _newest(session.resolve_job(job, dataset_dir)), _newest(pick_store.picks_path(job).parent))
                  for job in site_jobs(dataset_dir, site))
    return copy.deepcopy(_site_geojson(str(dataset_dir), site, antenna_offset_uncertainty_m, stamp))  # callers may edit


def _newest(folder: Path) -> int:
    return max((p.stat().st_mtime_ns for p in folder.iterdir()), default=0) if folder.exists() else 0


@lru_cache(maxsize=16)
def _site_geojson(dataset_dir_str: str, site: str, antenna_offset_uncertainty_m: float,
                  _stamp: tuple[tuple[str, int, int], ...]) -> dict[str, Any]:
    dataset_dir = Path(dataset_dir_str)
    features: list[dict[str, Any]] = []
    unplaced: list[str] = []
    targets: list[SiteTarget] = []
    placed: list[PlacedLine] = []
    lengths: dict[str, int] = {}
    jobs = site_jobs(dataset_dir, site)
    unusable = [job for job in jobs if session.line_dielectric(session.resolve_job(job, dataset_dir)) is None]
    fallback = site_dielectric(dataset_dir, site) if unusable else None
    for job in jobs:
        job_dir = session.resolve_job(job, dataset_dir)
        info = session.available_channels(job_dir)[0]
        line = place_line(job, job_dir, info.trace_spacing_m)
        if line is None:
            unplaced.append(job)
            continue
        ends = [line.at(0), line.at(info.n_traces - 1)]
        trace_points = [line.at(t) for t in np.linspace(0, info.n_traces - 1, 24)]
        features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [_point(*p) for p in trace_points]},
                         "properties": {"kind": "survey_line", "job": job, "length_m": round(info.line_length_m, 2),
                                        "gps_jitter_m": round(line.gps_jitter_m, 3), "start": _point(*ends[0])}})
        placed.append(line)
        lengths[job] = info.n_traces
        found = line_targets(job, job_dir, line, antenna_offset_uncertainty_m, info.n_traces, fallback)
        targets += found
        for t in found:
            features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": _point(t.lat, t.lon)},
                             "properties": {"kind": "target", "id": t.id, "job": t.job, "status": t.origin,
                                            "chainage_m": round(t.trace * line.trace_spacing_m, 3),
                                            "depth_m": round(t.depth_m, 2), "depth_error_m": round(t.depth_error_m, 2),
                                            "depth_basis": t.depth_basis,
                                            "horizontal_error_m": round(t.horizontal_error_m, 2),
                                            "channels": list(t.channels), "corroboration": len(t.channels),
                                            "trace": round(t.trace, 1),
                                            "near_surface": t.time_ns is not None and t.time_ns < NEAR_SURFACE_NS,
                                            "time_ns": None if t.time_ns is None else round(t.time_ns, 2)}})
    runs = _runs(placed, targets)
    evidence = _run_evidence(placed, lengths, targets, len(runs))
    for k, run in enumerate(runs):
        depths = [t.depth_m for t in run]
        features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [_point(t.lat, t.lon) for t in run]},
                         "properties": {"kind": "utility_run", "id": f"{site}#R{k + 1}", "lines": len({t.job for t in run}),
                                        "depth_m": round(float(np.median(depths)), 2),
                                        "depth_range_m": [round(min(depths), 2), round(max(depths), 2)],
                                        "targets": [t.id for t in run], "status": "suggested",
                                        "evidence": evidence["verdict"]}})
    return {"type": "FeatureCollection", "features": features,
            "metadata": {"site": site, "lines_placed": len({f["properties"]["job"] for f in features
                                                            if f["properties"]["kind"] == "survey_line"}),
                         "lines_not_placed": unplaced,
                         "antenna_offset_uncertainty_m": antenna_offset_uncertainty_m,
                         "dielectric": None if fallback is None else {
                             "value": round(fallback[0], 2), "basis": fallback[1], "lines_with_unusable_header": unusable},
                         "runs_evidence": evidence,
                         "note": ("Suggested targets are detector output for review; only supervisor-confirmed targets "
                                  "are dig-grade. Errors are one-sigma estimates; phone GPS (3-5 m) only finds the area.")}}
