"""The dig map: what a crew may act on, and how to find each item on the ground.

Field mode's site map (studio/site_map.py) shows everything the analysis found. The dig map shows
only what people decided:
- **confirmed**: a claim a named supervisor confirmed (studio/reviews.py). Always on the dig map.
- **interpreted**: an interpreter's own pick, not yet confirmed by a supervisor. Only when asked
  for, and flagged as such.
Proposed and rejected claims never appear.

A crew may dig by this, so nothing is guessed and nothing is dropped silently. A decision that
cannot be placed with confidence goes on the **needs-attention** list, shown in every output with
its reason: its target was regenerated or deleted since it was confirmed, its box is not one whose
apex position is known, decisions on one target conflict, its channel is gone, its line has no
usable GPS, or its time falls above time zero.

Each item can be found two ways, because phone GPS (3-5 m) cannot place a dig:
- its map position with a one-sigma horizontal error (the line's GPS jitter, the pick's position
  error, the GPS-to-radar antenna offset), for RTK GNSS or to walk to the area;
- its distance along its survey line from the line's start, for a tape from the start mark, which
  needs no satellite at all and is how GPR results are usually marked out.

Depth is always measured from the channel's own time zero (the direct wave), for picks as well as
for candidates, so the Studio's display time-zero setting cannot shift it.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core import boxes as box_store
from detect.channel_fusion import time_zero_ns
from detect.migration_picker import NEAR_SURFACE_NS
from studio import candidates as candidate_store
from studio import field_reports, session, site_map
from studio import picks as pick_store
from studio import reviews as review_store
from studio.processing import SPEED_OF_LIGHT_M_PER_NS

_DEFAULT_DIELECTRIC = 9.0
FITTED_DEPTH_RELATIVE_ERROR = 0.05


@dataclass(frozen=True)
class DigItem:
    id: str
    status: str  # "confirmed" or "interpreted"
    job: str
    lat: float
    lon: float
    horizontal_error_m: float
    chainage_m: float  # along the line from its start
    line_start: tuple[float, float]  # (lat, lon) of the line's first trace
    depth_m: float
    depth_error_m: float
    identity: str
    material: str
    decided_by: str
    decided_at: str
    depth_basis: str
    note: str = ""
    channel: str = ""  # the radargram channel the decision was made on
    near_surface: bool = False  # within NEAR_SURFACE_NS of the direct wave: may be the surface, check it
    field_outcome: str = ""  # the crew's latest report: found / not_found / dug, or empty
    field_report: str = ""  # who reported it, when, and any depth measured in the hole


@dataclass(frozen=True)
class Attention:
    job: str
    ref: str  # the review or pick id, empty for a whole line
    reason: str


class _Unplaceable(Exception):
    """This decision cannot be placed with confidence; the message says why."""


@dataclass(frozen=True)
class _Line:
    job: str
    job_dir: Path
    channels: dict[str, session.ChannelInfo]
    placed: site_map.PlacedLine
    horizontal_error_m: float
    fallback_dielectric: tuple[float, str] | None = None  # the site's, for a header that cannot describe ground


def _channel(line: _Line, name: str) -> session.ChannelInfo:
    info = line.channels.get(name)
    if info is None:
        raise _Unplaceable(f"channel {name} is no longer in this line")
    # GPS fixes are indexed by trace on the line; a channel on another trace grid cannot use them.
    if (info.trace_spacing_m, info.n_traces) != (line.placed.trace_spacing_m, max(c.n_traces for c in line.channels.values())):
        raise _Unplaceable(f"channel {name} has its own trace grid, which the line's GPS does not index")
    return info


def _depth(line: _Line, info: session.ChannelInfo, sample: float, velocity: float) -> tuple[float, bool]:
    """(depth, near the surface) of an apex at a raw sample index."""
    frame = session.load_frame(line.job_dir, info.extension)
    apex_ns = sample * info.sample_interval_ns - time_zero_ns(session.raw_traces(frame), info.sample_interval_ns)
    if apex_ns <= 0:
        raise _Unplaceable("its time is above the direct wave (time zero), so no depth exists")
    return velocity * apex_ns / 2, apex_ns < NEAR_SURFACE_NS


def _pick_item(line: _Line, pick: pick_store.Pick, *, status: str, decided_by: str, decided_at: str,
               identity: str, material: str, item_id: str, note: str = "") -> DigItem:
    info = _channel(line, pick.channel)
    depth, near = _depth(line, info, pick.sample, pick.velocity_m_per_ns)
    relative = FITTED_DEPTH_RELATIVE_ERROR if pick.velocity_source == "fitted" else site_map.DEPTH_RELATIVE_ERROR
    return replace(_item(line, info, pick.trace, depth, relative, status=status, decided_by=decided_by, decided_at=decided_at,
                         identity=identity, material=material, item_id=item_id, basis=f"velocity {pick.velocity_source}",
                         note=note), near_surface=near)


def _review_item(line: _Line, review: review_store.Review) -> DigItem:
    common = {"status": "confirmed", "decided_by": review.reviewer, "decided_at": review.decided_at,
              "identity": review.identity, "material": review.material, "item_id": f"{line.job}#{review.id}",
              "note": review.note}
    if review.target_kind == "pick":
        pick = next((p for p in pick_store.load_picks(line.job) if p.id == review.target_id), None)
        if pick is None:
            raise _Unplaceable("the pick it confirmed has been deleted; re-confirm it")
        return _pick_item(line, pick, **common)
    box = next((b for b in box_store.load_boxes(line.job) if b.id == review.target_id), None)
    if box is None:
        raise _Unplaceable("the candidate it confirmed was regenerated or deleted; re-confirm it")
    if box.detector != candidate_store.MIGRATION_DETECTOR:
        raise _Unplaceable("its box does not mark a known apex position; place a pick on the hyperbola and confirm that")
    info = _channel(line, review.channel)
    header = session.line_dielectric(line.job_dir)
    dielectric, basis = (header, f"header dielectric {header:g}") if header else (
        line.fallback_dielectric or (_DEFAULT_DIELECTRIC, "default 9"))
    apex_sample = box.y + candidate_store.BOX_ABOVE_NS / info.sample_interval_ns
    depth, near = _depth(line, info, apex_sample, SPEED_OF_LIGHT_M_PER_NS / math.sqrt(dielectric))
    return replace(_item(line, info, box.x + box.w / 2, depth, site_map.DEPTH_RELATIVE_ERROR,
                         basis=basis if header else f"{basis}: {dielectric:.1f}", **common), near_surface=near)


def _item(line: _Line, info: session.ChannelInfo, trace: float, depth: float, relative: float, *, status: str,
          decided_by: str, decided_at: str, identity: str, material: str, item_id: str, basis: str,
          note: str = "") -> DigItem:
    lat, lon = line.placed.at(trace)
    return DigItem(id=item_id, status=status, job=line.job, channel=info.extension, lat=lat, lon=lon,
                   horizontal_error_m=line.horizontal_error_m,
                   chainage_m=trace * info.trace_spacing_m, line_start=line.placed.at(0), depth_m=depth,
                   depth_error_m=relative * depth + site_map.DEPTH_FLOOR_ERROR_M, identity=identity, material=material,
                   decided_by=decided_by, decided_at=decided_at, depth_basis=basis, note=note)


def _standing_confirmations(job: str, reviews: list[review_store.Review], attention: list[Attention]) -> list[review_store.Review]:
    """One confirmed review per target; a target whose decisions disagree goes to attention instead."""
    # Decision times have one-second resolution; the file's order (each proposal is appended) breaks ties.
    order = {r.id: k for k, r in enumerate(reviews)}
    by_target: dict[tuple[str, str], list[review_store.Review]] = {}
    for r in reviews:
        if r.status in ("confirmed", "rejected"):
            by_target.setdefault((r.target_kind, r.target_id), []).append(r)
    standing = []
    for decided in by_target.values():
        confirmed = [r for r in decided if r.status == "confirmed"]
        if not confirmed:
            continue
        latest = max(decided, key=lambda r: (r.decided_at, order[r.id]))
        identities = {r.identity.strip().lower() for r in confirmed}
        if latest.status == "rejected" or len(identities) > 1:
            attention.append(Attention(job, confirmed[-1].id, "decisions on this target conflict "
                                       "(confirmed and later rejected, or confirmed as different things)"))
            continue
        standing.append(max(confirmed, key=lambda r: (r.decided_at, order[r.id])))
    return standing


MATCH_POSITION_M = 0.3
MATCH_TIME_NS = 1.5


class AlreadyDecided(ValueError):
    """The target already carries a supervisor decision; change it in Interpret › Review instead."""


def confirm_suggestion(dataset_dir: Path, site: str, target_id: str, *, identity: str, material: str,
                       reviewer: str, note: str = "", seen_trace: float | None = None,
                       seen_time_ns: float | None = None, acknowledge_near_surface: bool = False) -> review_store.Review:
    """A supervisor puts a site-map suggestion on the dig map, without the reasoning layer.

    The suggestion (`<job>#T<n>`, one target merged across channels) is tied back to the stored
    migration-picker box it came from: same line, a channel that saw it, within 0.3 m and 1.5 ns.
    The confirmation goes through the ordinary review record, named and timestamped, so the dig
    map, its audit trail and the training export treat it like any other confirmed claim.

    Suggestion ids are ranks, so the caller says where the suggestion it looked at was
    (`seen_trace`, `seen_time_ns`); if the server's suggestion of that id has moved since, nothing is
    confirmed. A near-surface suggestion needs `acknowledge_near_surface`, and a target that already
    carries a decision is refused rather than silently overridden.
    """
    if not reviewer.strip():
        raise ValueError("a confirmation needs the supervisor's name")
    if not identity.strip():
        raise ValueError("say what it is (e.g. 'water pipe', 'unknown utility')")
    job, _, ref = target_id.partition("#")
    if job not in site_map.site_jobs(dataset_dir, site) or not ref.startswith("T"):
        raise KeyError(target_id)
    job_dir = session.resolve_job(job, dataset_dir)
    suggestion = next((f["properties"] for f in site_map.site_geojson(dataset_dir, site)["features"]
                       if f["properties"].get("id") == target_id), None)
    if suggestion is None or suggestion.get("time_ns") is None:
        raise KeyError(target_id)
    if seen_trace is None or seen_time_ns is None:
        raise ValueError("say which suggestion you looked at (its trace and time)")
    spacing = session.available_channels(job_dir)[0].trace_spacing_m
    if (abs(suggestion["trace"] - seen_trace) * spacing > MATCH_POSITION_M / 3
            or abs(suggestion["time_ns"] - seen_time_ns) > MATCH_TIME_NS / 3):
        raise ValueError("the suggestions have changed since you opened them; reload the site and check it again")
    if suggestion.get("near_surface") and not acknowledge_near_surface:
        raise ValueError("this suggestion is near the surface; check its radargram and tick that you have")
    candidate_store.ensure_candidates(job, job_dir)
    channels = {c.extension: c for c in session.available_channels(job_dir)}
    best = None
    for box in box_store.load_boxes(job):
        info = channels.get(box.channel)
        if (box.detector != candidate_store.MIGRATION_DETECTOR or info is None or box.channel not in suggestion["channels"]
                or box.y <= 0):  # a box clipped at the record top no longer marks where its apex is
            continue
        frame = session.load_frame(job_dir, box.channel)
        apex_ns = (box.y * info.sample_interval_ns + candidate_store.BOX_ABOVE_NS
                   - time_zero_ns(session.raw_traces(frame), info.sample_interval_ns))
        dx = abs(box.x + box.w / 2 - suggestion["trace"]) * info.trace_spacing_m
        dt = abs(apex_ns - suggestion["time_ns"])
        if dx <= MATCH_POSITION_M and dt <= MATCH_TIME_NS and (best is None or dx + dt / 10 < best[0]):
            best = (dx + dt / 10, box)
    if best is None:
        raise ValueError("the suggestion no longer matches a stored candidate; reopen the site to refresh it")
    box = best[1]
    decided = [r for r in review_store.load_reviews(job) if r.target_id == box.id and r.status != "proposed"]
    if decided:
        last = decided[-1]
        raise AlreadyDecided(f"already {last.status} by {last.reviewer} as '{last.identity}'; change it in Interpret › Review")
    (proposed,) = review_store.propose(job, [{
        "channel": box.channel, "target_id": box.id, "target_kind": "candidate", "x": box.x, "y": box.y, "w": box.w,
        "h": box.h, "identity": identity.strip(), "material": material.strip(), "confidence": "supervisor",
        "why": f"confirmed from the field map ({target_id})", "layer": "field map", "model": "supervisor"}])
    return review_store.decide(job, proposed.id, "confirmed", reviewer, note)


def dig_items(dataset_dir: Path, site: str, *, include_interpreted: bool = False,
              antenna_offset_uncertainty_m: float = 0.15) -> tuple[list[DigItem], list[Attention]]:
    """The site's dig items, and every decision that could not be placed, with its reason."""
    items: list[DigItem] = []
    attention: list[Attention] = []
    for job in site_map.site_jobs(dataset_dir, site):
        reviews = _standing_confirmations(job, review_store.load_reviews(job), attention)
        confirmed_picks = {r.target_id for r in reviews if r.target_kind == "pick"}
        picks = [p for p in pick_store.load_picks(job) if p.id not in confirmed_picks] if include_interpreted else []
        if not reviews and not picks:
            continue
        job_dir = session.resolve_job(job, dataset_dir)
        channels = {c.extension: c for c in session.available_channels(job_dir)}
        if not channels:
            attention.append(Attention(job, "", "the line has no readable channel"))
            continue
        spacing = next(iter(channels.values())).trace_spacing_m
        placed = site_map.place_line(job, job_dir, spacing)
        if placed is None:
            attention.append(Attention(job, "", "the line has no usable GPS; mark its items by tape from the line start"))
            continue
        horizontal = math.sqrt(placed.gps_jitter_m**2 + site_map.PICK_POSITION_ERROR_M**2 + antenna_offset_uncertainty_m**2)
        fallback = None if session.line_dielectric(job_dir) else site_map.site_dielectric(dataset_dir, site)
        line = _Line(job, job_dir, channels, placed, horizontal, fallback)
        for r in reviews:
            try:
                items.append(_review_item(line, r))
            except _Unplaceable as why:
                attention.append(Attention(job, r.id, str(why)))
        for p in picks:
            try:
                items.append(_pick_item(line, p, status="interpreted", decided_by="interpreter (not supervisor-confirmed)",
                                        decided_at=p.created_at, identity=p.label, material="", item_id=f"{job}#{p.id}",
                                        note=p.note))
            except _Unplaceable as why:
                attention.append(Attention(job, p.id, str(why)))
    outcomes: dict[str, dict[str, field_reports.FieldReport]] = {}
    for job in {i.job for i in items}:
        try:
            outcomes[job] = field_reports.latest_outcomes(job)
        except ValueError:
            outcomes[job] = {}
            attention.append(Attention(job, "", "the field-reports file for this line is unreadable; its outcomes are not shown"))
    return [_with_outcome(i, outcomes[i.job].get(i.id)) for i in items], attention


