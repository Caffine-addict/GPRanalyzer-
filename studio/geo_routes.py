"""HTTP endpoints for the map: vendor utilities, line georeferencing, GeoJSON export.

See docs/GEOSPATIAL_PLAN.md (phases 1-3), reference/sue_geo.py and studio/georef.py.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import Response

from reference import sue_geo
from studio import candidates as candidate_store
from studio import geo_export, georef, gps_georef, session
from studio import picks as pick_store
from studio import reviews as review_store

router = APIRouter()
_GEOJSON = "application/geo+json"


def _dataset_dir(request: Request) -> Path:
    return getattr(request.app.state, "dataset_dir", session.DEFAULT_DATASET_DIR)


def _job(request: Request, job_name: str) -> Path:
    try:
        return session.resolve_job(job_name, _dataset_dir(request))
    except session.JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _safe_filename(stem: str) -> str:
    """A download name that cannot break the Content-Disposition header (quotes, CR/LF)."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", stem) or "export"


def _geojson(payload: dict[str, Any], filename: str | None = None) -> Response:
    headers = {"Content-Disposition": f'attachment; filename="{_safe_filename(filename)}"'} if filename else {}
    return Response(json.dumps(payload), media_type=_GEOJSON, headers=headers)


_CHANNEL_CACHE: dict[Path, tuple[tuple[int, ...], dict[str, session.ChannelInfo]]] = {}


def _channels(job_dir: Path) -> dict[str, session.ChannelInfo]:
    """Every channel's axis calibration, keyed by extension.

    Cached per job until a channel file changes: the map asks for every line's length on each
    request, and re-parsing every SPR file each time scales with the number of lines.
    """
    paths = [(ext, session.channel_path(job_dir, ext)) for ext, _label in session.CHANNEL_ORDER]
    present = [(ext, path) for ext, path in paths if path.exists()]
    # mtime and size: a re-import restoring identical timestamps but different data still misses
    key = tuple(v for _, path in present for v in (path.stat().st_mtime_ns, path.stat().st_size))
    cached = _CHANNEL_CACHE.get(job_dir.resolve())
    if cached and cached[0] == key:
        return cached[1]
    infos = {ext: session.describe_channel(session.load_frame(job_dir, ext), ext) for ext, _ in present}
    _CHANNEL_CACHE[job_dir.resolve()] = (key, infos)
    return infos


def vendor_collection(request: Request) -> dict[str, Any]:
    """Vendor call-outs, recomputed only when the processed CSVs change."""
    processed = next(iter(sorted(_dataset_dir(request).rglob("_processed"))), None)
    if processed is None:
        return {"type": "FeatureCollection", "features": [], "metadata": {"reason": "no processed vendor drawings"}}
    utilities, points = processed / "sue_utilities.csv", processed / "sue_geo_points.csv"
    if not utilities.exists() or not points.exists():
        return {"type": "FeatureCollection", "features": [], "metadata": {"reason": "vendor CSVs missing"}}
    key = (utilities.stat().st_mtime_ns, points.stat().st_mtime_ns)
    cached = getattr(request.app.state, "vendor_geo_cache", None)
    if cached and cached[0] == key:
        return cached[1]
    collection = sue_geo.feature_collection(sue_geo.load_rows(utilities), sue_geo.load_rows(points))
    # Each call-out links to its marked drawing (Survey documents path), opened at its sheet.
    dataset = _dataset_dir(request)
    for feature in collection["features"]:
        marked = processed / f"{Path(feature['properties']['drawing']).stem} - marked.pdf"
        feature["properties"]["marked_pdf"] = marked.relative_to(dataset).as_posix() if marked.exists() else None
    request.app.state.vendor_geo_cache = (key, collection)
    return collection


@router.get("/api/geo/vendor.geojson")
def vendor_geojson(request: Request, download: bool = False) -> Response:
    return _geojson(vendor_collection(request), "vendor_utilities.geojson" if download else None)


