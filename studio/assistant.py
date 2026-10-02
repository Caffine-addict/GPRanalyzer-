"""The Studio's reasoning layers: chat, line summary, what-is-what, live commentary, survey briefing.

Every layer follows the same three rules as the per-target interpretation (`studio/interpret.py`):

1. **The model reads measurements, never pixels.** Each layer is handed a compact, labelled
   context built here — positions and depths in metres with their provenance, shape fits,
   echo polarity, relative amplitude — and the reply comes back with that exact context text,
   so anyone can check the words against what the model was shown.
2. **The model may point, never place.** A claim about an object must name a target id the
   context contains (a detector candidate "C…" or an interpreter pick "P…"). Claims naming
   anything else are dropped and reported, so every circle the Studio draws sits on something
   it actually measured.
3. **A claim is a proposal.** Named claims become `proposed` reviews (`studio/reviews.py`)
   that a supervisor confirms or rejects; nothing a model says becomes a finding on its own.

What-is-what is deliberately modest. Radar alone does not tell a cable from a water main of
the same size; practitioners use echo polarity and amplitude for *material*, the hyperbola for
size, and an EM locator or excavation for *service*. The prompt says so, and the vendor depth
statistics it is given are from other roads in another city — a prior, labelled as one.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from detect.measure import echo_matches_direct_wave_polarity
from reason.engine import LLMClient
from studio.candidates import Candidate
from studio.picks import Pick
from studio.session import ChannelInfo

logger = logging.getLogger(__name__)

SPEED_OF_LIGHT_M_PER_NS = 0.2998
LAYERS = ("chat", "line_summary", "what_is_what", "live", "briefing")
MATERIALS = ["metallic", "non-metallic, air-filled", "non-metallic, water-filled", "void", "not a utility", "unknown"]
# No material measurement is validated on this instrument yet (polarity is proven only for flat
# echoes, amplitude is uncalibrated), so a claim naming a material can be no better than "low",
# whatever the model says. On a real line the model called two targets "metallic, high" from fit
# quality and amplitude alone. Raise this once a material rule is validated against excavation.
_MATERIAL_CLAIMS = {"metallic", "non-metallic, air-filled", "non-metallic, water-filled", "void"}
MATERIAL_CAP_NOTE = "capped to low: no material measurement is validated on this instrument yet"
_PROMPT = Path(__file__).resolve().parent.parent / "reason" / "prompts" / "v1_assistant.txt"
# gpt-oss spends completion tokens reasoning before it writes; 2048 truncated a 12-claim reply
# mid-JSON on a real line (Job_0703 RAD), so the layers get room the pipeline's 1024 does not need.
_MAX_TOKENS = 4096
_MAX_HISTORY_TURNS = 6  # chat context kept small: Groq's free tier allows ~8k tokens a minute

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "mentions", "next_steps"],
    "properties": {
        "answer": {"type": "string"},
        "mentions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["target_id", "identity", "material", "confidence", "why"],
                "properties": {
                    "target_id": {"type": "string"},
                    "identity": {"type": "string"},
                    "material": {"type": "string", "enum": MATERIALS},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "why": {"type": "string"},
                },
            },
        },
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
}

LAYER_INSTRUCTIONS = {
    "chat": "Answer the operator's question about this line, using only the context. If the context "
            "cannot answer it, say what measurement would.",
    "line_summary": "Summarise the line as an interpreter would for a supervisor: how many targets look "
                    "like real point reflectors, which line up as a possible continuous utility run (similar "
                    "depth at regular spacing, or the same target seen on several channels), which are "
                    "weak or doubtful and why, and what to check next. Mention each target you discuss.",
    "what_is_what": "For each target worth identifying, say what it most likely is. First the material from "
                    "echo polarity and amplitude (metal and water-filled plastic reverse the direct wave's "
                    "polarity with a strong, ringing echo for metal; air-filled plastic keeps it), then size "
                    "from the hyperbola, then — only as a low-confidence hypothesis — the service type, using "
                    "the vendor depth statistics as a prior. Say plainly when service type needs an EM "
                    "locator or excavation.",
    "live": "The automatic detector has just flagged the candidates in the context. Explain briefly what it "
            "is finding: which candidates look like genuine buried objects, which look like clutter, "
            "ringing or surface noise, and why. Mention each candidate you judge.",
    "briefing": "Write a survey briefing for a site supervisor across everything in the context: what data "
                "exists, what has been measured and verified, what is unverified and why, the review "
                "status of model claims, and the recommended next steps. No target mentions.",
}

# What each target field means. Without this the model read `implied_dielectric` as the object's
# own material ("3.6, so water-filled") on a real line — it is the ground's, from the curvature.
FIELD_NOTES = {
    "position_m": "distance along the line to the target's centre",
    "depth_m": "depth to the top of the echo; see depth_basis for whether it is measured or assumed",
    "shape": "shape measured from the echo: point (hyperbola), linear, disturbed, ambiguous",
    "suggested_class": "a rule-based suggestion from those measurements, not a confirmed class",
    "fit_r2": "how well a hyperbola fits the echo (1 = perfect); below ~0.85 the shape is doubtful",
    "implied_dielectric": "dielectric of the SOIL above the target, implied by the hyperbola's curvature — "
                          "NOT the object's material. Soils are roughly 4-30; far outside that the fit is "
                          "unreliable (ringing, clutter, or a truncated hyperbola)",
    "polarity": "first strong echo compared with the direct wave. Validated only for flat-topped echoes "
                "(voids, layers); on a point reflector it is a weak hint, never enough on its own for material",
    "relative_amplitude": "mean echo strength against the median target on this line (1 = typical)",
}

METHODS_NOTE = (
    "How material and type are told apart in practice: echo polarity relative to the direct wave "
    "(kept = drop in permittivity, e.g. air; reversed = rise, e.g. water, concrete, or metal); amplitude "
    "and ringing (metal gives strong repeated echoes); hyperbola fit for radius; top-and-bottom echoes of "
    "large pipes for contents; dual-polarised antennas for orientation; EM locators (passive 50 Hz for "
    "live power, active signal for metallic lines) and excavation for the service itself."
)


# --- context ------------------------------------------------------------------------------------

def _depth_m(sample: float, info: ChannelInfo) -> float | None:
    if not info.dielectric_assumed:
        return None
    velocity = SPEED_OF_LIGHT_M_PER_NS / math.sqrt(info.dielectric_assumed)
    return round(velocity * sample * info.sample_interval_ns / 2, 2)


def _polarity(traces: np.ndarray, x: float, y: float, w: float, h: float, info: ChannelInfo) -> str:
    rows = slice(int(y), max(int(y + h), int(y) + 1))
    cols = slice(int(x), max(int(x + w), int(x) + 1))
    same = echo_matches_direct_wave_polarity(traces, rows, cols, info.sample_interval_ns)
    return {True: "same as direct wave", False: "reversed from direct wave", None: "unreadable"}[same]


def _amplitude(traces: np.ndarray, x: float, y: float, w: float, h: float) -> float:
    region = traces[int(x): max(int(x + w), int(x) + 1), int(y): max(int(y + h), int(y) + 1)]
    return float(np.abs(region).mean()) if region.size else 0.0


def line_targets(candidates: list[Candidate], picks: list[Pick], info: ChannelInfo,
                 traces: np.ndarray) -> list[dict[str, Any]]:
    """Every target on one channel, as the model sees it, with the box a circle will be drawn on.

    Depth is from the file header's dielectric ("assumed"), or the pick's own when a person
    fitted it. A pick's free-text label is left out for the same reason interpret.py leaves it
    out: it is the interpreter's hypothesis, and handing it over would return it as a finding.
    """
    raw: list[dict[str, Any]] = []
    for c in candidates:
        if c.channel != info.extension:
            continue
        raw.append({"ref": c.id, "kind": "candidate", "x": c.x, "y": c.y, "w": c.w, "h": c.h,
                    "depth_m": _depth_m(c.y, info), "depth_basis": "header dielectric (assumed)",
                    "shape": c.shape, "suggested_class": c.suggested_class,
                    "fit_r2": None if c.fit_r2 is None else round(c.fit_r2, 2),
                    "implied_dielectric": None if c.implied_dielectric is None else round(c.implied_dielectric, 1)})
    for p in picks:
        if p.channel != info.extension:
            continue
        half = max(4.0, 0.25 / info.trace_spacing_m)
        raw.append({"ref": p.id, "kind": "pick", "x": p.trace - half, "y": max(0.0, p.sample - 4), "w": 2 * half,
                    "h": 24.0, "depth_m": round(p.depth_m, 2),
                    "depth_basis": f"velocity {p.velocity_source}", "shape": None, "suggested_class": None,
                    "fit_r2": None, "implied_dielectric": None})
    amplitudes = [_amplitude(traces, t["x"], t["y"], t["w"], t["h"]) for t in raw]
    typical = statistics.median(amplitudes) if amplitudes else 0.0
    for target, amplitude in zip(raw, amplitudes, strict=True):
        target["position_m"] = round((target["x"] + target["w"] / 2) * info.trace_spacing_m, 2)
        target["polarity"] = _polarity(traces, target["x"], target["y"], target["w"], target["h"], info)
        target["relative_amplitude"] = round(amplitude / typical, 2) if typical else None
    # Short labels along the line (C1, C2… / P1…): the stored ids are 8-hex hashes a model
    # would garble, and a garbled id is a dropped claim. `ref` keeps the real id.
    ordered = sorted(raw, key=lambda t: t["position_m"])
    counters = {"candidate": 0, "pick": 0}
    for target in ordered:
        counters[target["kind"]] += 1
        target["id"] = f"{'C' if target['kind'] == 'candidate' else 'P'}{counters[target['kind']]}"
    return ordered


_SHOWN = ("id", "kind", "position_m", "depth_m", "depth_basis", "shape", "suggested_class", "fit_r2",
          "implied_dielectric", "polarity", "relative_amplitude")


def line_context(job: str, info: ChannelInfo, targets: list[dict[str, Any]],
                 vendor_priors: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "line": {"job": job, "channel": info.extension, "channel_label": info.label,
                 "length_m": round(info.line_length_m, 1), "trace_spacing_m": info.trace_spacing_m,
                 "time_window_ns": info.time_window_ns, "header_dielectric": info.dielectric_assumed,
                 "max_depth_m": info.max_depth_m},
        "field_notes": FIELD_NOTES,
        "targets": [{k: t[k] for k in _SHOWN} for t in targets],
        "vendor_depth_priors": vendor_priors,
        "methods": METHODS_NOTE,
    }


def vendor_priors(csv_path: Path) -> dict[str, Any] | None:
    """Depth statistics per utility type from the vendor SUE drawings — a prior, not local truth.

    Only text-layer rows: the OCR-read ones are a lower bound with ~70% recall, and would bias
    the distribution towards the labels OCR reads best.
    """
    if not csv_path.exists():
        return None
    depths: dict[str, list[float]] = defaultdict(list)
    with csv_path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("read_by", "text") == "text" and row["depth_m"]:
                depths[row["utility"]].append(float(row["depth_m"]))
    if not depths:
        return None
    return {
        "source": "Sky Group SUE drawings, 8 Bangalore roads, depth to top of utility, vendor states ±30%",
        "by_type": {name: {"n": len(v), "median_m": round(statistics.median(v), 2),
                           "p10_m": round(float(np.percentile(v, 10)), 2), "p90_m": round(float(np.percentile(v, 90)), 2)}
                    for name, v in sorted(depths.items(), key=lambda kv: -len(kv[1]))},
    }


def survey_context(jobs: dict[str, dict[str, Any]], processed_dir: Path) -> dict[str, Any]:
    """Everything the briefing is allowed to talk about, each item saying where it came from."""
    context: dict[str, Any] = {"radar_lines": jobs}
    utilities = processed_dir / "sue_utilities.csv"
    if utilities.exists():
        with utilities.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        context["vendor_drawings"] = {
            "callouts": len(rows),
            "by_read_method": dict(Counter(r.get("read_by", "text") for r in rows)),
            "by_status": dict(Counter(r["status"] for r in rows)),
            "by_type": dict(Counter(r["utility"] for r in rows).most_common()),
            "ocr_accuracy_note": "OCR-read drawings: ~69% of labels found, ~94% of those read correctly "
                                 "(measured against the text-layer drawings)",
        }
    targets = processed_dir / "vendor_radargram_targets.csv"
    if targets.exists():
        with targets.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        credible = sorted(float(r["implied_dielectric"]) for r in rows
                          if r.get("implied_dielectric") and not r.get("fit_rejected"))
        context["vendor_radargrams"] = {
            "circled_targets": len(rows),
            "classical_detector_hits": sum(r["detector_box_on_it"] == "True" for r in rows),
            "matched_filter_hits": sum(r.get("matched_filter_on_it") == "True" for r in rows),
            "velocity_check": {"credible_fits": len(credible), "implied_dielectrics": credible,
                               "report_dielectric": 7.3},
        }
    context["known_limits"] = [
        "No trained detection model is installed; a synthetic-data training run may be in progress.",
        "No labelled real B-scans exist, so no accuracy on real lines can be stated.",
        "Service type (electric/water/OFC) cannot be read from radar alone.",
    ]
    return context


# --- the call -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Reply:
    answer: str
    mentions: list[dict[str, Any]]  # each joined to its target (box, channel, kind)
    dropped: list[str]  # "<id>: <reason>" for each claim not kept (unknown target, unknown material)
    next_steps: list[str]
    model: str | None
    latency_ms: float
    context_text: str
    error: str | None


def build_prompt(layer: str, context: dict[str, Any], question: str = "",
                 history: list[dict[str, str]] | None = None) -> tuple[str, str]:
    """(prompt, the context text it contains). Layer instructions and context are always present."""
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer!r}")
    context_text = json.dumps(context, indent=1, default=str)
    turns = (history or [])[-_MAX_HISTORY_TURNS:]
    transcript = "\n".join(f"{t['role']}: {t['text']}" for t in turns) or "(none)"
    template = _PROMPT.read_text(encoding="utf-8")
    prompt = template.format(instructions=LAYER_INSTRUCTIONS[layer], context=context_text,
                             history=transcript, question=question.strip() or "(no question — do the task above)")
    return prompt, context_text


def run(client: LLMClient, models: list[str], layer: str, context: dict[str, Any], targets: list[dict[str, Any]],
        question: str = "", history: list[dict[str, str]] | None = None,
        temperature: float = 0.2, timeout_s: float = 30.0) -> Reply:
    """Ask one layer. Never raises for a model failure: the reply carries the error instead."""
    prompt, context_text = build_prompt(layer, context, question, history)
    start = time.monotonic()
    last_error = "no model configured"
    for model in models:
        try:
            raw = client.complete_json(prompt, schema=SCHEMA, model=model, temperature=temperature,
                                       max_tokens=_MAX_TOKENS, timeout_s=timeout_s, strict=True)
            return _parsed(raw, model, targets, context_text, start)
        except Exception as exc:  # noqa: BLE001 - best-effort, like reason/engine.py; the next model may answer
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("assistant.attempt_failed layer=%s model=%s error=%s", layer, model, last_error)
    return Reply("", [], [], [], None, _elapsed(start), context_text,
                 f"the reasoning model returned nothing usable ({last_error})")


def _elapsed(start: float) -> float:
    return round((time.monotonic() - start) * 1000, 1)


def _parsed(raw: dict[str, Any], model: str, targets: list[dict[str, Any]], context_text: str,
            start: float) -> Reply:
    by_id = {t["id"]: t for t in targets}
    mentions, dropped = [], []
    for mention in raw.get("mentions", []):
        target_id = str(mention.get("target_id"))
        target = by_id.get(target_id)
        if target is None:
            dropped.append(f"{target_id}: no such target on this line")
            continue
        if mention.get("material") not in MATERIALS:
            dropped.append(f"{target_id}: material {mention.get('material')!r} is not one the Studio records")
            continue
        if mention["material"] in _MATERIAL_CLAIMS and mention.get("confidence") != "low":
            mention = {**mention, "confidence": "low", "why": f"{mention.get('why', '')} [{MATERIAL_CAP_NOTE}]"}
        mentions.append({**mention, "target_kind": target["kind"], "target_ref": target["ref"],
                         **{k: target[k] for k in ("x", "y", "w", "h", "position_m", "depth_m")}})
    return Reply(str(raw.get("answer", "")), mentions, dropped, [str(s) for s in raw.get("next_steps", [])],
                 model, _elapsed(start), context_text, None)


def fingerprint(layer: str, context_text: str, question: str, history: list[dict[str, str]] | None) -> str:
    """Everything the model is shown, hashed: an identical request is answered from cache."""
    # The template is part of what the model is shown: a prompt fix must not be answered from
    # a cache filled under the old wording.
    payload = json.dumps([_PROMPT.read_text(encoding="utf-8"), layer, context_text, question.strip(),
                          (history or [])[-_MAX_HISTORY_TURNS:]])
    return hashlib.sha256(payload.encode()).hexdigest()