def _with_outcome(item: DigItem, report: field_reports.FieldReport | None) -> DigItem:
    if report is None:
        return item
    found = f", {report.depth_found_m:.2f} m deep in the hole" if report.depth_found_m is not None else ""
    return replace(item, field_outcome=report.outcome, field_report=f"{report.reporter}, {report.reported_at}{found}")


def to_geojson(site: str, items: list[DigItem], attention: list[Attention]) -> dict[str, Any]:
    features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(i.lon, 8), round(i.lat, 8)]},
                 "properties": {"kind": "dig_item", "id": i.id, "status": i.status, "line": i.job, "channel": i.channel,
                                "chainage_m": round(i.chainage_m, 2),
                                "line_start": [round(i.line_start[1], 8), round(i.line_start[0], 8)],
                                "horizontal_error_m": round(i.horizontal_error_m, 2),
                                "depth_m": round(i.depth_m, 2),
                                "depth_range_m": [round(max(i.depth_m - i.depth_error_m, 0), 2),
                                                  round(i.depth_m + i.depth_error_m, 2)],
                                "depth_basis": i.depth_basis, "identity": i.identity, "material": i.material,
                                "decided_by": i.decided_by, "decided_at": i.decided_at, "note": i.note,
                                "field_outcome": i.field_outcome, "field_report": i.field_report,
                                "near_surface": i.near_surface}}
                for i in items]
    return {"type": "FeatureCollection", "features": features,
            "metadata": {"site": site, "items": len(items),
                         "confirmed": sum(i.status == "confirmed" for i in items),
                         "needs_attention": [{"line": a.job, "ref": a.ref, "reason": a.reason} for a in attention],
                         "note": ("Only supervisor-confirmed items are dig-grade decisions. Positions are one-sigma; "
                                  "mark each by tape from its line's start mark, or with RTK GNSS. Phone GPS (3-5 m) "
                                  "only finds the area. Always verify with a cable locator before breaking ground.")}}


