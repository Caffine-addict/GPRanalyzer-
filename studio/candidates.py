"""Read-only view of the Phase-1 candidate boxes and their shape diagnoses.

Candidates come from `detect/migration_picker.py`, generated the first time a job's candidates are
asked for, so an imported line has them without anyone running a script (the older
`scripts/detect_candidates.py` boxes stay alongside it where they were stored). Then
`studio/diagnose.py` measures each one's shape — hyperbola fit quality,
coherence, aspect, implied permittivity — and *suggests* a class. Both already
persist to `annotations/<job>/`. This module joins the two so the Studio can
draw them over the radargram the interpreter is actually looking at.

Read-only, and deliberately so. A suggestion drawn on the same canvas as the
data is easy to start reading as a label, so the Studio shows candidates and
never edits them: interpretation happens through picks (`studio/picks.py`),
which record who decided and on what basis. The suggestion is a prompt to
look, not an answer.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from core import boxes as box_store
from core.annotation_io import job_lock, write_text_atomically
from detect.channel_fusion import ChannelPicks, fuse, time_zero_ns
from detect.migration_picker import pick_targets
from studio import session
from studio.diagnose import diagnose_job
from studio.processing import SPEED_OF_LIGHT_M_PER_NS

MIGRATION_DETECTOR = "migration-picker"
MIGRATION_DETECTOR_VERSION = "2"  # 2: picks merged across channels (corroboration)
_BOX_ABOVE_NS, _BOX_BELOW_NS = 1.0, 3.0
_MIN_HALF_WIDTH_M, _MAX_HALF_WIDTH_M = 0.2, 1.5
_DEFAULT_DIELECTRIC = 9.0
_MARKER = "candidates.json"  # "this job's picker run happened", even when it found nothing
logger = logging.getLogger(__name__)


def hyperbola_half_width_m(apex_ns: float, velocity_m_per_ns: float, limb_delay_ns: float = _BOX_BELOW_NS) -> float:
    """How far either side of the apex the limbs reach before falling `limb_delay_ns` below it.

    From t(x)^2 = t0^2 + (2x/v)^2: a deep target's hyperbola is wider than a shallow one's, so a
    fixed-width box would clip one and pad the other with noise for the diagnoser to fit.
    """
    half = 0.5 * velocity_m_per_ns * float(np.sqrt((apex_ns + limb_delay_ns) ** 2 - apex_ns**2))
    return float(np.clip(half, _MIN_HALF_WIDTH_M, _MAX_HALF_WIDTH_M))


@dataclass(frozen=True)
class Candidate:
    """One candidate box with whatever the diagnoser could measure about it."""

    id: str
    channel: str
    x: float  # native trace index of the box's left edge
    y: float  # native sample index of its top edge
    w: float
    h: float
    note: str
    suggested_class: str | None
    shape: str | None
    rationale: str | None
    fit_r2: float | None
    implied_dielectric: float | None
    corroboration: int | None = None  # channels that saw this target; more is likelier real


def generate_candidates(job_name: str, job_dir: Path) -> list[box_store.Box]:
    """Run the migration picker on every channel of the job and store its boxes.

    A box spans the apex and the hyperbola's limbs down to a few ns below it, sized from the
    hyperbola itself at the migration velocity. A channel that cannot be picked is logged and
    skipped; the others still get their boxes.
    """
    failed: list[str] = []
    per_channel = {}
    for info in session.available_channels(job_dir):
        frame = session.load_frame(job_dir, info.extension)
        traces = session.raw_traces(frame)
        dielectric = frame.dielectric_assumed or _DEFAULT_DIELECTRIC
        try:
            picks = pick_targets(traces, sample_interval_ns=info.sample_interval_ns,
                                 trace_spacing_m=info.trace_spacing_m, dielectric=dielectric)
        except ValueError as exc:
            logger.warning("migration picker skipped %s %s: %s", job_name, info.extension, exc)
            failed.append(info.extension)
            continue
        per_channel[info.extension] = (info, dielectric, ChannelPicks(
            picks, info.sample_interval_ns, time_zero_ns(traces, info.sample_interval_ns)))

    # Channels of one line share position and (from each channel's own time zero) time, so a
    # target seen on several of them is likelier real. Lines from other radars have one channel.
    seen_on = {}
    spacings = {info.trace_spacing_m for info, _, _ in per_channel.values()}
    if len(per_channel) > 1 and len(spacings) == 1:
        for target in fuse({name: ch for name, (_, _, ch) in per_channel.items()}, spacings.pop()):
            for name, pick in target.members:
                seen_on[(name, pick.trace, pick.sample)] = len(target.channels)

    found = []
    for name, (info, dielectric, channel) in per_channel.items():
        dt, spacing = info.sample_interval_ns, info.trace_spacing_m
        velocity = SPEED_OF_LIGHT_M_PER_NS / float(np.sqrt(dielectric))
        for pick in channel.picks:
            half = hyperbola_half_width_m(pick.sample * dt, velocity) / spacing
            x0, x1 = max(0.0, pick.trace - half), min(info.n_traces - 1.0, pick.trace + half)
            y0 = max(0.0, pick.sample - _BOX_ABOVE_NS / dt)
            y1 = min(info.n_samples - 1.0, pick.sample + _BOX_BELOW_NS / dt)
            corroboration = seen_on.get((name, pick.trace, pick.sample))
            seen = f"; seen on {corroboration} channels" if corroboration and corroboration > 1 else ""
            if x1 > x0 and y1 > y0:
                found.append(box_store.DetectedBox(channel=name, x=x0, y=y0, w=x1 - x0, h=y1 - y0,
                                                   note=f"auto: migration focus, {pick.strength:.1f}x background{seen}",
                                                   corroboration=corroboration))
    boxes = box_store.replace_detector_boxes(job_name, detector=MIGRATION_DETECTOR,
                                             detector_version=MIGRATION_DETECTOR_VERSION, boxes=found)
    write_text_atomically(box_store.boxes_path(job_name).parent / _MARKER, json.dumps(
        {"detector": MIGRATION_DETECTOR, "version": MIGRATION_DETECTOR_VERSION, "boxes": len(found),
         "channels_skipped": failed}) + "\n")
    return boxes


def ensure_candidates(job_name: str, job_dir: Path) -> None:
    """Generate the migration picker's boxes once per job; later calls only read.

    The check and the run share the job's lock, so two first requests run the picker once. A
    marker records that the run happened, so a line where it found nothing (or skipped a channel)
    is not re-migrated on every request.
    """
    marker = box_store.boxes_path(job_name).parent / _MARKER
    with job_lock(marker.parent):
        if _done(marker):
            return
        generate_candidates(job_name, job_dir)


def _done(marker: Path) -> bool:
    try:
        recorded = json.loads(marker.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return recorded.get("detector") == MIGRATION_DETECTOR and recorded.get("version") == MIGRATION_DETECTOR_VERSION


def load_candidates(job_name: str, job_dir: Path) -> list[Candidate]:
    """Boxes for a job, each joined to its diagnosis when one exists.

    A box with no diagnosis is still returned, with every diagnostic field
    None — it is a real region someone flagged, and dropping it because the
    measurement step hasn't run would hide it entirely.
    """
    boxes = box_store.load_boxes(job_name)
    if not boxes:
        return []

    diagnoses = {diagnosis.box_id: diagnosis for diagnosis in diagnose_job(job_dir)}
    candidates = []
    for box in boxes:
        raw = asdict(box)
        diagnosis = diagnoses.get(box.id)
        candidates.append(
            Candidate(
                id=box.id,
                channel=raw["channel"],
                x=raw["x"],
                y=raw["y"],
                w=raw["w"],
                h=raw["h"],
                note=raw.get("note", ""),
                suggested_class=diagnosis.suggested_class if diagnosis else None,
                shape=diagnosis.shape if diagnosis else None,
                rationale=diagnosis.rationale if diagnosis else None,
                fit_r2=diagnosis.fit_r2 if diagnosis else None,
                implied_dielectric=diagnosis.implied_dielectric if diagnosis else None,
                corroboration=raw.get("corroboration"),
            )
        )
    return candidates
