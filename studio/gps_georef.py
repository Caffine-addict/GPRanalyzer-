"""The onboard GPS as a fallback way onto the map — used only as far as it is measured to work.

Each line's `Single-01.gps` holds NMEA fixes, each tagged with the trace it was logged at, so a
fix ties a real map position to a chainage: exactly what a reference point is. But the receiver
is measured unreliable (docs/pilot/GPS_DIAGNOSTIC.md): on three of the four delivered lines the
position barely moves while the wheel covers ~9.5 m. So each line is classed by what its own
fixes can support, and nothing more:

- **track** — the GPS moved at least `MIN_MOVEMENT` of the wheel distance. The line is placed
  from three of its fixes (start, middle, end), and the reference points' accuracy is the
  largest disagreement between GPS distance and wheel chainage over every fix on the line —
  measured, not assumed. The receiver's absolute accuracy is not known and is said to be.
- **location** — the GPS logged a position but did not follow the line. The survey happened
  near there, so the line is shown as a circle of possible positions (its own length plus the
  fixes' spread) with no direction — never as a guessed line.
- **none** — no usable fixes.

Surveyed reference points (studio/georef.py) always override this.
"""

from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from pathlib import Path

from core.geo import distance_m
from studio.georef import ReferencePoint

GPS_FILE = "Single-01.gps"
MIN_MOVEMENT = 0.8  # GPS start→end must cover this fraction of the wheel distance to be a track
SOURCE = "onboard GPS (Single-01.gps) — logged, not surveyed"


@dataclass(frozen=True)
class Fix:
    trace: int
    lat: float
    lon: float


@dataclass(frozen=True)
class GpsAssessment:
    kind: str  # "track" | "location" | "none"
    reason: str
    fixes: int
    movement_ratio: float | None  # GPS start→end distance / wheel chainage between the same fixes
    max_disagreement_m: float | None  # worst |GPS distance from first fix − wheel chainage|
    reference_points: list[ReferencePoint]  # for a track
    centre: tuple[float, float] | None  # (lat, lon), for a location
    radius_m: float | None  # for a location


def read_fixes(job_dir: Path) -> list[Fix]:
    """Fixes from the job's GPS log; malformed rows are skipped, never repaired."""
    path = job_dir / GPS_FILE
    if not path.exists():
        return []
    fixes = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for row in csv.reader(handle):
            try:
                fix = Fix(trace=int(row[1]), lat=float(row[2]), lon=float(row[3]))
            except (IndexError, ValueError):
                continue
            if -90 <= fix.lat <= 90 and -180 <= fix.lon <= 180 and (fix.lat, fix.lon) != (0.0, 0.0):
                fixes.append(fix)
    return sorted(fixes, key=lambda f: f.trace)


def assess(fixes: list[Fix], trace_spacing_m: float, line_length_m: float) -> GpsAssessment:
    if len(fixes) < 2:
        return GpsAssessment("none", "fewer than two GPS fixes", len(fixes), None, None, [], None, None)
    first, last = fixes[0], fixes[-1]
    wheel = (last.trace - first.trace) * trace_spacing_m
    moved = distance_m(first.lat, first.lon, last.lat, last.lon)
    ratio = moved / wheel if wheel > 0 else None
    disagreement = max(abs(distance_m(first.lat, first.lon, f.lat, f.lon) - (f.trace - first.trace) * trace_spacing_m)
                       for f in fixes)
    if ratio is not None and ratio >= MIN_MOVEMENT:
        middle = min(fixes, key=lambda f: abs(f.trace - (first.trace + last.trace) / 2))
        accuracy = max(round(disagreement, 2), 0.01)
        chosen = [first, middle, last] if middle not in (first, last) else [first, last]
        points = [ReferencePoint(chainage_m=round(f.trace * trace_spacing_m, 3), lat=f.lat, lon=f.lon,
                                 accuracy_m=accuracy, source=SOURCE) for f in chosen]
        reason = (f"GPS covered {moved:.2f} m of {wheel:.2f} m wheel distance; GPS and wheel disagree by up to "
                  f"{disagreement:.2f} m along the line. The receiver's absolute accuracy is unverified.")
        return GpsAssessment("track", reason, len(fixes), round(ratio, 3), round(disagreement, 2), points, None, None)
    spread = max(distance_m(first.lat, first.lon, f.lat, f.lon) for f in fixes)
    centre = (statistics.fmean(f.lat for f in fixes), statistics.fmean(f.lon for f in fixes))
    reason = (f"GPS moved only {moved:.2f} m while the wheel covered {wheel:.2f} m — the position froze, so the "
              "line's direction is unknown. The survey happened within the circle shown.")
    return GpsAssessment("location", reason, len(fixes), None if ratio is None else round(ratio, 3),
                         round(disagreement, 2), [], centre, round(line_length_m + spread, 2))
