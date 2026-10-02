"""Vendor SUE call-outs on the map: placed from the Lat/Long the vendor printed on each sheet.

Phase 1 of docs/GEOSPATIAL_PLAN.md. Every sheet prints a Lat/Long at (or beside) its start and
end, and `reference.sue_sheets` gives each of those points a chainage. A call-out at chainage c
is placed by interpolating between the printed points either side of it.

How good that is, is measured rather than assumed, per drawing:

- **Hold-out error.** Each interior printed point is predicted from its neighbours and the miss
  recorded. Its 90th percentile becomes the drawing's `position_error_m` — the error of
  interpolating along this road with points this far apart. It does not include the label
  offset (a call-out's chainage is that of its label, a few metres from the utility).
- **Path check.** The distance walked along the points must match the chainage they span. Where
  it does not (extra points off the centre line, points out of order, missing sheets) the whole
  drawing is `position_confidence: "unavailable"` and no call-out from it is placed.
"""

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from core.geo import distance_m, interpolate

PATH_TOLERANCE = 0.05  # walked length may differ from chainage span by this fraction
_SAME_POINT_M = 1.0  # printed points closer than this along the chainage are one point


@dataclass(frozen=True)
class DrawingGeo:
    drawing: str
    anchors: list[tuple[float, float, float]]  # (chainage, lat, lon), sorted, de-duplicated
    path_ratio: float | None  # walked length / chainage span
    holdout_p90_m: float | None
    usable: bool
    reason: str


def _anchors(rows: list[dict[str, str]]) -> list[tuple[float, float, float]]:
    points = sorted((float(r["chainage_m"]), float(r["lat"]), float(r["lon"])) for r in rows if r.get("chainage_m"))
    kept: list[tuple[float, float, float]] = []
    for point in points:
        if kept and point[0] - kept[-1][0] < _SAME_POINT_M:
            continue
        kept.append(point)
    return kept


def _holdout_errors(anchors: list[tuple[float, float, float]]) -> list[float]:
    errors = []
    for i in range(1, len(anchors) - 1):
        chainage, lat, lon = anchors[i]
        predicted = interpolate(chainage, [anchors[i - 1], anchors[i + 1]])
        if predicted is not None:
            errors.append(distance_m(lat, lon, *predicted))
    return errors


def assess(drawing: str, rows: list[dict[str, str]]) -> DrawingGeo:
    """Whether a drawing's printed points can place its call-outs, and how well."""
    anchors = _anchors(rows)
    if len(anchors) < 2:
        return DrawingGeo(drawing, anchors, None, None, False, "fewer than two printed points with a chainage")
    walked = sum(distance_m(a[1], a[2], b[1], b[2]) for a, b in pairwise(anchors))
    span = anchors[-1][0] - anchors[0][0]
    ratio = walked / span if span > 0 else None
    errors = _holdout_errors(anchors)
    p90 = round(sorted(errors)[int(0.9 * (len(errors) - 1))], 1) if errors else None
    if ratio is None or abs(ratio - 1) > PATH_TOLERANCE:
        reason = (f"walking the printed points covers {walked:.0f} m for a {span:.0f} m chainage span — "
                  "points off the centre line or out of order")
        return DrawingGeo(drawing, anchors, ratio, p90, False, reason)
    return DrawingGeo(drawing, anchors, round(ratio, 3), p90, True, "")


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def feature_collection(utilities: list[dict[str, str]], points: list[dict[str, str]]) -> dict[str, Any]:
    """GeoJSON (RFC 7946, lon/lat) of every call-out that can be placed, with per-drawing checks.

    Call-outs that cannot be placed (drawing failed its path check, no chainage, or a chainage
    outside the printed points) are counted in the metadata, never given a guessed position.
    """
    points_by: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in points:
        points_by[row["drawing"]].append(row)
    drawings = {name: assess(name, rows) for name, rows in points_by.items()}

    features: list[dict[str, Any]] = []
    unplaced: defaultdict[str, int] = defaultdict(int)
    for row in utilities:
        geo = drawings.get(row["drawing"])
        why = None
        if geo is None:
            why = "no printed points on this drawing"
        elif not geo.usable:
            why = "drawing failed its path check"
        elif not row.get("chainage_m"):
            why = "no chainage"
        position = None
        if why is None and geo is not None:
            position = interpolate(float(row["chainage_m"]), geo.anchors)
            if position is None:
                why = "chainage outside the printed points"
        if position is None or geo is None:
            unplaced[why or "unknown"] += 1
            continue
        lat, lon = position
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]},
            "properties": {
                "kind": "vendor_callout",
                "source": f"{row['drawing']} · sheet {row['sheet']}",
                "drawing": row["drawing"], "sheet": int(row["sheet"]),
                "utility": row["utility"],
                "depth_m": float(row["depth_m"]) if row.get("depth_m") else None,
                "depth_basis": "vendor-stated, ±30%",
                "chainage_m": float(row["chainage_m"]),
                "chainage_source": row.get("chainage_source"),
                "position_method": "interpolated_from_printed_latlong",
                "position_confidence": "estimated",
                "position_error_m": geo.holdout_p90_m,
                "status": row.get("status"),
                "read_by": row.get("read_by", "text"),
                "review_status": None,
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "position_error_note": "position_error_m is the drawing's measured 90th-percentile hold-out error of "
                                   "interpolating between its printed points; add a few metres for the label "
                                   "offset, which is not measured",
            "unplaced": dict(unplaced),
            "drawings": [{"drawing": g.drawing, "points": len(g.anchors), "path_ratio": g.path_ratio,
                          "holdout_p90_m": g.holdout_p90_m, "usable": g.usable, "reason": g.reason}
                         for g in drawings.values()],
        },
    }


def summary(collection: dict[str, Any]) -> str:
    errors = [d["holdout_p90_m"] for d in collection["metadata"]["drawings"] if d["usable"] and d["holdout_p90_m"]]
    median = statistics.median(errors) if errors else None
    return (f"{len(collection['features'])} call-outs placed; unplaced {collection['metadata']['unplaced']}; "
            f"median per-drawing p90 hold-out error {median} m")