_COLUMNS = ("id", "status", "line", "chainage_m", "lat", "lon", "horizontal_error_m", "depth_m", "depth_min_m",
            "depth_max_m", "identity", "material", "decided_by", "decided_at", "depth_basis", "note",
            "line_start_lat", "line_start_lon", "field_outcome", "field_report", "attention_reason")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _text(value: str) -> str:
    """A free-text cell made inert for spreadsheets: a leading = + - @ would run as a formula."""
    return f"'{value}" if value.startswith(_FORMULA_START) else value


def to_csv(items: list[DigItem], attention: list[Attention]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_COLUMNS)
    for i in items:
        writer.writerow([_text(i.id), i.status, _text(i.job), f"{i.chainage_m:.2f}", f"{i.lat:.8f}", f"{i.lon:.8f}",
                         f"{i.horizontal_error_m:.2f}", f"{i.depth_m:.2f}", f"{max(i.depth_m - i.depth_error_m, 0):.2f}",
                         f"{i.depth_m + i.depth_error_m:.2f}", _text(i.identity), _text(i.material),
                         _text(i.decided_by), i.decided_at, _text(i.depth_basis), _text(i.note),
                         f"{i.line_start[0]:.8f}", f"{i.line_start[1]:.8f}", i.field_outcome, _text(i.field_report), ""])
    for a in attention:
        writer.writerow([_text(f"{a.job}#{a.ref}" if a.ref else a.job), "needs_attention", _text(a.job)]
                        + [""] * (len(_COLUMNS) - 4) + [_text(a.reason)])
    return buffer.getvalue()


