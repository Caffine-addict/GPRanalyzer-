"""What a site's survey report says, worked out from the Studio's own data (studio/survey_report.py lays it out).

A pure function of what geo_routes gathers: the 3D scene, the utility runs and their claims, the
ground sections, the vendor's record call-outs and each line's header and usable depth. Every
sentence it produces is a count, a measurement or a stated absence: where the Studio holds nothing
(no EML, no antenna frequency, no trial pit), it says so rather than leaving the reader to assume.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections import Counter
from typing import Any

from core.geo import to_local
from studio.runs import QUALITY
from studio.session import MIN_PLAUSIBLE_DIELECTRIC

RECORD_SEARCH_M = 10.0  # record call-outs this far outside the surveyed lines still count as "in the area"
_SOURCE_TEXT = {"surveyed": "surveyed reference points", "gps_track": "onboard GPS track"}
_FEATURE_CLASSES = {"cavities": "possible void or cavity", "disturbed_zone": "disturbed ground (e.g. trench fill)"}


def _short(job: str) -> str:
    return job.split("_", 1)[1] if "_" in job else job


def _along(line: dict[str, Any], chainage: float) -> tuple[float, float]:
    path = line["path"]
    for (c0, e0, n0), (c1, e1, n1) in itertools.pairwise(path):
        if chainage <= c1:
            f = 0.0 if c1 == c0 else max(0.0, min(1.0, (chainage - c0) / (c1 - c0)))
            return e0 + f * (e1 - e0), n0 + f * (n1 - n0)
    return path[-1][1], path[-1][2]


def _targets_below_ground(line: dict[str, Any]) -> list[dict[str, Any]]:
    """The site picker's targets and people's picks, with depth below ground (as studio/runs.py measures it)."""
    v = line["velocity"]["value"]
    out = []
    for t in line["targets"]:
        if t["kind"] == "detected":
            time_ns = t["time_ns"]
        elif t["kind"] == "pick" and t["channel"] == line["channel"]:
            time_ns = t["sample"] * t["dt_ns"] - line["time_zero_ns"]
        else:
            continue
        if v and time_ns > 0:
            east, north = _along(line, t["chainage_m"])
            out.append({"id": t["id"], "job": line["job"], "east": east, "north": north, "depth_m": v * time_ns / 2,
                        "chainage_m": t["chainage_m"]})
    return out


def build(site: str, scene: dict[str, Any], found: dict[str, Any], claims: list[dict[str, Any]],
          ground: dict[str, Any], records: list[dict[str, Any]], lines_meta: dict[str, dict[str, Any]],
          software: dict[str, Any]) -> dict[str, Any]:
    placed, located, unplaced = scene["lines"], scene["located"], scene["unplaced"]
    origin = scene["origin"]
    jobs = [*(line["job"] for line in placed), *(s["job"] for s in located), *(u["job"] for u in unplaced)]
    targets = [t for line in placed for t in _targets_below_ground(line)]
    measured = {g["job"]: g["dielectric"] for g in ground["lines"]}

    # ---- area and plan
    points = [(e, n) for line in placed for _, e, n in line["path"]] + [(s["east"], s["north"]) for s in located]
    if points:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        extent = f"about {max(xs) - min(xs):.0f} m east-west by {max(ys) - min(ys):.0f} m north-south"
    else:
        extent = "unknown: no line has a usable position"
    area = (f"The Studio holds {len(jobs)} radar lines for site {site}. "
            f"{len(placed)} are placed ({_counts(placed)}), {len(located)} have a location only (GPS froze) "
            f"and {len(unplaced)} have no usable position. The placed survey covers {extent}"
            + (f" around {origin['lat']:.6f}, {origin['lon']:.6f}." if origin else "."))
    in_area = []
    for record in records:
        lon, lat = record["geometry"]["coordinates"][:2]
        if origin is None:
            break
        east, north = to_local(lat, lon, origin["lat"], origin["lon"])
        near = min((math.hypot(east - e, north - n) for e, n in points), default=math.inf)
        if near <= RECORD_SEARCH_M + max((s["radius_m"] for s in located), default=0):
            in_area.append({**record["properties"], "east": east, "north": north})
    plan = {
        "lines": [{"label": _short(line["job"]), "source": line["source"], "en": [(e, n) for _, e, n in line["path"]]}
                  for line in placed],
        "located": [{"label": _short(s["job"]), "en": (s["east"], s["north"]), "radius_m": s["radius_m"]} for s in located],
        "targets": [(t["east"], t["north"]) for t in targets],
        "runs": [{"status": c["status"], "en": [(t["east"], t["north"]) for t in c["targets"]]}
                 for c in claims if c["status"] != "rejected" and c.get("found_now", True)],
        "records": [{"en": (r["east"], r["north"]), "utility": r.get("utility")} for r in in_area],
        "note": ("Local metres from the site origin, north up. Blue: line placed by onboard GPS; green: by surveyed "
                 "points; dashed orange circle: GPS froze, the line ran somewhere inside it. Orange dashed: proposed "
                 "utility run; green: confirmed. Red crosses: utility record call-outs from the vendor drawings. "
                 f"Lines with no position are not drawn: {', '.join(_short(u['job']) for u in unplaced) or 'none'}."),
    }

    # ---- methods and metadata
    header: dict[str, str] = next((m["header"] for m in lines_meta.values() if m["header"]), {})
    channel_text = sorted({f"{c['extension']} {c['sample_interval_ns']} ns × {c['n_samples']} samples"
                           for m in lines_meta.values() for c in m["channels"]})
    methods = [
        (f"Ground-penetrating radar: {header.get('INSTRUMENT', 'instrument not recorded')} "
        f"(antenna reported as {header.get('ANTENNA_TYPE_DETECTED', 'not recorded')}). Three frequency channels are "
        f"recorded at once from one antenna head: {', '.join(channel_text) or 'none'}. Distance along each line from "
        "the wheel encoder."),
        "Antenna centre frequency: not recorded in the files, so no frequency band could be set from it.",
        (f"Positioning: {_counts(placed) or 'no placed lines'}; GPS location only (froze) on {len(located)} line(s); "
        f"no position on {len(unplaced)}."),
        ("Electromagnetic location (EML): no EML data is held in GPR Studio for this site. If EML was used on site, "
        "its findings are not part of this report."),
        (f"Interpretation aids, all of whose output an interpreter reviews: automatic target picking "
        f"({software['detector']}, fused across the three channels); utility-run linking across lines "
        "(deterministic rules, no learned model); language-model readings "
        f"({', '.join(software['models'])}), stored as proposals until a person confirms or rejects each."),
    ]
    metadata = []
    for job in jobs:
        meta = lines_meta.get(job, {"header": {}, "channels": []})
        h = meta["header"]
        length = max((c["line_length_m"] for c in meta["channels"]), default=0)
        traces = max((c["n_traces"] for c in meta["channels"]), default=0)
        header_eps = h.get("SPR_MEDIUM_DIELECTRIC", "—")
        try:
            if float(header_eps) < MIN_PLAUSIBLE_DIELECTRIC:
                header_eps += " (not usable: below any soil)"
        except ValueError:
            pass
        source = next((_SOURCE_TEXT[line["source"]] for line in placed if line["job"] == job),
                      "location only (GPS froze)" if any(s["job"] == job for s in located) else "none")
        metadata.append([job, f"{h.get('ACQUISITION_DATE', '?')} {h.get('ACQUISITION_TIME', '')}".strip(),
                         f"{length:.1f} m", str(traces), ", ".join(c["extension"] for c in meta["channels"]),
                         header_eps, source])

    # ---- how well each method worked
    not_measured = [_short(g["job"]) for g in ground["lines"] if g["dielectric"] is None]
    success = [
        f"Radar data: recorded on all {len(jobs)} lines.",
        f"Positioning: succeeded on {len(placed)} of {len(jobs)} lines. "
        + (f"GPS froze on {', '.join(_short(s['job']) for s in located)}: each is shown only as the circle it lies in "
           "(radius " + ", ".join(f"{s['radius_m']:.1f} m" for s in located) + "), with no targets placed. " if located else "")
        + (f"No usable position on {', '.join(_short(u['job']) for u in unplaced)}: not on the plan." if unplaced else ""),
        f"Target picking: {len(targets)} targets on the placed lines.",
        f"Ground dielectric: measured on {len(measured) - len(not_measured)} of {len(measured)} lines"
        + (f"; too few clear hyperbolas on {', '.join(not_measured)}." if not_measured else "."),
        (f"Utility-run linking: {len(found['runs'])} run(s); {found['chance']['verdict']} "
        f"(p {found['chance']['p_chance']}, {found['chance']['chance_mean']} on average at random positions)."),
    ]

    # ---- records against detections
    not_detected, conflicts = [], []
    for record in in_area:
        tolerance = (record.get("position_error_m") or 5.0) + max((line["position_error_m"] for line in placed), default=0)
        nearest = min(targets, key=lambda t: math.hypot(t["east"] - record["east"], t["north"] - record["north"]),
                      default=None)
        gap = math.hypot(nearest["east"] - record["east"], nearest["north"] - record["north"]) if nearest else math.inf
        label = f"{record.get('utility') or 'utility'} (record depth {record.get('depth_m', '—')} m, {record.get('source', '')})"
        if gap > tolerance:
            not_detected.append(f"{label}: no detection within {tolerance:.1f} m.")
        elif record.get("depth_m") is not None and nearest is not None:
            difference = abs(nearest["depth_m"] - record["depth_m"])
            if difference > 0.3 * record["depth_m"] + 0.15 * nearest["depth_m"]:
                conflicts.append(f"{label}: nearest detection {nearest['id']} is {gap:.1f} m away at "
                                 f"{nearest['depth_m']:.2f} m depth, {difference:.2f} m from the record.")
    records_note = (
        f"{len(in_area)} utility call-outs from the vendor's drawings lie within {RECORD_SEARCH_M:.0f} m of the survey. "
        "Their positions are estimated from the drawings and the lines' from onboard GPS, so a match means "
        "'within their combined position error', not a confirmed identity."
        if in_area else f"No utility call-outs from the vendor's drawings lie within {RECORD_SEARCH_M:.0f} m of the survey.")

    # ---- other features
    other = []
    for line in placed:
        for t in line["targets"]:
            if t["kind"] == "candidate" and t.get("class") in _FEATURE_CLASSES and t["channel"] == line["channel"]:
                other.append(f"{_short(line['job'])} at {t['chainage_m']:.2f} m: {_FEATURE_CLASSES[t['class']]} "
                             f"(detector class {t['class']}, channel {t['channel']}).")

    # ---- calibration, penetration, conditions
    velocity = Counter(line["velocity"]["source"] for line in placed)
    fitted = sum(1 for line in placed for t in line["targets"] if t["kind"] == "pick" and t.get("velocity_source") == "fitted")
    calibration = [
        "Velocity used for depths, by line: " + ", ".join(f"{n} {source}" for source, n in velocity.items())
        + " (fitted = from a hyperbola on that line; assumed = header dielectric; site = measured on the site's own hyperbolas).",
        f"Hyperbola fits by an interpreter on this site: {fitted}.",
        f"Ground dielectric measured from the lines' own hyperbolas: {len(ground['sections'])} section(s): "
        + "; ".join(f"ε {z['dielectric']} (lines vary {z['range'][0]}–{z['range'][1]}), velocity {z['velocity_m_per_ns']} m/ns, "
                    f"{len(z['lines'])} lines" for z in ground["sections"])
        + f". Typical error of this measurement about {round(ground['typical_error'] * 100)}%.",
        "No calibration against a target of known depth (trial pit, exposed utility) has been recorded for this site.",
    ]
    # Usable depth: the time where the record sinks into noise (detect/expert_checks.penetration_sample),
    # converted with the same velocity and time zero the line's depths use everywhere else.
    by_job = {line["job"]: line for line in placed} | {s["job"]: s for s in located}
    rows, depths = [], []
    for job in jobs:
        usable = lines_meta.get(job, {}).get("usable") or {}
        line = by_job.get(job)
        v = line["velocity"]["value"] if line else None
        time_ns = usable.get("time_ns")
        limit = ("noise floor" if time_ns is not None else "the record's time window (signal reaches its end)"
                 if usable else "not measured")
        time_ns = time_ns if time_ns is not None else usable.get("window_ns")
        if time_ns is not None and v:
            depth = v * max(0.0, time_ns - (line or {}).get("time_zero_ns", 0.0)) / 2
            depths.append(depth)
            rows.append([job, f"{depth:.2f} m ({time_ns:.1f} ns)", f"{limit}; velocity {line['velocity']['source'] if line else ''}"])
        else:
            rows.append([job, f"{time_ns:.1f} ns" if time_ns is not None else "—", f"{limit}; no velocity, so no depth"])
    penetration = {"rows": rows, "summary": (
        f"Usable depth on the shallow channel (RAD) ranges {min(depths):.2f}–{max(depths):.2f} m, median "
        f"{statistics.median(depths):.2f} m, each line converted with its own velocity." if depths else "Not measured.")}
    conditions = [f"Wet ground measured on {_short(g['job'])} (ε {g['dielectric']}): depths there may read shallow."
                  for g in ground["lines"] if g.get("wet")]

    # ---- recommendations
    recommendations = []
    if any(line["source"] == "gps_track" for line in placed) or located:
        recommendations.append("Survey at least two reference points per line with a total station or RTK GNSS: every "
                               "placed line rests on onboard GPS, and lines whose GPS froze cannot be placed at all.")
    if unplaced:
        recommendations.append(f"Position the lines with none: {', '.join(_short(u['job']) for u in unplaced)}.")
    proposed = [c for c in claims if c["status"] == "proposed"]
    if proposed:
        recommendations.append(f"Review the {len(proposed)} proposed utility run(s) and verify any that matter by trial pit "
                               "before relying on them.")
    if not found["chance"]["beats_chance"] and found["runs"]:
        recommendations.append("Runs here are no more common than at random: collect lines closer together, or verify "
                               "targets, before treating any run as a utility.")
    if not_detected:
        recommendations.append(f"Sweep the {len(not_detected)} record utilities not detected by GPR with an EML locator.")
    if not_measured:
        recommendations.append(f"Measure velocity on {', '.join(not_measured)} (hyperbola fits) or by a trial pit.")
    recommendations.append("Record the antenna's centre frequency with the survey: the files do not carry it.")

    quality = {
        "note": (f"Quality level: {QUALITY['pas128']} for every run. {QUALITY['reason'][0].upper()}{QUALITY['reason'][1:]}. "
                 f"It {QUALITY['provisional_ceiling']} The GPR data was post-processed, so any level that is later "
                 "supported would carry the P suffix. No ASCE 38 level is given."),
        "rows": [[c["id"], c["status"], str(c["lines"]), f"{c['depth_m']} ({c['depth_range_m'][0]}–{c['depth_range_m'][1]})",
                  f"{QUALITY['pas128']} (P)", f"confidence {c['confidence']}; {c['chance']['verdict']}"]
                 for c in claims],
    }
    archive = [
        "Raw radar files: each line's folder in the dataset, unchanged by the Studio.",
        "Annotations (picks, claims, survey points, run decisions, this report's form and approvals): annotations/.",
        "Every language-model reply, with exactly what the model was shown: annotations/_audit/.",
        "Removed lines are moved to removed_lines/, never deleted by the Studio.",
        ("PAS 128:2022 asks for six years' retention (12 for contracts under seal). The Studio deletes none of the "
        "above; nightly backups keep the last 14. Long-term retention must be arranged by the operator."),
    ]
    software_text = [
        f"GPR Studio {software['version']} (code revision {software['commit']}).",
        ("Detection and every measurement read the raw traces. Display processing (dewow, background removal, "
        "band-pass, gain) is for viewing only and does not change any measured value."),
    ]
    return {"area": area, "methods": methods, "plan": plan, "metadata": metadata, "success": success,
            "records": {"note": records_note, "not_detected": not_detected, "conflicts": conflicts},
            "other_features": other, "recommendations": recommendations, "calibration": calibration,
            "software": software_text, "penetration": penetration, "conditions": conditions, "quality": quality,
            "archive": archive}


def _counts(placed: list[dict[str, Any]]) -> str:
    counts = Counter(line["source"] for line in placed)
    return ", ".join(f"{n} by {_SOURCE_TEXT.get(source, source)}" for source, n in counts.items())
