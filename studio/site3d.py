"""A site in 3D: every radar line of a site as a curtain along its real track (static/js/view3d.js).

Positions come from the same sources as the Map, in the same order (geo_routes._source):

- **surveyed** reference points, else the **GPS track** — both placed by georef between reference
  points, so a curtain covers exactly the chainage the Map draws and nothing past its ends;
- **location only** (the GPS froze): a centre and a radius, no direction. It gets no curtain and no
  targets along it, because drawing either would invent the line's direction;
- **not placed**: listed as needing positions, never drawn.

Depth is left to the client on purpose. Each target carries its raw sample index and its channel's
sample interval, and each line its velocity and where that came from, so the 3D view places targets
with the same velocity and time zero (the "First sample" setting) as the 2D ruler. Utility runs
(studio/runs.py) state depth below ground from each line's measured time zero instead, as Field mode
does; the 3D view labels which zero each depth it shows is counted from.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from core.geo import interpolate, to_local
from detect.channel_fusion import time_zero_ns
from studio import candidates as candidate_store
from studio import georef, session, site_map
from studio import picks as pick_store
from studio import reviews as review_store
from studio.processing import SPEED_OF_LIGHT_M_PER_NS

PATH_STEP_M = 0.5  # curtain vertices along the line; GPS-track anchors are metres apart anyway


@dataclass(frozen=True)
class LineSource:
    """One line of the site and what geo_routes._source found for it."""

    job: str
    job_dir: Path
    kind: str | None  # "surveyed" | "gps_track" | "gps_location" | None
    found: Any  # reference points, a GpsAssessment, or None
    channels: dict[str, session.ChannelInfo]
    error: str | None = None


def _from_dielectric(dielectric: float) -> float:
    return round(SPEED_OF_LIGHT_M_PER_NS / math.sqrt(dielectric), 5)


def _velocity(job: str, channel: session.ChannelInfo, site_estimate: Callable[[], tuple[float, str]]) -> dict[str, Any]:
    """The line's velocity and where it came from, most trustworthy first.

    Fitted on this line's own hyperbolas ("fitted", what the 2D view calls measured); else the
    header's dielectric ("assumed"); else the site's own arches, as Field mode uses (studio/site_map
    .site_dielectric), which says itself when it could only fall back to the default.
    """
    fitted = [p.velocity_m_per_ns for p in pick_store.load_picks(job)
              if p.velocity_source == "fitted" and p.channel == channel.extension]
    if fitted:
        return {"value": round(statistics.median(fitted), 5), "source": "fitted",
                "basis": f"median of {len(fitted)} hyperbola fit(s) on this line"}
    if channel.dielectric_assumed:
        return {"value": _from_dielectric(channel.dielectric_assumed), "source": "assumed",
                "basis": f"header dielectric {channel.dielectric_assumed:g}"}
    dielectric, basis = site_estimate()
    measured = basis.startswith("measured")
    return {"value": _from_dielectric(dielectric), "source": "site" if measured else "assumed",
            "basis": f"dielectric {dielectric:.2f}, {basis}"}


def _detected(job: str, job_dir: Path, n_traces: int, spacing_m: float) -> list[dict[str, Any]]:
    """The site picker's targets on this line, as Field mode places them (same ids, same edge rule).

    Their time is from each channel's measured time zero, not a raw sample, so they carry time_ns.
    """
    mtime = site_map._mtime(job_dir)
    edge = site_map.LINE_EDGE_M / spacing_m
    contrasts = site_map._contrasts(str(job_dir), mtime)
    return [{"kind": "detected", "id": f"{job}#T{k + 1}", "job": job, "trace": trace, "time_ns": round(time_ns, 3),
             "channels": list(channels), "contrast": contrasts[k], "chainage_m": round(trace * spacing_m, 3)}
            for k, (trace, time_ns, channels, _header) in enumerate(site_map._detected(str(job_dir), mtime))
            if time_ns > 0 and edge <= trace <= n_traces - 1 - edge]


def _targets(job: str, job_dir: Path, channels: dict[str, session.ChannelInfo]) -> list[dict[str, Any]]:
    """Candidates, picks and claims at their apex: trace along the line, raw sample down it."""
    out: list[dict[str, Any]] = []
    for c in candidate_store.load_candidates(job, job_dir):
        if c.channel in channels:  # the box's top edge is the hyperbola's apex
            out.append({"kind": "candidate", "id": c.id, "channel": c.channel, "trace": c.x + c.w / 2, "sample": c.y,
                        "class": c.suggested_class, "corroboration": c.corroboration})
    for p in pick_store.load_picks(job):
        if p.channel in channels:
            out.append({"kind": "pick", "id": p.id, "channel": p.channel, "trace": p.trace, "sample": p.sample,
                        "label": p.label, "velocity_source": p.velocity_source})
    for r in review_store.load_reviews(job):
        if r.channel in channels:  # the claim's box is a copy of its target's
            out.append({"kind": "claim", "id": r.id, "channel": r.channel, "trace": r.x + r.w / 2, "sample": r.y,
                        "identity": r.identity, "status": r.status, "target_id": r.target_id})
    for t in out:
        info = channels[t["channel"]]
        t.update(job=job, dt_ns=info.sample_interval_ns, chainage_m=round(t["trace"] * info.trace_spacing_m, 3))
    return out


def _time_zero_ns(job_dir: Path, channel: session.ChannelInfo) -> float:
    return time_zero_ns(session.raw_traces(session.load_frame(job_dir, channel.extension)), channel.sample_interval_ns)


def _curtain(line: LineSource, channel: session.ChannelInfo, origin: tuple[float, float],
            site_estimate: Callable[[], tuple[float, str]], detected: bool) -> dict[str, Any]:
    points = line.found if line.kind == "surveyed" else line.found.reference_points
    placement = georef.place(points)
    start = max(0.0, placement.anchors[0][0])
    end = min(channel.line_length_m, placement.anchors[-1][0])
    steps = max(1, math.ceil((end - start) / PATH_STEP_M))
    path = []
    for i in range(steps + 1):
        chainage = start + (end - start) * i / steps
        at = interpolate(chainage, placement.anchors)
        if at is not None:
            east, north = to_local(at[0], at[1], *origin)
            path.append([round(chainage, 3), round(east, 3), round(north, 3)])
    found = _targets(line.job, line.job_dir, line.channels)
    if detected:
        found += _detected(line.job, line.job_dir, channel.n_traces, channel.trace_spacing_m)
    targets = [t for t in found if start <= t["chainage_m"] <= end]
    return {
        "job": line.job, "source": line.kind, "channel": channel.extension,
        "n_traces": channel.n_traces, "n_samples": channel.n_samples,
        "trace_spacing_m": channel.trace_spacing_m, "sample_interval_ns": channel.sample_interval_ns,
        "line_length_m": round(channel.line_length_m, 3), "covered_m": [round(start, 3), round(end, 3)],
        "path": path, "velocity": _velocity(line.job, channel, site_estimate),
        # Where the picker's time zero sits in this channel's record: a detected target's time_ns
        # plus this is its time on the curtain, which (like the 2D view) starts at the record.
        "time_zero_ns": round(_time_zero_ns(line.job_dir, channel), 3),
        "position_error_m": placement.position_error_m,
        "warnings": placement.warnings() + ([] if line.kind == "surveyed" else [line.found.reason]),
        "targets": targets,
    }


def _origin(lines: Sequence[LineSource]) -> tuple[float, float] | None:
    """A local east/north origin: the mean of every placed position on the site."""
    spots = [(p.lat, p.lon) for line in lines if line.kind in ("surveyed", "gps_track")
             for p in (line.found if line.kind == "surveyed" else line.found.reference_points)]
    spots += [line.found.centre for line in lines if line.kind == "gps_location"]
    if not spots:
        return None
    return statistics.fmean(s[0] for s in spots), statistics.fmean(s[1] for s in spots)


def scene(site: str, lines: Sequence[LineSource], channel: str,
          site_estimate: Callable[[], tuple[float, str]], *, detected: bool = True) -> dict[str, Any]:
    """`site_estimate` gives the site's (dielectric, basis); called only if a line needs it (about 1 s).

    `detected=False` leaves out the site picker's targets, which take seconds on a site not yet
    picked: the view draws the lines from that answer first, then asks again with them.
    """
    site_estimate = cache(site_estimate)
    origin = _origin(lines)
    curtains: list[dict[str, Any]] = []
    located: list[dict[str, Any]] = []
    unplaced: list[dict[str, str]] = []
    for line in lines:
        info = line.channels.get(channel) or next(iter(line.channels.values()), None)
        if line.error or info is None or origin is None or line.kind is None:
            unplaced.append({"job": line.job, "reason": line.error or "no reference points and no usable GPS"})
        elif line.kind == "gps_location":
            (lat, lon), (lat0, lon0) = line.found.centre, origin
            east, north = to_local(lat, lon, lat0, lon0)
            located.append({"job": line.job, "east": round(east, 3), "north": round(north, 3),
                            "radius_m": line.found.radius_m, "reason": line.found.reason, "channel": info.extension,
                            "n_samples": info.n_samples, "sample_interval_ns": info.sample_interval_ns,
                            "velocity": _velocity(line.job, info, site_estimate),
                            "targets_not_drawn": len(_targets(line.job, line.job_dir, line.channels))})
        else:
            curtains.append(_curtain(line, info, origin, site_estimate, detected))
    return {
        "site": site, "channel": channel,
        "origin": None if origin is None else {"lat": round(origin[0], 8), "lon": round(origin[1], 8)},
        "lines": curtains, "located": located, "unplaced": unplaced,
        "detected_included": detected,
        "topography": None,  # no site in the dataset has a surface model yet; the layer is skipped
    }
