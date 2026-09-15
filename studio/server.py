"""GPR Studio — the interpretation workstation's HTTP layer.

Deliberately a separate app from `api/server.py`. That one serves the live
pipeline: surveys running now, findings streaming to operator/manager/PM
dashboards over WebSocket. This one is the *post-processing* side — open a
recorded line, work the processing chain, fit velocities, mark targets. They
share parsers and contracts and nothing else, because they answer to different
users at different times, and folding the two would drag the gated pipeline's
lifecycle into what is really a desktop tool.

It replaces `scripts/spr_viewer_server.py`, which rendered one fixed PNG per
job with no processing chain, no zoom, and no way to measure anything.

Every endpoint that renders or measures re-runs the chain from the raw traces
(see `studio/processing.py`) — there is no processed-state cache to fall out of
sync, and no way for the picture on screen to disagree with the numbers beside
it. At ~386 x 256 samples per line that costs single-digit milliseconds.
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from core.config import Config, load_config
from evidence.quality import quality_level
from reason.engine import GroqClient, ReasoningEngine
from reason.prompt import build_evidence_block
from reference import library as reference_library
from studio import candidates as candidate_store
from studio import corroborate as corroboration
from studio import interpret, render, session, velocity
from studio import palette as palette_module
from studio import picks as pick_store
from studio.processing import chain_from_params, run_chain

_STATIC_DIR = Path(__file__).resolve().parent / "static"

# Query keys that change how the radargram is *drawn*; everything else in the
# query string is a processing parameter and is validated as one.
_DISPLAY_KEYS = frozenset({"palette", "contrast", "brightness", "width", "height"})

app = FastAPI(title="GPR Studio")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


def _dataset_dir(request: Request) -> Path:
    return getattr(request.app.state, "dataset_dir", session.DEFAULT_DATASET_DIR)


def _job(request: Request, job_name: str) -> Path:
    try:
        return session.resolve_job(job_name, _dataset_dir(request))
    except session.JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _split_query(params: dict[str, str]) -> tuple[dict[str, Any], render.DisplaySettings]:
    """Separate display settings from processing parameters, validating both."""
    display_raw = {k: v for k, v in params.items() if k in _DISPLAY_KEYS}
    # Processing values stay as raw strings here: chain_from_params is the one
    # place they are validated and typed, including the "false"-is-truthy trap.
    processing_raw: dict[str, Any] = {k: v for k, v in params.items() if k not in _DISPLAY_KEYS}

    try:
        settings = render.DisplaySettings(
            palette=display_raw.get("palette", palette_module.DEFAULT_PALETTE),
            contrast_percentile=float(display_raw.get("contrast", 98.0)),
            brightness=float(display_raw.get("brightness", 0.0)),
            width_px=int(display_raw["width"]) if "width" in display_raw else None,
            height_px=int(display_raw["height"]) if "height" in display_raw else None,
        )
        palette_module.get(settings.palette)  # raises KeyError on an unknown name
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid display setting: {exc}") from exc
    return processing_raw, settings


def _processed(job_dir: Path, extension: str, processing: dict[str, Any]):
    """Raw traces -> processed radargram, plus the channel's axis metadata."""
    try:
        radargram, info = session.load_radargram(job_dir, extension)
        chain = chain_from_params(processing)
        processed = run_chain(
            radargram,
            chain,
            trace_spacing_m=info.trace_spacing_m,
            sample_interval_ns=info.sample_interval_ns,
        )
    except (session.JobNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return processed, info, chain


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((_STATIC_DIR / "index.html").read_text())


@app.get("/api/palettes")
def list_palettes() -> list[dict]:
    return [
        {"name": p.name, "label": p.label, "is_diverging": p.is_diverging}
        for p in palette_module.available()
    ]


@app.get("/api/jobs")
def list_jobs(request: Request) -> list[str]:
    return session.list_jobs(_dataset_dir(request))


@app.get("/api/jobs/{job_name}")
def job_detail(request: Request, job_name: str) -> dict:
    """Everything the client needs to lay out a job: channels, axes, GPS, header."""
    job_dir = _job(request, job_name)
    try:
        channels = session.available_channels(job_dir)
    except (session.JobNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "job": job_name,
        "channels": [vars(info) for info in channels],
        "gps": session.load_gps_track(job_dir),
        "header": session.job_header(job_dir),
        "axis_provenance": {
            "distance": session.DISTANCE_PROVENANCE,
            "depth": session.DEPTH_PROVENANCE,
        },
    }


@app.get("/api/jobs/{job_name}/channels/{extension}/image.png")
def channel_image(request: Request, job_name: str, extension: str) -> Response:
    """The processed radargram as a PNG, at whatever size the canvas asked for."""
    job_dir = _job(request, job_name)
    processing, settings = _split_query(dict(request.query_params))
    processed, _info, _chain = _processed(job_dir, extension, processing)
    try:
        image = render.to_rgb(processed, settings)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=render.encode_png(image),
        media_type="image/png",
        # The chain is fully described by the query string, so any repeat of the
        # same view is byte-identical and safe to cache; a changed parameter is
        # a different URL and misses by construction.
        headers={"Cache-Control": "private, max-age=300"},
    )


