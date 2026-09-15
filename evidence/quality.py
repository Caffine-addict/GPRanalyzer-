"""Evidence -> a survey quality level the industry already understands (PAS 128 / ASCE 38).

Every number this system reports has a confidence label (`core/contracts.py`), which is the
right internal discipline but not the language a client procures in. Utility surveys are
specified and accepted against a published ladder, and an automated interpretation has to land
somewhere on it:

- **PAS 128:2022** (UK): QL-D is records only; QL-C is records reconciled with site features;
  QL-B4 up to QL-B1 are geophysical detection with increasing confidence; QL-A is physical
  verification by excavation. QL-B1 is the top *detection* grade and requires the utility to be
  traced continuously with position **and** depth well supported — in practice corroborated by
  more than one method. A `P` suffix (e.g. `QL-B2P`) marks that the data was post-processed,
  which raises interpretability on cluttered sites.
- **ASCE 38-22** (US): the same QL-D to QL-A shape, with QL-A meaning verified by exposure.

Two rules this module will not break, because breaking them is how a survey tool becomes a
liability:

1. **QL-A is unreachable from here, always.** It means somebody exposed the utility. No amount
   of radar, corroboration or reasoning earns it. `verified_by_excavation` exists so a human can
   record that it happened out of band, and is never inferred.
2. **QL-B1 needs depth that was measured, not assumed.** On this project that means a velocity
   fitted from the target's own hyperbola (`depth_confidence == "calibrated"`), plus agreement
   from an independent channel. A header permittivity typed in on site is an assumption, and an
   assumption cannot support the highest detection grade however good the detector is.

The research consensus behind this: the 2026 review of GPR utility detection identifies
"event-utility mismatch" — detecting a hyperbola is not identifying a utility — as a primary
obstacle, and names uncertainty quantification as a required direction. A quality level is that
uncertainty, expressed in the form the industry already audits against. None of the commercial
AI-GPR products surveyed map their output to these levels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.contracts import Evidence

QualityLevel = Literal["QL-D", "QL-C", "QL-B4", "QL-B3", "QL-B2", "QL-B1", "QL-A"]

# Ordered weakest to strongest, so a caller can compare or sort without hardcoding the ladder.
QUALITY_LADDER: tuple[QualityLevel, ...] = (
    "QL-D",
    "QL-C",
    "QL-B4",
    "QL-B3",
    "QL-B2",
    "QL-B1",
    "QL-A",
)

_RATIONALE_VERIFIED = "verified by physical exposure, recorded by a person — not inferred here"


@dataclass(frozen=True)
class QualityAssessment:
    """One finding's survey quality level, with the reason it landed there.

    `post_processed` maps to PAS 128's `P` suffix. `label` is what goes on a drawing.
    """

    level: QualityLevel
    rationale: str
    post_processed: bool = False

    @property
    def label(self) -> str:
        """The level as it is written on a deliverable, e.g. "QL-B2P"."""
        if self.post_processed and self.level.startswith("QL-B"):
            return f"{self.level}P"
        return self.level

    @property
    def is_detection_grade(self) -> bool:
        """True for the QL-B band — geophysically detected, not verified and not records-only."""
        return self.level.startswith("QL-B")


def quality_level(
    evidence: Evidence,
    *,
    corroborating_channels: int = 1,
    post_processed: bool = False,
    verified_by_excavation: bool = False,
) -> QualityAssessment:
    """Grade one piece of evidence against the PAS 128 / ASCE 38 ladder.

    Args:
        evidence: the finding's evidence, whose confidence labels drive the grade.
        corroborating_channels: how many *distinct* receivers saw this target and agreed —
            `studio/corroborate.py` computes it. 1 means a single channel, which is the normal
            case and caps the grade below QL-B1.
        post_processed: whether the data behind this was post-processed (PAS 128's `P` suffix).
        verified_by_excavation: set only when someone physically exposed the utility. Never
            inferred from any measurement.

    Returns:
        The level, and the specific reason for it.
    """
    if corroborating_channels < 1:
        raise ValueError(f"corroborating_channels must be at least 1, got {corroborating_channels}")

    if verified_by_excavation:
        return QualityAssessment("QL-A", _RATIONALE_VERIFIED, post_processed)

    has_position = evidence.position_confidence != "unavailable"
    has_depth = evidence.depth_confidence != "unavailable"
    depth_measured = evidence.depth_confidence == "calibrated"
    position_measured = evidence.position_confidence == "calibrated"

    # Nothing located at all. Not a detection in any reportable sense.
    if not has_position:
        return QualityAssessment(
            "QL-D",
            "no position could be derived, so this cannot be placed on a drawing — "
            "reportable only as an indication that something was detected",
            post_processed,
        )

    # Positioned but with no depth: PAS 128's QL-B3 is exactly "position known, depth not".
    if not has_depth:
        return QualityAssessment(
            "QL-B3",
            "horizontal position available but no depth could be derived — "
            "plan position only, no vertical information",
            post_processed,
        )

    # The top detection grade: depth actually measured, and an independent channel agrees.
    if depth_measured and position_measured and corroborating_channels >= 2:
        return QualityAssessment(
            "QL-B1",
            f"depth measured from the target's own hyperbola and corroborated across "
            f"{corroborating_channels} independent channels, with encoder-measured position",
            post_processed,
        )

    # Measured depth on one channel only. Good, but uncorroborated.
    if depth_measured:
        return QualityAssessment(
            "QL-B2",
            "depth measured from the target's own hyperbola on a single channel — "
            "good confidence in position and depth, but not independently corroborated",
            post_processed,
        )

    # Depth exists but rests on an assumed velocity. This is the common case, and it must not
    # dress itself up: an assumed permittivity is not a measurement.
    return QualityAssessment(
        "QL-B4",
        "position available and a depth estimated from an assumed velocity, not a measured one — "
        "the lowest geophysical detection grade, and the honest one for header-derived depth",
        post_processed,
    )


def quality_from_records_only() -> QualityAssessment:
    """The grade for a utility taken from existing records with no survey behind it.

    Here so that a report mixing surveyed and recorded utilities can label both on one ladder,
    rather than leaving the recorded ones unlabelled and implicitly as good as the surveyed ones.
    """
    return QualityAssessment(
        "QL-D",
        "from existing records only, with no geophysical detection or site reconciliation",
    )
