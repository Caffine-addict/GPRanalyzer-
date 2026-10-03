"""HTTP endpoints for the reasoning layers and the supervisor's reviews (see studio/assistant.py).

A router of its own rather than more of studio/server.py: the server was already the largest
module in the Studio, and these endpoints share nothing with the imaging ones but job lookup.
"""

from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from core import boxes as box_store
from core.config import Config, load_config
from reason.engine import GroqClient, LLMClient
from studio import assistant, render, review_report, session
from studio import candidates as candidate_store
from studio import picks as pick_store
from studio import reviews as review_store
from studio.processing import chain_from_params, run_chain

router = APIRouter()
_CACHE_LIMIT = 128


def _dataset_dir(request: Request) -> Path:
    return getattr(request.app.state, "dataset_dir", session.DEFAULT_DATASET_DIR)


def _job(request: Request, job_name: str) -> Path:
    try:
        return session.resolve_job(job_name, _dataset_dir(request))
    except session.JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _config(request: Request) -> Config:
    config = getattr(request.app.state, "config", None)
    if config is None:
        config = load_config()
        request.app.state.config = config
    return config


def _client(request: Request) -> LLMClient:
    """The model client, or a 503 saying exactly what is missing (tests inject their own)."""
    client = getattr(request.app.state, "assistant_client", None)
    if client is not None:
        return client
    load_dotenv()
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="reasoning is not configured — put GROQ_API_KEY in .env "
                                                    "(see .env.example), then restart the Studio")
    client = GroqClient(api_key)
    request.app.state.assistant_client = client
    return client


def _models(config: Config) -> list[str]:
    reasoning = config.reasoning
    return [reasoning.model] + ([reasoning.fallback_model] if reasoning.fallback_model
                                and reasoning.fallback_model != reasoning.model else [])


def _processed_dir(request: Request) -> Path | None:
    """The survey's `_processed` folder (vendor drawings and radargram checks), if one exists."""
    return next(iter(sorted(_dataset_dir(request).rglob("_processed"))), None)


def _cache(request: Request) -> dict[str, dict]:
    cache = getattr(request.app.state, "assistant_cache", None)
    if cache is None:
        cache = request.app.state.assistant_cache = {}
    return cache


def _ask(request: Request, layer: str, context: dict[str, Any], targets: list[dict[str, Any]],
         question: str, history: list[dict[str, str]]) -> dict[str, Any]:
    config = _config(request)
    context_text = json.dumps(context, indent=1, default=str)
    key = assistant.fingerprint(layer, context_text, question, history)
    cached = _cache(request).get(key)
    if cached is not None:
        return {**cached, "cached": True}
    reply = assistant.run(_client(request), _models(config), layer, context, targets, question, history,
                          temperature=config.reasoning.temperature)
    result = {**asdict(reply), "layer": layer, "cached": False}
    if reply.error is None:
        cache = _cache(request)
        if len(cache) >= _CACHE_LIMIT:
            cache.pop(next(iter(cache)))
        cache[key] = result
    return result


_HISTORY_ROLES = ("operator", "assistant")
_MAX_HISTORY_SENT = 20


def _history(body: dict[str, Any]) -> list[dict[str, str]]:
    """The chat so far, as the browser recorded it.

    Client-supplied and unverified: a caller can put words in the "assistant" role. Accepted on a
    loopback, single-operator tool — what a forged turn can move is prose, never a circle (claims
    must name a measured target) or a material's confidence (capped server-side) — and the prompt
    tells the model the transcript is unverified.
    """
    history = body.get("history") or []
    if not isinstance(history, list):
        raise HTTPException(status_code=422, detail="history must be a list of {role, text}")
    turns = [t for t in history[-_MAX_HISTORY_SENT:] if isinstance(t, dict) and t.get("role") in _HISTORY_ROLES]
    return [{"role": str(t["role"]), "text": str(t.get("text", ""))[:2000]} for t in turns]


