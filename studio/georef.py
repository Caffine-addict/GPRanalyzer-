"""Putting a radar line on the map from surveyed reference points (docs/GEOSPATIAL_PLAN.md phase 3).

The onboard GPS is measured broken (docs/pilot/GPS_DIAGNOSTIC.md), so a line is placed the way
docs/SINGLE_PASS_MAPPING.md §d settled: two or more points on the ground whose map position is
known, each tied to a chainage along the line. Everything between them is placed by
interpolation; nothing outside them is placed at all.

Two checks travel with every placed line, because both are things this method can get wrong:

- **Scale.** The surveyed distance between the outermost points should equal their chainage
  difference. A mismatch means the wheel encoder or the survey is off; it is reported, not hidden.
- **Straightness.** Interpolation assumes the antenna went in a straight line. With three or
  more points, each interior one's sideways offset from the line through its neighbours is the
  measured straightness error. With two, it is unchecked and the features say so.

Stored in annotations/<job>/georef.json with the same lock and atomic write as the picks.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.annotation_io import job_lock, safe_job_dir, write_text_atomically
from core.geo import distance_m, interpolate, to_local

_ANNOTATIONS_ROOT = Path("annotations")
SCALE_WARNING = 0.03  # wheel and survey disagreeing by more than 3% is worth a flag
_MAX_ACCURACY_M = 100.0
_LINE_STEP_M = 1.0


@dataclass(frozen=True)
class ReferencePoint:
    chainage_m: float
    lat: float
    lon: float
    accuracy_m: float  # how well the point itself is known on the ground
    source: str  # e.g. "total station", "RTK", "site plan"


@dataclass(frozen=True)
class Placement:
    anchors: list[tuple[float, float, float]]  # (chainage, lat, lon)
    scale_ratio: float  # surveyed distance / chainage difference, outermost points
    straightness_m: float | None  # worst interior miss; None with only two points
    accuracy_m: float  # worst reference-point accuracy

    @property
    def position_error_m(self) -> float:
        return round(self.accuracy_m + (self.straightness_m or 0.0), 2)

    def warnings(self) -> list[str]:
        notes = []
        if abs(self.scale_ratio - 1) > SCALE_WARNING:
            notes.append(f"surveyed distance and wheel chainage differ by {abs(self.scale_ratio - 1) * 100:.1f}%")
        if self.straightness_m is None:
            notes.append("straightness unchecked — add a third reference point near the middle of the line")
        return notes


def _job_dir(job_name: str) -> Path:
    return safe_job_dir(_ANNOTATIONS_ROOT, job_name)


def _path(job_name: str) -> Path:
    return _job_dir(job_name) / "georef.json"


def validate(raw: Any, line_length_m: float) -> list[ReferencePoint]:
    """Reference points from an untrusted payload. Raises ValueError saying what is wrong."""
    if not isinstance(raw, list) or len(raw) < 2:
        raise ValueError("give at least two reference points")
    points = []
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"point {i}: expected an object")  # noqa: TRY004 - surfaced to the client as a 422
        try:
            point = ReferencePoint(chainage_m=float(item["chainage_m"]), lat=float(item["lat"]), lon=float(item["lon"]),
                                   accuracy_m=float(item["accuracy_m"]), source=str(item.get("source", "")).strip())
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"point {i}: needs chainage_m, lat, lon and accuracy_m as numbers") from exc
        if not all(math.isfinite(v) for v in (point.chainage_m, point.lat, point.lon, point.accuracy_m)):
            raise ValueError(f"point {i}: numbers must be finite")
        if not -90 <= point.lat <= 90 or not -180 <= point.lon <= 180:
            raise ValueError(f"point {i}: latitude must be within ±90 and longitude within ±180")
        if not -1.0 <= point.chainage_m <= line_length_m + 1.0:
            raise ValueError(f"point {i}: chainage {point.chainage_m} m is off this {line_length_m:.1f} m line")
        if not 0 < point.accuracy_m <= _MAX_ACCURACY_M:
            raise ValueError(f"point {i}: accuracy must be above 0 and at most {_MAX_ACCURACY_M:.0f} m")
        if not point.source or len(point.source) > 200:
            raise ValueError(f"point {i}: say where the point came from (e.g. total station, RTK, site plan)")
        points.append(point)
    chainages = [p.chainage_m for p in points]
    if len(set(chainages)) != len(chainages):
        raise ValueError("two reference points share a chainage")
    return sorted(points, key=lambda p: p.chainage_m)


def place(points: list[ReferencePoint]) -> Placement:
    anchors = [(p.chainage_m, p.lat, p.lon) for p in points]
    first, last = anchors[0], anchors[-1]
    surveyed = distance_m(first[1], first[2], last[1], last[2])
    scale = surveyed / (last[0] - first[0])
    # Straightness is the SIDEWAYS offset of each interior point from the line through its
    # neighbours. The along-line part of any miss is a scale error, already reported above;
    # counting it here too would blend the two (a synthetic 0.30 m bow plus a 5% scale error
    # read as a 0.38 m "bow" before this split).
    misses = []
    for i in range(1, len(anchors) - 1):
        (_, lat0, lon0), (_, lat1, lon1), (_, lat2, lon2) = anchors[i - 1], anchors[i], anchors[i + 1]
        ve, vn = to_local(lat2, lon2, lat0, lon0)
        we, wn = to_local(lat1, lon1, lat0, lon0)
        span = math.hypot(ve, vn)
        if span > 0:
            misses.append(abs(ve * wn - vn * we) / span)
    return Placement(anchors, round(scale, 4), round(max(misses), 2) if misses else None,
                     max(p.accuracy_m for p in points))


def load(job_name: str) -> list[ReferencePoint] | None:
    path = _path(job_name)
    if not path.exists():
        return None
    try:
        return [ReferencePoint(**p) for p in json.loads(path.read_text())["reference_points"]]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path} is not a valid georeference file: {exc}") from exc


def save(job_name: str, points: list[ReferencePoint]) -> None:
    with job_lock(_job_dir(job_name)):
        payload = {"reference_points": [asdict(p) for p in points],
                   "updated_at": datetime.now(UTC).isoformat(timespec="seconds")}
        write_text_atomically(_path(job_name), json.dumps(payload, indent=2) + "\n")


def _point(lon_lat: tuple[float, float], properties: dict[str, Any]) -> dict[str, Any]:
    lat, lon = lon_lat
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]},
            "properties": properties}


def line_features(job: str, line_length_m: float, placement: Placement,
                  targets: list[dict[str, Any]],
                  position_method: str = "chainage_between_reference_points",
                  extra_warnings: tuple[str, ...] = ()) -> dict[str, Any]:
    """GeoJSON for one line: its track, and every target that falls between the reference points.

    `targets` are dicts with at least kind, id, chainage_m; anything else is passed through as
    properties. A target outside the reference points is counted, not placed.
    """
    warnings = [*placement.warnings(), *extra_warnings]
    common = {"position_method": position_method, "position_confidence": "estimated",
              "position_error_m": placement.position_error_m, "warnings": warnings}
    start, end = placement.anchors[0][0], placement.anchors[-1][0]
    steps = max(1, int((end - start) / _LINE_STEP_M))
    track = [interpolate(start + (end - start) * i / steps, placement.anchors) for i in range(steps + 1)]
    features: list[dict[str, Any]] = [{
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[round(p[1], 7), round(p[0], 7)] for p in track if p]},
        "properties": {"kind": "radar_line", "id": job, "source": job, "chainage_start_m": start,
                       "chainage_end_m": end, "line_length_m": round(line_length_m, 2),
                       "scale_ratio": placement.scale_ratio, "straightness_m": placement.straightness_m, **common},
    }]
    outside = 0
    for target in targets:
        position = interpolate(float(target["chainage_m"]), placement.anchors)
        if position is None:
            outside += 1
            continue
        features.append(_point(position, {**target, "source": job, **common}))
    return {"type": "FeatureCollection", "features": features,
            "metadata": {"job": job, "targets_outside_reference_points": outside,
                         "position_method": position_method,
                         "covered_chainage_m": [start, end], "warnings": warnings}}
