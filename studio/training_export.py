"""Turn supervisor decisions into detector training data.

Public GPR data cannot train this detector: simulated radargrams do not transfer to real ground
(docs/EXTERNAL_DATA.md), and the public field datasets withhold where their utilities are. The
labels that do exist are the ones made here: every target a supervisor confirmed or an interpreter
picked is a real, positioned example on the customer's own radar, and every rejection is a hard
negative. Exporting them is how the detector gets better with use.

Only what someone decided is exported. A whole radargram labelled where it was reviewed would teach
the model that every unreviewed pipe on it is background, so each decided target becomes a tile
around itself, and a tile touching a claim still awaiting review is left out. Each example keeps
who decided it and when (manifest.jsonl), so the training set has the same audit trail as reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from functools import partial
from pathlib import Path

import cv2
import numpy as np
import yaml

from core.config import EnhancementConfig, load_config
from preprocess.enhance import enhance
from render.bscan import traces_to_image
from studio import session
from studio.field_reports import FieldReport, load_reports
from studio.picks import Pick, load_picks
from studio.reviews import Review, load_reviews

LABEL_CLASS = 0  # point_reflector: reviewed targets are hyperbola apexes; the free-text identity rides in the manifest
_MIN_TILE = 128  # native traces and samples: room for a hyperbola's limbs around its apex
_PICK_HALF_WIDTH, _PICK_ABOVE, _PICK_BELOW = 16, 4, 36  # estimated box around a picked apex, native units
_VAL_SHARE = 0.2

Box = tuple[float, float, float, float]  # x (trace), y (sample), w, h in native units


@dataclass(frozen=True)
class Decision:
    channel: str
    box: Box
    positive: bool
    source: str  # "review" or "pick"
    source_id: str
    identity: str
    decided_by: str
    decided_at: str
    box_estimated: bool  # a pick marks an apex, so its box is an estimate
    field_verified: bool = False  # a crew reported what the ground held there (studio/field_reports.py)
    field_depth_m: float | None = None  # measured in the hole: depth and velocity calibration data


@dataclass(frozen=True)
class Example:
    job: str
    channel: str
    window: tuple[int, int, int, int]  # trace0, sample0, trace1, sample1
    labels: tuple[Box, ...]  # positives inside the window, window-relative native units
    origin: Decision


def decisions(reviews: Sequence[Review], picks: Sequence[Pick], reports: Sequence[FieldReport] = (),
              job: str | None = None) -> tuple[list[Decision], list[tuple[str, Box]]]:
    """Every decided target, and the boxes left unknown (channel, box) that no tile may touch.

    Unknown: claims still awaiting review, and targets the crew dug for and did not find. A miss is
    not proof of absence (the hole can be off by the GPS error, too shallow, or the reflector real but
    not a utility), so it is never a hard negative that would teach the detector to ignore a clean
    hyperbola. Found or dug is ground truth and makes a positive, unless a supervisor decided again
    after the report. A supervisor's decision on an interpreter's pick replaces the pick, so one
    target never yields two tiles with opposite labels.
    """
    if job is not None:
        reports = [rep for rep in reports if rep.item_id.startswith(f"{job}#")]
    order = {rep.id: k for k, rep in enumerate(reports)}
    latest: dict[str, FieldReport] = {}
    for rep in sorted(reports, key=lambda rep: (rep.reported_at, order[rep.id])):
        latest[rep.item_id.split("#", 1)[1]] = rep
    decided_on_pick = {r.target_id: r.id for r in reviews if r.target_kind == "pick" and r.status != "proposed"}
    for pick_id, review_id in decided_on_pick.items():  # a report filed against the pick reaches its review
        if pick_id in latest and (review_id not in latest or latest[pick_id].reported_at > latest[review_id].reported_at):
            latest[review_id] = latest[pick_id]

    decided, undecided = [], []
    for r in reviews:
        box = (r.x, r.y, r.w, r.h)
        if r.status == "proposed":
            undecided.append((r.channel, box))
            continue
        decided.append(Decision(r.channel, box, r.status == "confirmed", "review", r.id,
                                r.identity, r.reviewer, r.decided_at, box_estimated=False))
    for p in picks:
        if p.id in decided_on_pick:
            continue
        box = (p.trace - _PICK_HALF_WIDTH, p.sample - _PICK_ABOVE, 2.0 * _PICK_HALF_WIDTH, float(_PICK_ABOVE + _PICK_BELOW))
        decided.append(Decision(p.channel, box, True, "pick", p.id, p.label, "interpreter", p.created_at, box_estimated=True))

    labelled = []
    for d in decided:
        report = latest.get(d.source_id)
        if report is None or report.reported_at < d.decided_at:  # a later decision stands over an older report
            labelled.append(d)
        elif report.outcome == "not_found":
            undecided.append((d.channel, d.box))
        else:
            labelled.append(replace(d, positive=True, source=f"field ({d.source})", decided_by=report.reporter,
                                    decided_at=report.reported_at, field_verified=True, field_depth_m=report.depth_found_m))
    return labelled, undecided


def _window(box: Box, n_traces: int, n_samples: int) -> tuple[int, int, int, int]:
    x, y, w, h = box
    width, height = min(n_traces, max(_MIN_TILE, int(2 * w))), min(n_samples, max(_MIN_TILE, int(2 * h)))
    t0 = int(np.clip(x + w / 2 - width / 2, 0, n_traces - width))
    s0 = int(np.clip(y + h / 2 - height / 2, 0, n_samples - height))
    return t0, s0, t0 + width, s0 + height


def _overlaps(box: Box, window: tuple[int, int, int, int]) -> bool:
    x, y, w, h = box
    t0, s0, t1, s1 = window
    return x < t1 and x + w > t0 and y < s1 and y + h > s0


def _inside(box: Box, window: tuple[int, int, int, int]) -> Box | None:
    """The box clipped to the window, window-relative; None unless at least half of it is inside."""
    x, y, w, h = box
    t0, s0, t1, s1 = window
    cx0, cy0, cx1, cy1 = max(x, t0), max(y, s0), min(x + w, t1), min(y + h, s1)
    if cx1 <= cx0 or cy1 <= cy0 or (cx1 - cx0) * (cy1 - cy0) < 0.5 * w * h:
        return None
    return cx0 - t0, cy0 - s0, cx1 - cx0, cy1 - cy0


def examples_for_job(job: str, reviews: Sequence[Review], picks: Sequence[Pick],
                     shape_of: Callable[[str], tuple[int, int]], reports: Sequence[FieldReport] = ()) -> list[Example]:
    """One tile per decided target; `shape_of(channel)` gives that channel's (n_traces, n_samples)."""
    decided, undecided = decisions(reviews, picks, reports, job)
    out = []
    for d in decided:
        n_traces, n_samples = shape_of(d.channel)
        window = _window(d.box, n_traces, n_samples)
        if any(ch == d.channel and _overlaps(box, window) for ch, box in undecided):
            continue
        labels = tuple(b for other in decided if other.channel == d.channel and other.positive
                       if (b := _inside(other.box, window)) is not None)
        out.append(Example(job, d.channel, window, labels, d))
    return out