@router.post("/api/jobs/{job_name}/channels/{extension}/assistant")
def ask_about_line(request: Request, job_name: str, extension: str,
                   body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008 - FastAPI idiom
    """One reasoning layer over one channel of one line. Named targets become proposed reviews."""
    layer = str(body.get("layer", "chat"))
    if layer not in assistant.LAYERS or layer == "briefing":
        raise HTTPException(status_code=422, detail="layer must be one of chat, line_summary, what_is_what, live")
    question = str(body.get("question", ""))[:2000]
    if layer == "chat" and not question.strip():
        raise HTTPException(status_code=422, detail="ask a question")
    job_dir = _job(request, job_name)
    try:
        frame = session.load_frame(job_dir, extension)
        info = session.describe_channel(frame, extension)
        traces = session.raw_traces(frame)
        candidates = candidate_store.load_candidates(job_name, job_dir)
        picks = pick_store.load_picks(job_name)
    except (session.JobNotFoundError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    depth_limit = assistant.usable_depth(traces, info)  # computed once, used for both the flags and the context
    targets = assistant.line_targets(candidates, picks, info, traces, depth_limit)
    processed = _processed_dir(request)
    priors = assistant.vendor_priors(processed / "sue_utilities.csv") if processed else None
    context = assistant.line_context(job_name, info, targets, priors, depth_limit)
    result = _ask(request, layer, context, targets, question, _history(body))

    claims = [{"channel": extension, "target_id": m["target_ref"], "target_kind": m["target_kind"],
               "x": m["x"], "y": m["y"], "w": m["w"], "h": m["h"], "identity": m["identity"],
               "material": m["material"], "confidence": m["confidence"], "why": m["why"],
               "layer": layer, "model": result["model"] or ""} for m in result["mentions"]]
    reviews = review_store.propose(job_name, claims)
    labels = {t["ref"]: t["id"] for t in targets}
    return {**result, "reviews": [{**asdict(r), "label": labels.get(r.target_id, "")} for r in reviews]}


@router.post("/api/assistant/briefing")
def survey_briefing(request: Request) -> dict[str, Any]:
    """A briefing across the whole dataset. No target claims: it is about the survey, not an object."""
    jobs: dict[str, dict[str, Any]] = {}
    for job in session.list_jobs(_dataset_dir(request)):
        try:
            reviews = review_store.load_reviews(job)
            picks = pick_store.load_picks(job)
        except ValueError as exc:
            jobs[job] = {"error": str(exc)}
            continue
        jobs[job] = {"detector_candidates": len(box_store.load_boxes(job)), "interpreter_picks": len(picks),
                     "model_claims": {s: sum(r.status == s for r in reviews) for s in review_store.STATUSES}}
    processed = _processed_dir(request)
    context = assistant.survey_context(jobs, processed) if processed else {"radar_lines": jobs}
    result = _ask(request, "briefing", context, [], "", [])
    request.app.state.last_briefing = result  # what briefing.pdf prints
    return result


@router.get("/api/assistant/briefing.pdf")
def briefing_pdf(request: Request) -> Response:
    reply = getattr(request.app.state, "last_briefing", None)
    if reply is None:
        raise HTTPException(status_code=404, detail="no briefing yet — ask for one in the Assistant panel first")
    return Response(review_report.briefing_pdf(reply), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="survey_briefing.pdf"'})


@router.get("/api/jobs/{job_name}/channels/{extension}/review_report.pdf")
def line_review_report(request: Request, job_name: str, extension: str) -> Response:
    """The supervisor's sign-off document for one channel: circled radargram and every decision."""
    job_dir = _job(request, job_name)
    try:
        radargram, info = session.load_radargram(job_dir, extension)
        raw = run_chain(radargram, chain_from_params({}), trace_spacing_m=info.trace_spacing_m,
                        sample_interval_ns=info.sample_interval_ns)
        rgb = render.to_rgb(raw, render.DisplaySettings())
        reviews = review_store.load_reviews(job_name)
    except (session.JobNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    pdf = review_report.review_report_pdf(job_name, info, rgb, reviews)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{job_name}_{extension}_review.pdf"'})


@router.get("/api/jobs/{job_name}/reviews")
def list_reviews(request: Request, job_name: str) -> list[dict[str, Any]]:
    _job(request, job_name)
    try:
        return [asdict(r) for r in review_store.load_reviews(job_name)]
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/jobs/{job_name}/reviews/{review_id}/decision")
def decide_review(request: Request, job_name: str, review_id: str,
                  body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008 - FastAPI idiom
    _job(request, job_name)
    try:
        review = review_store.decide(job_name, review_id, str(body.get("status", "")),
                                     str(body.get("reviewer", "")), str(body.get("note", ""))[:1000])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown review {review_id!r}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return asdict(review)


@router.get("/api/jobs/{job_name}/reviews.csv", response_class=PlainTextResponse)
def reviews_csv(request: Request, job_name: str) -> Response:
    _job(request, job_name)
    buffer = io.StringIO()
    csv.writer(buffer).writerows(review_store.to_csv_rows(review_store.load_reviews(job_name)))
    return Response(buffer.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{job_name}_reviews.csv"'})
