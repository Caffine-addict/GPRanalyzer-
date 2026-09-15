"""Read-only view of the Phase-1 candidate boxes and their shape diagnoses.

`scripts/detect_candidates.py` finds regions worth a second look and
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

from dataclasses import asdict, dataclass
from pathlib import Path

from core import boxes as box_store
from studio.diagnose import diagnose_job


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
            )
        )
    return candidates