@router.get("/api/geo/lines")
def georeferenced_lines(request: Request) -> list[dict[str, Any]]:
    """Every line, and whether it has reference points yet."""
    lines = []
    for job in session.list_jobs(_dataset_dir(request)):
        try:
            source = _source(job, _job(request, job))[0]
        except (ValueError, KeyError, session.JobNotFoundError):
            source = None
        lines.append({"job": job, "georeferenced": source is not None, "source": source})
    return lines


def _source(job_name: str, job_dir: Path) -> tuple[str | None, Any]:
    """How this line gets onto the map: surveyed points first, then whatever its GPS supports.

    Returns ("surveyed", points) | ("gps_track", assessment) | ("gps_location", assessment) | (None, None).
    """
    points = georef.load(job_name)
    if points:
        return "surveyed", points
    channels = _channels(job_dir)
    info = channels.get("RAD") or next(iter(channels.values()), None)
    if info is None:
        return None, None
    length = max(i.line_length_m for i in channels.values())
    gps = gps_georef.assess(gps_georef.read_fixes(job_dir), info.trace_spacing_m, length)
    if gps.kind == "track":
        return "gps_track", gps
    if gps.kind == "location":
        return "gps_location", gps
    return None, None


@router.get("/api/jobs/{job_name}/georef")
def get_georef(request: Request, job_name: str) -> dict[str, Any]:
    job_dir = _job(request, job_name)
    try:
        points = georef.load(job_name)
        length = max((info.line_length_m for info in _channels(job_dir).values()), default=0.0)
    except (ValueError, session.JobNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not points:
        _kind, gps = _source(job_name, job_dir)
        return {"reference_points": [], "line_length_m": length, "placement": None,
                "gps": None if gps is None else {"kind": gps.kind, "reason": gps.reason, "fixes": gps.fixes,
                                                 "max_disagreement_m": gps.max_disagreement_m}}
    placement = georef.place(points)
    return {"reference_points": [asdict(p) for p in points], "line_length_m": length,
            "placement": {"scale_ratio": placement.scale_ratio, "straightness_m": placement.straightness_m,
                          "position_error_m": placement.position_error_m, "warnings": placement.warnings()}}


@router.put("/api/jobs/{job_name}/georef")
def put_georef(request: Request, job_name: str,
               body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008 - FastAPI idiom
    job_dir = _job(request, job_name)
    length = max((info.line_length_m for info in _channels(job_dir).values()), default=0.0)
    try:
        points = georef.validate(body.get("reference_points"), length)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    georef.save(job_name, points)
    return get_georef(request, job_name)


def _targets(job_name: str, job_dir: Path, channels: dict[str, session.ChannelInfo]) -> list[dict[str, Any]]:
    """Picks, detector candidates and model claims, each with its chainage along the line."""
    targets: list[dict[str, Any]] = []
    for pick in pick_store.load_picks(job_name):
        info = channels.get(pick.channel)
        if info:
            targets.append({"kind": "target", "id": pick.id, "origin": "interpreter pick", "channel": pick.channel,
                            "chainage_m": round(pick.trace * info.trace_spacing_m, 3), "depth_m": round(pick.depth_m, 3),
                            "depth_basis": f"velocity {pick.velocity_source}", "label": pick.label,
                            "review_status": None})
    for c in candidate_store.load_candidates(job_name, job_dir):
        info = channels.get(c.channel)
        if info:
            targets.append({"kind": "target", "id": c.id, "origin": "detector candidate", "channel": c.channel,
                            "chainage_m": round((c.x + c.w / 2) * info.trace_spacing_m, 3),
                            "shape": c.shape, "suggested_class": c.suggested_class, "review_status": None})
    for r in review_store.load_reviews(job_name):
        info = channels.get(r.channel)
        if info and r.status != "rejected":
            targets.append({"kind": "claim", "id": r.id, "channel": r.channel,
                            "chainage_m": round((r.x + r.w / 2) * info.trace_spacing_m, 3),
                            "identity": r.identity, "material": r.material, "confidence": r.confidence,
                            "review_status": r.status, "reviewer": r.reviewer or None})
    return targets


def line_collection(request: Request, job_name: str) -> dict[str, Any]:
    job_dir = _job(request, job_name)
    try:
        kind, found = _source(job_name, job_dir)
        channels = _channels(job_dir)
        if kind is None:
            return {"type": "FeatureCollection", "features": [],
                    "metadata": {"job": job_name, "reason": "no reference points and no usable GPS"}}
        length = max(info.line_length_m for info in channels.values())
        if kind == "gps_location":
            lat, lon = found.centre
            feature = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]},
                       "properties": {"kind": "line_location", "id": job_name, "source": job_name,
                                      "position_method": "onboard_gps_location_only", "position_confidence": "estimated",
                                      "uncertainty_radius_m": found.radius_m, "line_length_m": round(length, 2),
                                      "gps_fixes": found.fixes, "warnings": [found.reason]}}
            return {"type": "FeatureCollection", "features": [feature],
                    "metadata": {"job": job_name, "position_method": "onboard_gps_location_only",
                                 "reason": found.reason}}
        points = found if kind == "surveyed" else found.reference_points
        method = "chainage_between_reference_points" if kind == "surveyed" else "onboard_gps_track"
        extra = () if kind == "surveyed" else (found.reason,)
        return georef.line_features(job_name, length, georef.place(points), _targets(job_name, job_dir, channels),
                                    position_method=method, extra_warnings=extra)
    except (ValueError, KeyError, session.JobNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/jobs/{job_name}/geo.geojson")
def line_geojson(request: Request, job_name: str, download: bool = False) -> Response:
    return _geojson(line_collection(request, job_name), f"{job_name}.geojson" if download else None)


def _scope(request: Request, scope: str) -> tuple[dict[str, Any], str]:
    """("all" | "vendor" | "lines" | a job name) -> (collection, file stem)."""
    if scope == "vendor":
        return vendor_collection(request), "vendor_utilities"
    if scope in ("all", "lines"):
        payload = all_collection(request)
        if scope == "lines":
            payload["features"] = [f for f in payload["features"] if f["properties"]["kind"] != "vendor_callout"]
        return payload, "gpr_survey" if scope == "all" else "radar_lines"
    return line_collection(request, scope), scope


@router.get("/api/geo/export.kml")
def export_kml(request: Request, scope: str = "all") -> Response:
    collection, stem = _scope(request, scope)
    return Response(geo_export.to_kml(collection, f"GPR Studio — {stem}"),
                    media_type="application/vnd.google-earth.kml+xml",
                    headers={"Content-Disposition": f'attachment; filename="{_safe_filename(stem)}.kml"'})


@router.get("/api/geo/export.dxf")
def export_dxf(request: Request, scope: str = "lines") -> Response:
    collection, stem = _scope(request, scope)
    try:
        content, epsg = geo_export.to_dxf(collection)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(content, media_type="application/dxf",
                    headers={"Content-Disposition": f'attachment; filename="{_safe_filename(stem)}_EPSG{epsg}.dxf"'})


@router.get("/api/geo/all.geojson")
def all_geojson(request: Request) -> Response:
    """Everything that can be placed — vendor call-outs and every georeferenced line — in one file."""
    return _geojson(all_collection(request), "gpr_survey.geojson")


def all_collection(request: Request) -> dict[str, Any]:
    collection = vendor_collection(request)
    features = list(collection["features"])
    lines: dict[str, Any] = {}
    for job in session.list_jobs(_dataset_dir(request)):
        # One unreadable georef.json must cost only its own line, not the whole export.
        try:
            line = line_collection(request, job)
        except HTTPException as exc:
            lines[job] = {"error": exc.detail}
            continue
        if line["features"]:
            features.extend(line["features"])
            lines[job] = line["metadata"]
    return {"type": "FeatureCollection", "features": features,
            "metadata": {"vendor": collection.get("metadata"), "lines": lines}}