@app.get("/api/jobs/{job_name}/channels/{extension}/trace")
def channel_trace(request: Request, job_name: str, extension: str, index: int = Query(...)) -> dict:
    """One A-scan out of the processed radargram — the wiggle plot and cursor readout."""
    job_dir = _job(request, job_name)
    processing, _settings = _split_query(dict(request.query_params) | {"index": ""})
    processing.pop("index", None)
    processed, info, _chain = _processed(job_dir, extension, processing)
    try:
        samples = render.trace_waveform(processed, index)
    except IndexError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "trace": index,
        "distance_m": index * info.trace_spacing_m,
        "sample_interval_ns": info.sample_interval_ns,
        "samples": samples,
    }


@app.post("/api/jobs/{job_name}/channels/{extension}/fit")
def fit_hyperbola(
    request: Request,
    job_name: str,
    extension: str,
    payload: dict = Body(...),  # noqa: B008 - FastAPI dependency-injection pattern
) -> dict:
    """Fit a hyperbola inside an operator-drawn region and report the velocity it implies.

    Fits the *raw* traces, never the processed display — see
    `studio.velocity.fit_region`. A measured velocity must not move because
    somebody changed the gain.
    """
    job_dir = _job(request, job_name)
    try:
        frame = session.load_frame(job_dir, extension)
        info = session.describe_channel(frame, extension)
        fit = velocity.fit_region(
            session.raw_traces(frame),
            trace_start=int(payload["trace_start"]),
            sample_start=int(payload["sample_start"]),
            trace_span=int(payload["trace_span"]),
            sample_span=int(payload["sample_span"]),
            trace_spacing_m=info.trace_spacing_m,
            sample_interval_ns=info.sample_interval_ns,
        )
    except (KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid fit region: {exc}") from exc
    except (session.JobNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if fit is None:
        # Not an error: the region's ridge points genuinely don't describe a
        # hyperbola, which is itself the answer.
        return {"fit": None, "reason": "no hyperbola fits this region's ridge points"}

    return {
        "fit": vars(fit),
        "curve": velocity.hyperbola_curve(
            apex_trace=fit.apex_trace,
            apex_time_ns=fit.apex_time_ns,
            velocity_m_per_ns=fit.velocity_m_per_ns,
            trace_spacing_m=info.trace_spacing_m,
            n_traces=info.n_traces,
        ),
    }


@app.get("/api/jobs/{job_name}/channels/{extension}/curve")
def preview_curve(
    request: Request,
    job_name: str,
    extension: str,
    apex_trace: float = Query(...),
    apex_time_ns: float = Query(...),
    velocity_m_per_ns: float = Query(...),
) -> dict:
    """The hyperbola a given apex and velocity would draw — the manual-fit overlay."""
    job_dir = _job(request, job_name)
    try:
        info = session.describe_channel(session.load_frame(job_dir, extension), extension)
        curve = velocity.hyperbola_curve(
            apex_trace=apex_trace,
            apex_time_ns=apex_time_ns,
            velocity_m_per_ns=velocity_m_per_ns,
            trace_spacing_m=info.trace_spacing_m,
            n_traces=info.n_traces,
        )
    except (session.JobNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "curve": curve,
        "dielectric": velocity.dielectric_from_velocity(velocity_m_per_ns),
        "depth_m": velocity.depth_from_time(apex_time_ns, velocity_m_per_ns),
    }


@app.get("/api/jobs/{job_name}/candidates")
def list_candidates(request: Request, job_name: str) -> list[dict]:
    """Phase-1 candidate boxes plus their shape diagnoses — read-only, see studio/candidates.py."""
    job_dir = _job(request, job_name)
    try:
        return [vars(candidate) for candidate in candidate_store.load_candidates(job_name, job_dir)]
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/jobs/{job_name}/picks")
def list_picks(request: Request, job_name: str) -> list[dict]:
    _job(request, job_name)
    try:
        return [vars(p) for p in pick_store.load_picks(job_name)]
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/jobs/{job_name}/picks")
def create_pick(
    request: Request,
    job_name: str,
    payload: dict = Body(...),  # noqa: B008 - FastAPI dependency-injection pattern
) -> dict:
    _job(request, job_name)
    try:
        pick = pick_store.add_pick(
            job_name,
            channel=payload["channel"],
            trace=float(payload["trace"]),
            sample=float(payload["sample"]),
            time_ns=float(payload["time_ns"]),
            depth_m=float(payload["depth_m"]),
            velocity_m_per_ns=float(payload["velocity_m_per_ns"]),
            velocity_source=payload["velocity_source"],
            dielectric=float(payload["dielectric"]),
            label=payload.get("label", ""),
            note=payload.get("note", ""),
            fit_r2=None if payload.get("fit_r2") is None else float(payload["fit_r2"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid pick: {exc}") from exc
    return vars(pick)


@app.delete("/api/jobs/{job_name}/picks/{pick_id}")
def remove_pick(request: Request, job_name: str, pick_id: str) -> dict:
    _job(request, job_name)
    if not pick_store.delete_pick(job_name, pick_id):
        raise HTTPException(status_code=404, detail=f"unknown pick: {pick_id!r}")
    return {"deleted": pick_id}


@app.get("/api/jobs/{job_name}/picks.csv", response_class=PlainTextResponse)
def export_picks(request: Request, job_name: str) -> PlainTextResponse:
    """Target list as CSV, velocity provenance included on every row."""
    _job(request, job_name)
    buffer = io.StringIO()
    csv.writer(buffer).writerows(pick_store.to_csv_rows(pick_store.load_picks(job_name)))
    return PlainTextResponse(
        buffer.getvalue(),
        headers={"Content-Disposition": f'attachment; filename="{job_name}_targets.csv"'},
    )


def _studio_config(request: Request) -> Config:
    """The project config, cached on app state. Same file the live pipeline reads."""
    config = getattr(request.app.state, "config", None)
    if config is None:
        config = load_config()
        request.app.state.config = config
    return config


def _reasoning_engine(request: Request, config: Config):
    """The Studio's reasoning engine, or a 503 explaining exactly what is missing.

    A missing key is a deployment fact, not a runtime failure, so it is reported as one
    rather than being folded into the best-effort path — an operator who sees "no
    interpretation" deserves to know the difference between "the model had nothing to say"
    and "nobody configured a model". Tests inject their own engine on `app.state`.
    """
    engine = getattr(request.app.state, "reasoning_engine", None)
    if engine is not None:
        return engine
    load_dotenv()
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="reasoning is not configured — put GROQ_API_KEY in .env (see .env.example), then restart the Studio",
        )
    # v1_pick, not the pipeline's v1_finding: this target was marked by a person, so the
    # prompt must not present a detector's confidence it never had.
    engine = ReasoningEngine(GroqClient(api_key), replace(config.reasoning, prompt_version="v1_pick"))
    request.app.state.reasoning_engine = engine
    return engine


# Every interpretation is a billed model call, and a reviewer working a job clicks the same
# target repeatedly — switching channels, adjusting the display, coming back to compare. The
# measured half is deterministic and the written half costs money, so an identical request is
# answered from memory. The key is a fingerprint of the *evidence*, not the pick id: edit the
# pick, refit the velocity, or change anything the model was shown, and the fingerprint moves and
# the model is asked again. Bounded so a long session cannot grow it without limit.
_INTERPRETATION_CACHE_LIMIT = 256


def _evidence_fingerprint(job_name: str, item: interpret.PickEvidence, risk: Any) -> str:
    """Everything the model is shown, hashed. Changing any of it must invalidate the cache."""
    evidence = item.evidence
    return "|".join(
        str(part)
        for part in (
            job_name,
            item.pick.id,
            evidence.detection_class,
            evidence.depth_m,
            evidence.depth_confidence,
            evidence.position_m,
            evidence.position_confidence,
            evidence.amplitude,
            evidence.amplitude_confidence,
            evidence.hyperbola_width_px,
            evidence.neighbours,
            evidence.corroborating_channels,
            risk.level,
            round(risk.score, 6),
            risk.rules_fired,
        )
    )


def _interpretation_cache(request: Request) -> dict[str, dict]:
    cache = getattr(request.app.state, "interpretation_cache", None)
    if cache is None:
        cache = {}
        request.app.state.interpretation_cache = cache
    return cache


def _corroborating_channels(picks: list, target_id: str, trace_spacing_m: float) -> int:
    """How many distinct channels independently saw this pick's target.

    Built from every pick on the job, not just the one channel being interpreted — corroboration
    across receivers is the entire point, so it cannot be computed from one channel's picks. A
    pick that clusters with nothing returns 1: itself, seen once.

    `trace_spacing_m` comes from the channel header (`SPR_SHAFT_INTERVAL`) rather than a constant:
    it is 0.025 m on all four delivered lines, but hardcoding it here would silently produce wrong
    positions for any job recorded with a different encoder setting.
    """
    apexes = [
        corroboration.Apex(
            id=pick.id,
            channel=pick.channel,
            position_m=pick.trace * trace_spacing_m,
            depth_m=pick.depth_m,
        )
        for pick in picks
    ]
    for cluster in corroboration.corroborate(apexes):
        if target_id in cluster.apex_ids:
            return cluster.n_channels
    return 1


@app.post("/api/jobs/{job_name}/picks/{pick_id}/interpret")
def interpret_target(request: Request, job_name: str, pick_id: str) -> dict:
    """What one picked target most likely is.

    Two halves, and the response keeps them apart. The *measured* half — taxonomy class
    and the rule behind it, depth with its provenance, risk across the line — is computed
    here from the radargram and is the same every time anyone asks. The *written* half is
    a language model's, and is best-effort: if it fails or times out, the measurements
    still come back, with the reason there are no words. Raw traces are used deliberately;
    the amplitude and polarity measurements do their own background removal and need the
    direct wave intact.
    """
    job_dir = _job(request, job_name)
    try:
        picks = pick_store.load_picks(job_name)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    pick = next((candidate for candidate in picks if candidate.id == pick_id), None)
    if pick is None:
        raise HTTPException(status_code=404, detail=f"unknown pick: {pick_id!r}")

    config = _studio_config(request)
    try:
        frame = session.load_frame(job_dir, pick.channel)
        info = session.describe_channel(frame, pick.channel)
        traces = session.raw_traces(frame)
    except (session.JobNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    by_id = interpret.evidence_for_line(picks, info, traces, config.detection.taxonomy)
    item = by_id.get(pick_id)
    if item is None:
        raise HTTPException(status_code=422, detail=f"pick {pick_id!r} is not on channel {info.extension}")
    risk = interpret.risk_for_line(list(by_id.values()), config.risk)

    cache = _interpretation_cache(request)
    fingerprint = _evidence_fingerprint(job_name, item, risk)
    cached = cache.get(fingerprint)
    if cached is not None:
        return {**cached, "cached": True}

    # Count first, then reason: the model has to be told how many independent receivers saw this
    # target, or it writes "nothing has confirmed this" about a target two receivers just agreed
    # on. The evidence the model sees and the evidence the grade is computed from are then the
    # same object, which is the point.
    channels = _corroborating_channels(picks, pick_id, info.trace_spacing_m)
    evidence = replace(item.evidence, corroborating_channels=channels)

    engine = _reasoning_engine(request, config)
    result, latency_ms = engine.reason(evidence, risk)

    # post_processed=False: the interpretation runs on raw traces by design, so PAS 128's "P"
    # suffix would be a false claim here.
    grade = quality_level(evidence, corroborating_channels=channels, post_processed=False)
    response = {
        "pick_id": pick_id,
        "class": item.taxonomy_class,
        "class_rule": item.class_rule,
        "depth_m": item.evidence.depth_m,
        "depth_confidence": item.evidence.depth_confidence,
        "position_m": item.evidence.position_m,
        "risk_level": risk.level,
        "risk_score": risk.score,
        # Exactly the text the model was shown, so an operator can check the words
        # against the evidence rather than taking them on trust.
        "evidence": build_evidence_block(evidence),
        "model": config.reasoning.model,
        "latency_ms": round(latency_ms, 1),
        "reasoning": None if result is None else vars(result),
        "reasoning_error": None
        if result is not None
        else "the reasoning model returned nothing usable — the measurements above still stand",
        # What this finding may be reported as on a deliverable. QL-A is unreachable from radar
        # alone and QL-B1 needs a fitted velocity corroborated across channels — see
        # evidence/quality.py.
        "quality_level": grade.label,
        "quality_rationale": grade.rationale,
        "corroborating_channels": channels,
        "cached": False,
    }
    # Only a real answer is worth keeping. Caching a model failure would pin the failure in place
    # for the rest of the session, and the next click is exactly when a retry should happen.
    if result is not None:
        if len(cache) >= _INTERPRETATION_CACHE_LIMIT:
            cache.pop(next(iter(cache)))
        cache[fingerprint] = response
    return response


@app.get("/api/reference")
def list_reference() -> list[dict]:
    """The confirmed-hyperbola signature library — see `reference/hyperbolas/README.md`."""
    try:
        crops = reference_library.load_manifest()
    except reference_library.ReferenceLibraryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [
        {
            "id": crop.id,
            "sheet": crop.sheet,
            "callout": crop.callout,
            "confirmed_by": crop.confirmed_by,
            "label_class": crop.label_class,
            "label_depth_m": crop.label_depth_m,
        }
        for crop in crops
    ]


@app.get("/api/reference/crops/{crop_id}.png")
def reference_crop(crop_id: str) -> FileResponse:
    try:
        crop = reference_library.get_crop(crop_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(crop.path, media_type="image/png")


@app.get("/api/reference/sheets/{sheet_name}")
def reference_sheet(sheet_name: str) -> FileResponse:
    try:
        path = reference_library.sheet_path(sheet_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except reference_library.ReferenceLibraryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/jpeg")
