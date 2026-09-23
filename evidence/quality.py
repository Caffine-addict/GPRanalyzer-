"""Evidence -> the PAS 128 survey quality level it supports, which today is: none of them.

Every number this system reports carries a confidence label (`core/contracts.py`), which is the
right internal discipline but not the language a client procures in. Utility surveys are
specified and accepted against a published ladder, so an automated interpretation has to say
where it lands on it — including, as here, that it lands nowhere yet.

**PAS 128:2022** (UK): QL-D is records only; QL-C is records reconciled with site features;
QL-B4 up to QL-B1 are geophysical detection with increasing confidence; QL-A is physical
verification by excavation. A `P` suffix (e.g. `QL-B2P`) marks post-processed data.

## Why this module grades almost nothing

Three independent blockers, each on its own fatal to a QL-B claim from this system:

1. **B1 needs more than one geophysical *technique*.** In practice EML plus GPR. RAD/RA1/RA2
   are three frequency channels of one antenna — one technique, however well they agree (see
   `docs/pilot/CHANNEL_IDENTITY.md`). B1 is therefore unreachable here by construction.
2. **B1 and B2 are accuracy bands, and no accuracy has ever been demonstrated.** They commit to
   stated position and depth tolerances. Nothing this project has found has been checked
   against an excavation or a utility record, so there is no error distribution to compare
   against any tolerance. Claiming the band would be asserting an accuracy nobody has measured.
3. **There is no georeferenced horizontal position at all.** Every QL-B level describes a
   position on a site plan. This system reports chainage along a line whose endpoints have not
   been surveyed in, and the onboard GPS cannot place it (`docs/pilot/GPS_DIAGNOSTIC.md`). A
   distance along an unplaced line is not a position on a drawing.

So `quality_level()` returns `"ungraded"` for every input, carrying the reason and the evidence
it does have. `provisional_ceiling` names what could eventually be supported and what has to
change first — a condition, never a grade. A grade only appears when a person supplies one
through `quality_from_external()`, or records an excavation via `verified_by_excavation`.

## What is deliberately NOT here

No ASCE 38 mapping. An earlier version of this module claimed ASCE 38-22 was "the same QL-D to
QL-A shape" and labelled one output as satisfying both standards. That was wrong in kind, not
just in detail: ASCE 38's QL-B covers horizontal position from surface geophysics, and does not
certify depth there at all, while this module's entire ladder was depth-driven. One label
cannot stand for both standards. If ASCE output is ever needed it belongs in its own module
with its own logic.

The grading rules above are the minimum needed to stop the system overclaiming. They are not a
faithful implementation of PAS 128:2022, and must not be extended into one from memory — nobody
on this project has worked from the standard's text. Get the document before building further.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.contracts import Evidence

QualityLevel = Literal["ungraded", "QL-D", "QL-C", "QL-B4", "QL-B3", "QL-B2", "QL-B1", "QL-A"]

# Ordered weakest to strongest, so a caller can compare or sort without hardcoding the ladder.
# "ungraded" is deliberately absent: it is the absence of a grade, not a rung below the lowest
# one, and putting it here would let it be sorted as though it ranked.
QUALITY_LADDER: tuple[QualityLevel, ...] = (
    "QL-D",
    "QL-C",
    "QL-B4",
    "QL-B3",
    "QL-B2",
    "QL-B1",
    "QL-A",
)

# Levels this system's own evidence can never support, for the reasons in the module docstring.
# `quality_level()` cannot return these; only `quality_from_external()` can, where a person is
# asserting what a different survey concluded.
GPR_UNREACHABLE_LEVELS: frozenset[str] = frozenset({"QL-B1", "QL-B2"})

_RATIONALE_VERIFIED = "verified by physical exposure, recorded by a person — not inferred here"

# The two blockers that no amount of better radar work can clear on its own. Stated once so the
# rationale and the provisional ceiling cannot drift apart.
_WHY_UNGRADED = (
    "not graded — GPR alone is a single geophysical technique, there is no ground-truth accuracy "
    "validation, and there is no georeferenced horizontal position"
)
_CEILING = (
    "could support at most QL-B2P, and only once: (1) depth accuracy is validated against "
    "ground truth across the depth range, (2) the line is georeferenced so chainage becomes a "
    "position on a drawing, and (3) the detection is traced as a linear feature rather than "
    "reported as a point anomaly. Until all three hold, no PAS 128 level would be supportable."
)


@dataclass(frozen=True)
class QualityAssessment:
    """What grade a finding supports, why, and what it would take to support more.

    `post_processed` maps to PAS 128's `P` suffix and is derived from the processing chain that
    was actually applied, not assumed. It is recorded even while `level` is `"ungraded"`, so
    that if a grade later becomes supportable the suffix is already known rather than re-guessed.

    It is **tri-state on purpose**: `None` means the processing provenance was never recorded,
    which is not the same claim as `False` ("recorded, and the data was raw"). Collapsing the two
    would assert raw data about a pick nobody captured a chain for — the same fabrication this
    project refuses for depth and position. `label` renders no `P` for either, but only because
    an unknown provenance cannot earn a suffix, not because the two are equivalent.

    `provisional_ceiling` is a conditional note and never a grade. It exists so that "ungraded"
    reads as a specific, closeable gap rather than a refusal.
    """

    level: QualityLevel
    rationale: str
    post_processed: bool | None = None
    provisional_ceiling: str | None = None

    @property
    def label(self) -> str:
        """The level as it is written on a deliverable, e.g. "QL-B2P".

        Only a recorded `True` earns the suffix. `None` (never recorded) must not, because the
        suffix is a positive claim about the data, and we would be making it without evidence.
        """
        if self.post_processed is True and self.level.startswith("QL-B"):
            return f"{self.level}P"
        return self.level

    @property
    def is_detection_grade(self) -> bool:
        """True for the QL-B band — geophysically detected, not verified and not records-only.

        False for `"ungraded"`: withholding a grade is not a detection grade.
        """
        return self.level.startswith("QL-B")


def _evidence_summary(evidence: Evidence, corroborating_channels: int) -> str:
    """What this finding does have, stated plainly, for the ungraded rationale.

    Withholding the grade must not withhold the measurements — a reader still needs to know
    whether the depth was measured or assumed, and how many channels agreed.
    """
    if evidence.position_confidence == "unavailable":
        return "no along-line position could be derived, so this cannot be placed even on its own line"

    if evidence.depth_confidence == "calibrated":
        depth = "depth measured from this target's own hyperbola"
    elif evidence.depth_confidence == "estimated":
        depth = "depth derived from an assumed velocity, not a measured one"
    else:
        depth = "no depth could be derived"

    if corroborating_channels >= 2:
        agreement = f", consistent across {corroborating_channels} frequency channels"
    else:
        agreement = ", seen on a single frequency channel"

    return f"{depth}{agreement}"


def quality_level(
    evidence: Evidence,
    *,
    corroborating_channels: int = 1,
    post_processed: bool | None = None,
    verified_by_excavation: bool = False,
) -> QualityAssessment:
    """Grade one piece of evidence — which, absent an excavation, means declining to grade it.

    Args:
        evidence: the finding's evidence. Its confidence labels shape the rationale, but they
            cannot earn a PAS level; see the module docstring for why.
        corroborating_channels: how many *distinct frequency channels* saw this target and
            agreed — `studio/corroborate.py` computes it. Reported in the rationale as evidence,
            but it does not move the grade: more channels is still one technique.
        post_processed: whether the data behind this was post-processed (PAS 128's `P` suffix).
            Derive it from the chain actually applied — `studio.processing.is_post_processed` —
            rather than passing a literal. Pass `None`, the default, when no chain was recorded:
            that is a different claim from `False` and is carried through as one.
        verified_by_excavation: set only when someone physically exposed the utility. Never
            inferred from any measurement.

    Returns:
        `"ungraded"` with its reason and a provisional ceiling, or `"QL-A"` for a recorded
        excavation. Never `"QL-B1"` or `"QL-B2"`.
    """
    if corroborating_channels < 1:
        raise ValueError(f"corroborating_channels must be at least 1, got {corroborating_channels}")

    if verified_by_excavation:
        return QualityAssessment("QL-A", _RATIONALE_VERIFIED, post_processed)

    summary = _evidence_summary(evidence, corroborating_channels)
    return QualityAssessment(
        "ungraded",
        f"{summary} — {_WHY_UNGRADED}",
        post_processed,
        provisional_ceiling=_CEILING,
    )


def quality_from_external(
    level: QualityLevel,
    basis: str,
    *,
    post_processed: bool | None = None,
) -> QualityAssessment:
    """A grade asserted from outside this system — a second technique, another surveyor's work.

    This is the only route to QL-B1 or QL-B2, and it is deliberately a human assertion with a
    stated basis rather than anything inferred. Same shape as `verified_by_excavation`: the
    system records what a person determined out of band, and never derives it.

    Args:
        level: any level on `QUALITY_LADDER`. `"ungraded"` is not a grade and is refused.
        basis: what supports it — the techniques used, who surveyed it. Required; an
            externally-asserted grade with no stated basis is exactly the unsourced number this
            project refuses everywhere else.
    """
    if level not in QUALITY_LADDER:
        raise ValueError(f"{level!r} is not a PAS 128 level — expected one of {list(QUALITY_LADDER)}")
    if not basis.strip():
        raise ValueError("an externally supplied grade needs a stated basis, not an empty string")
    return QualityAssessment(level, f"externally graded: {basis.strip()}", post_processed)


def quality_from_records_only() -> QualityAssessment:
    """The grade for a utility taken from existing records with no survey behind it.

    Here so that a report mixing surveyed and recorded utilities can label both on one ladder,
    rather than leaving the recorded ones unlabelled and implicitly as good as the surveyed ones.
    """
    return QualityAssessment(
        "QL-D",
        "from existing records only, with no geophysical detection or site reconciliation",
    )
