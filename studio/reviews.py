"""What the reasoning model said about a target, waiting for a supervisor to confirm or reject it.

A language model's reading of a radargram is a *proposal*, never a finding. Every time one of
the Studio's reasoning layers names a target ("T3 is probably a metallic pipe"), the claim is
stored here against that target's own measured box, drawn as a circle on the radargram, and
stays `proposed` until a named person confirms or rejects it. Reports print the status, so a
deliverable can always tell a supervisor-confirmed identification from a model's guess.

Claims are tied to targets that already exist — detector candidates or interpreter picks — by
id. The model is never allowed to place a circle: it can only point at something the Studio
measured, so every circle sits on real data.

Stored in `annotations/<job>/reviews.json`, with the same per-job lock and atomic write as the
picks, because a supervisor confirming while a layer proposes is exactly two writers at once.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.annotation_io import job_lock, safe_job_dir, write_text_atomically

_ANNOTATIONS_ROOT = Path("annotations")
STATUSES = ("proposed", "confirmed", "rejected")
_DECISIONS = ("confirmed", "rejected")


@dataclass(frozen=True)
class Review:
    id: str
    channel: str
    target_id: str
    target_kind: str  # "candidate" or "pick"
    x: float  # the target's box in native trace/sample units, copied when the claim was made
    y: float
    w: float
    h: float
    identity: str  # what the model says the object is
    material: str
    confidence: str
    why: str
    layer: str  # which reasoning layer said it
    model: str
    created_at: str
    status: str = "proposed"
    reviewer: str = ""
    note: str = ""
    decided_at: str = ""


def _job_dir(job_name: str) -> Path:
    return safe_job_dir(_ANNOTATIONS_ROOT, job_name)


def _path(job_name: str) -> Path:
    return _job_dir(job_name) / "reviews.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load_reviews(job_name: str) -> list[Review]:
    path = _path(job_name)
    if not path.exists():
        return []
    try:
        return [Review(**item) for item in json.loads(path.read_text())["reviews"]]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path} is not a valid reviews file: {exc}") from exc


def _save(job_name: str, reviews: list[Review]) -> None:
    write_text_atomically(_path(job_name), json.dumps({"reviews": [asdict(r) for r in reviews]}, indent=2) + "\n")


def propose(job_name: str, claims: list[dict[str, Any]]) -> list[Review]:
    """Store model claims as `proposed` reviews. Returns the reviews that now stand for them.

    A claim that repeats one already waiting (same target, same identity) is not stored twice:
    asking the same question again should not bury the supervisor in duplicates. A claim about a
    target that was already *decided* is stored as a new proposal — the decision stays on record,
    and the new reading waits for its own.
    """
    if not claims:
        return []
    with job_lock(_job_dir(job_name)):
        reviews = load_reviews(job_name)
        standing = []
        for claim in claims:
            existing = next((r for r in reviews if r.status == "proposed" and r.target_id == claim["target_id"]
                             and r.identity.lower() == str(claim["identity"]).lower()), None)
            if existing is not None:
                standing.append(existing)
                continue
            review = Review(id=f"R{uuid.uuid4().hex[:8]}", created_at=_now(),
                            **{k: claim[k] for k in ("channel", "target_id", "target_kind", "x", "y", "w", "h",
                                                     "identity", "material", "confidence", "why", "layer", "model")})
            reviews.append(review)
            standing.append(review)
        _save(job_name, reviews)
    return standing


def decide(job_name: str, review_id: str, status: str, reviewer: str, note: str = "") -> Review:
    """A supervisor confirms or rejects one claim. The reviewer's name is required."""
    if status not in _DECISIONS:
        raise ValueError(f"status must be one of {', '.join(_DECISIONS)}")
    if not reviewer.strip():
        raise ValueError("a decision needs the reviewer's name")
    with job_lock(_job_dir(job_name)):
        reviews = load_reviews(job_name)
        index = next((i for i, r in enumerate(reviews) if r.id == review_id), None)
        if index is None:
            raise KeyError(review_id)
        decided = replace(reviews[index], status=status, reviewer=reviewer.strip(), note=note.strip(),
                          decided_at=_now())
        _save(job_name, [*reviews[:index], decided, *reviews[index + 1:]])
    return decided


def to_csv_rows(reviews: list[Review]) -> list[list[str]]:
    header = ["review", "channel", "target", "identity", "material", "confidence", "status",
              "reviewer", "decided_at", "note", "said_by", "why"]
    rows = [[r.id, r.channel, r.target_id, r.identity, r.material, r.confidence, r.status,
             r.reviewer, r.decided_at, r.note, f"{r.layer} ({r.model})", r.why] for r in reviews]
    return [header, *rows]
