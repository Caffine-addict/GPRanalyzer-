"""What the crew found on site, per dig item: the excavation truth the project has never had.

Every report records the outcome, who reported it, when, and where the phone was (with its own GPS
accuracy), so a report can be weighed later rather than taken on trust. Stored per line in
`annotations/<job>/field_reports.json` with the same lock and atomic write as picks and reviews.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from core.annotation_io import job_lock, safe_job_dir, write_text_atomically

_ANNOTATIONS_ROOT = Path("annotations")
OUTCOMES = ("found", "not_found", "dug")
_MAX_TEXT = 500


@dataclass(frozen=True)
class FieldReport:
    id: str
    item_id: str  # the dig item, "<job>#<review or pick id>"
    outcome: str
    reporter: str
    note: str
    reported_at: str
    lat: float | None  # where the phone was, if it shared its position
    lon: float | None
    accuracy_m: float | None  # the phone's own estimate of its GPS error
    depth_found_m: float | None = None  # measured in the hole, when dug


def _path(job: str) -> Path:
    return safe_job_dir(_ANNOTATIONS_ROOT, job) / "field_reports.json"


def load_reports(job: str) -> list[FieldReport]:
    path = _path(job)
    if not path.exists():
        return []
    try:
        return [FieldReport(**r) for r in json.loads(path.read_text())["reports"]]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path} is not a valid field-reports file: {exc}") from exc


def _number(value: object, name: str, low: float, high: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a number between {low} and {high}")
    return float(value)


def add_report(job: str, *, item_id: str, outcome: str, reporter: str, note: str = "", lat: object = None,
               lon: object = None, accuracy_m: object = None, depth_found_m: object = None) -> FieldReport:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}")
    if not item_id.startswith(f"{job}#"):
        raise ValueError("the item does not belong to this line")
    if not reporter.strip():
        raise ValueError("a field report needs the reporter's name")
    if len(reporter) > _MAX_TEXT or len(note) > _MAX_TEXT:
        raise ValueError(f"name and note are limited to {_MAX_TEXT} characters")
    report = FieldReport(
        id=f"F{uuid.uuid4().hex[:8]}", item_id=item_id, outcome=outcome, reporter=reporter.strip(), note=note.strip(),
        reported_at=datetime.now(UTC).isoformat(timespec="seconds"),
        lat=_number(lat, "lat", -90, 90), lon=_number(lon, "lon", -180, 180),
        accuracy_m=_number(accuracy_m, "accuracy_m", 0, 10_000), depth_found_m=_number(depth_found_m, "depth_found_m", 0, 20))
    with job_lock(_path(job).parent):
        reports = load_reports(job)
        write_text_atomically(_path(job), json.dumps({"reports": [asdict(r) for r in [*reports, report]]}, indent=2) + "\n")
    return report


def latest_outcomes(job: str) -> dict[str, FieldReport]:
    """The most recent report per item (reports are appended in order)."""
    return {r.item_id: r for r in load_reports(job)}