def _split(job: str) -> str:
    """Whole jobs go to one split, so a line never trains and validates at once."""
    return "val" if int(hashlib.sha256(job.encode()).hexdigest(), 16) % 100 < _VAL_SHARE * 100 else "train"


def write_dataset(examples: Sequence[Example], traces_of: Callable[[str, str], np.ndarray], out_dir: Path,
                  enhancement: EnhancementConfig, class_names: Sequence[str]) -> dict[str, int]:
    """YOLO images/labels per split, data.yaml, and the manifest. Returns counts."""
    for split in ("train", "val"):
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)
    counts = {"examples": 0, "positives": 0, "hard_negatives": 0, "field_verified": 0}
    manifest = []
    for i, ex in enumerate(examples):
        t0, s0, t1, s1 = ex.window
        crop = traces_of(ex.job, ex.channel)[t0:t1, s0:s1]
        split = _split(ex.job)
        name = f"{ex.job}_{ex.channel}_{i:05d}"
        cv2.imwrite(str(out_dir / "images" / split / f"{name}.png"), enhance(traces_to_image(crop), enhancement))
        width, height = t1 - t0, s1 - s0
        rows = [f"{LABEL_CLASS} {(x + w / 2) / width:.6f} {(y + h / 2) / height:.6f} {w / width:.6f} {h / height:.6f}"
                for x, y, w, h in ex.labels]
        (out_dir / "labels" / split / f"{name}.txt").write_text("".join(r + "\n" for r in rows))
        manifest.append(json.dumps({"image": f"{split}/{name}.png", "job": ex.job, "channel": ex.channel,
                                    "window": list(ex.window), "labels": [list(b) for b in ex.labels],
                                    **{f"origin_{k}": v for k, v in asdict(ex.origin).items()}}))
        counts["examples"] += 1
        counts["positives"] += len(ex.labels)
        counts["hard_negatives"] += int(not ex.origin.positive)
        counts["field_verified"] += int(ex.origin.field_verified)
    (out_dir / "manifest.jsonl").write_text("".join(line + "\n" for line in manifest))
    (out_dir / "data.yaml").write_text(yaml.safe_dump(
        {"path": str(out_dir.resolve()), "train": "images/train", "val": "images/val",
         "names": dict(enumerate(class_names))}, sort_keys=False))
    return counts


def _shape(traces_of: Callable[[str, str], np.ndarray], job: str, channel: str) -> tuple[int, int]:
    n_traces, n_samples = traces_of(job, channel).shape
    return n_traces, n_samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export supervisor-reviewed targets as YOLO training data.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=session.DEFAULT_DATASET_DIR)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args(argv)
    config = load_config(args.config)

    def traces_of(job: str, channel: str) -> np.ndarray:
        return session.raw_traces(session.load_frame(session.resolve_job(job, args.dataset), channel))

    examples: list[Example] = []
    for job in session.list_jobs(args.dataset):
        reviews, picks = load_reviews(job), load_picks(job)
        try:
            reports = load_reports(job)
        except ValueError as exc:  # one unreadable file costs that line's field labels, not the export
            print(f"skipping field reports for {job}: {exc}")
            reports = []
        if reviews or picks:
            examples += examples_for_job(job, reviews, picks, partial(_shape, traces_of, job), reports)
    if not examples:
        print("nothing to export: no confirmed, rejected or picked targets yet")
        return 1
    counts = write_dataset(examples, traces_of, args.out, config.enhancement, config.detection.classes)
    print(f"exported {counts['examples']} tiles: {counts['positives']} labelled targets, "
          f"{counts['hard_negatives']} hard negatives, {counts['field_verified']} verified in the field -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