def to_pdf(site: str, items: list[DigItem], attention: list[Attention]) -> bytes:
    """The dig sheet a crew takes to site: one row per item, how to mark it, how deep."""
    styles = getSampleStyleSheet()
    body = styles["BodyText"]

    def cell(text: object) -> Paragraph:
        return Paragraph(escape(str(text)), body)

    story: list[Any] = [
        Paragraph(escape(f"Dig sheet — site {site}"), styles["Title"]),
        cell(f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}. "
             f"{sum(i.status == 'confirmed' for i in items)} supervisor-confirmed item(s), "
             f"{sum(i.status == 'interpreted' for i in items)} interpreted (not yet confirmed), "
             f"{len(attention)} needing attention (not on this sheet as dig items)."),
        cell("Mark each item by tape along its survey line from the line's start mark, or with RTK GNSS. "
             "Phone GPS only finds the area. Depths are ranges, not promises. Verify with a cable locator "
             "and hand-dig the last 0.3 m."),
        Spacer(1, 0.4 * cm),
    ]
    header = ["Item", "Status", "Line", "From line start", "Lat, lon", "Map ± (m)", "Depth", "What", "Decided by"]
    rows = [header] + [[cell(i.id.split("#")[-1]), cell(i.status), cell(i.job), cell(f"{i.chainage_m:.2f} m"),
                        cell(f"{i.lat:.7f}, {i.lon:.7f}"), cell(f"{i.horizontal_error_m:.2f}"),
                        cell(f"{max(i.depth_m - i.depth_error_m, 0):.2f}–{i.depth_m + i.depth_error_m:.2f} m"),
                        cell(" ".join(x for x in (i.identity, i.material, i.note) if x)), cell(i.decided_by)]
                       for i in items]
    table = Table(rows, repeatRows=1, colWidths=[2.2 * cm, 2.2 * cm, 3 * cm, 2.6 * cm, 3.8 * cm, 1.8 * cm, 2.6 * cm,
                                                 5.2 * cm, 3.8 * cm])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                               ("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(table if items else cell("Nothing confirmed for this site yet."))
    if attention:
        story += [Spacer(1, 0.5 * cm), Paragraph("Needs attention before digging", styles["Heading2"])]
        story += [cell(f"{a.job}{' #' + a.ref if a.ref else ''}: {a.reason}") for a in attention]
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                      topMargin=1.2 * cm, bottomMargin=1.2 * cm, title=f"Dig sheet {site}").build(story)
    return buffer.getvalue()
