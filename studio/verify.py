"""Verify the detector's targets one at a time, to build real training labels (docs/TRAINING_PLAN.md).

A person sees each merged picker target on all three channels and says yes (a real apex), no
(nothing there: a hard negative) or unsure. Decisions go to Postgres under the signed-in account.

Only training sites are offered. The sites that hold the operator tags (simulate.composite.TEST_SITES)
are the held-out test: anyone who had verified targets there would have seen the test.
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse

from detect.migration_picker import time_zero_ns
from simulate.composite import TEST_SITES
from store.label_store import LabelStore
from studio import session, site_map

router = APIRouter()
_STATIC_DIR = Path(__file__).resolve().parent / "static"
DECISIONS = ("yes", "no", "unsure")
GOAL = 150  # verified apexes before fine-tuning is worth trying (docs/TRAINING_PLAN.md)


def _store(request: Request) -> LabelStore:
    store = getattr(request.app.state, "labels", None)
    if store is None:
        raise HTTPException(status_code=503, detail="label storage is not set up on this server")
    return store


def _dataset_dir(request: Request) -> Path:
    return getattr(request.app.state, "dataset_dir", session.DEFAULT_DATASET_DIR)


def _training_sites(dataset_dir: Path) -> Counter[str]:
    return Counter(site_map.site_of(j) for j in session.list_jobs(dataset_dir)
                   if "_" in j and site_map.site_of(j) not in TEST_SITES)


@lru_cache(maxsize=256)
def _time_zeros(job_dir_str: str, _mtime_ns: int) -> tuple[tuple[str, float], ...]:
    job_dir = Path(job_dir_str)
    return tuple((info.extension, time_zero_ns(session.raw_traces(session.load_frame(job_dir, info.extension)),
                                               info.sample_interval_ns))
                 for info in session.available_channels(job_dir))


def _targets(dataset_dir: Path, job: str) -> list[dict[str, Any]]:
    """The line's merged picker targets, each with where to mark it on every channel."""
    job_dir = session.resolve_job(job, dataset_dir)
    mtime = site_map._mtime(job_dir)
    first = session.available_channels(job_dir)[0]
    spacing = first.trace_spacing_m
    length_m = round((session.raw_traces(session.load_frame(job_dir, first.extension)).shape[0] - 1) * spacing, 3)
    zeros = _time_zeros(str(job_dir), mtime)
    return [{"job": job, "trace": int(trace), "time_ns": round(float(time_ns), 3),
             "chainage_m": round(float(trace) * spacing, 3), "line_length_m": length_m, "seen_on": list(channels),
             "channels": [{"extension": ext, "mark_ns": round(float(time_ns) + zero, 3)} for ext, zero in zeros]}
            for trace, time_ns, channels, _header in site_map._detected(str(job_dir), mtime)]


def _site(request: Request, site: str) -> list[str]:
    if site in TEST_SITES:
        raise HTTPException(status_code=422, detail=f"{site} holds the test targets and is not offered for verifying")
    jobs = [j for j in site_map.site_jobs(_dataset_dir(request), site)]
    if not jobs:
        raise HTTPException(status_code=404, detail=f"no site called {site}")
    return jobs


def _key(job: str, trace: int, time_ns: float) -> tuple[str, int, float]:
    return job, int(trace), round(float(time_ns), 3)


@router.get("/verify", response_class=HTMLResponse)
def verify_page() -> HTMLResponse:
    return HTMLResponse((_STATIC_DIR / "verify.html").read_text())


@router.get("/api/verify/sites")
def verify_sites(request: Request) -> dict[str, Any]:
    sites = _training_sites(_dataset_dir(request))
    return {"sites": [{"site": s, "lines": n} for s, n in sorted(sites.items())], "held_out": sorted(TEST_SITES)}


@router.get("/api/verify/queue")
def verify_queue(request: Request, site: str) -> dict[str, Any]:
    jobs = _site(request, site)
    decided = {_key(lab.job, lab.trace, lab.time_ns): lab for lab in _store(request).latest(jobs)}
    items = []
    for job in jobs:
        for target in _targets(_dataset_dir(request), job):
            label = decided.get(_key(job, target["trace"], target["time_ns"]))
            items.append({**target, "decision": label.decision if label else None,
                          "decided_by": label.username if label else None})
    return {"site": site, "items": items}


@router.post("/api/verify/labels")
def verify_label(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008 - FastAPI idiom
    job, decision = str(payload.get("job", "")), payload.get("decision")
    if decision not in DECISIONS:
        raise HTTPException(status_code=422, detail=f"decision must be one of {', '.join(DECISIONS)}")
    try:
        trace, time_ns = int(payload["trace"]), float(payload["time_ns"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="trace and time_ns are required numbers") from exc
    if site_map.site_of(job) in TEST_SITES:
        raise HTTPException(status_code=422, detail="that line is on a held-out test site")
    try:
        targets = _targets(_dataset_dir(request), job)
    except session.JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="no such line") from exc
    if not any(_key(job, t["trace"], t["time_ns"]) == _key(job, trace, time_ns) for t in targets):
        raise HTTPException(status_code=422, detail="that is not one of this line's targets; reload the queue")
    _store(request).record(job, trace, round(time_ns, 3), decision, request.state.user.user_id)
    return {"job": job, "trace": trace, "time_ns": round(time_ns, 3), "decision": decision}


@router.get("/api/verify/progress")
def verify_progress(request: Request) -> dict[str, Any]:
    dataset_dir = _dataset_dir(request)
    jobs = [j for j in session.list_jobs(dataset_dir) if "_" in j and site_map.site_of(j) not in TEST_SITES]
    counts = Counter(lab.decision for lab in _store(request).latest(jobs))
    return {"yes": counts["yes"], "no": counts["no"], "unsure": counts["unsure"], "goal": GOAL}
