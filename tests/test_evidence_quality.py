"""Tests for evidence/quality.py — what PAS 128 grade, if any, a piece of evidence supports.

The rules that must never bend are pinned first. Two of them are structural: a grade this
system cannot honestly support must be *unreachable*, not merely unlikely.

- **QL-A cannot be reached by measurement.** It means somebody exposed the utility.
- **QL-B1 and QL-B2 cannot be reached at all from this system's own evidence.** B1 requires
  detection by more than one geophysical *technique*; three frequency channels of GPR are one
  technique (see docs/pilot/CHANNEL_IDENTITY.md). B1 and B2 are also accuracy bands, and this
  project has no ground truth against which any accuracy has been demonstrated. Both are
  reachable only through `quality_from_external()`, where a human records what an external
  survey concluded.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from core.config import load_config
from core.contracts import Evidence
from evidence.quality import (
    GPR_UNREACHABLE_LEVELS,
    QUALITY_LADDER,
    quality_from_external,
    quality_from_records_only,
    quality_level,
)

_CONFIDENCES = ("calibrated", "estimated", "unavailable")

# Loaded from config rather than hardcoded: if the taxonomy changes, this sweep must follow it
# automatically, or it quietly stops covering the classes that actually exist.
_TAXONOMY = tuple(load_config(Path("config.yaml")).detection.taxonomy)

# A mutation keyed on a field the fixture holds constant survives the sweep — this was found by
# mutation testing, not theory: a branch returning QL-B2 for `detection_class == "cavities"`
# passed the entire suite, because every Evidence built here was a "clear_point_reflector".
# So the sweep varies every field quality_level's inputs expose, not only its keyword arguments.
_DETECTION_CONFIDENCES = (0.05, 1.0)
_WIDTHS = (4.0, 24.0, 250.0)


def _evidence(**overrides: object) -> Evidence:
    base: dict[str, object] = {
        "detection_class": "clear_point_reflector",
        "detection_confidence": 1.0,
        "depth_m": 1.3,
        "depth_confidence": "estimated",
        "position_m": 8.55,
        "position_confidence": "calibrated",
        "amplitude": 4.2,
        "amplitude_confidence": "estimated",
        "hyperbola_width_px": 24.0,
    }
    base.update(overrides)
    return Evidence(**base)  # type: ignore[arg-type]


def _evidence_for(
    depth_c: str,
    position_c: str,
    amplitude_c: str,
    *,
    detection_class: str = "clear_point_reflector",
    detection_confidence: float = 1.0,
    hyperbola_width_px: float = 24.0,
) -> Evidence:
    """Evidence honouring the value/confidence invariant for an arbitrary combination."""
    return _evidence(
        detection_class=detection_class,
        detection_confidence=detection_confidence,
        hyperbola_width_px=hyperbola_width_px,
        depth_m=None if depth_c == "unavailable" else 1.3,
        depth_confidence=depth_c,
        position_m=None if position_c == "unavailable" else 8.55,
        position_confidence=position_c,
        amplitude=None if amplitude_c == "unavailable" else 4.2,
        amplitude_confidence=amplitude_c,
    )


# ------------------------------------------------------- the structural rules (property sweep)


def test_no_input_whatsoever_can_reach_b1_or_b2() -> None:
    """Exhaustive sweep of the whole input space. This is the rule that must not bend.

    Hand-picked cases prove a path exists; they cannot prove no path exists. This walks every
    combination of the inputs `quality_level` can see, so a future edit that reintroduces a B1
    or B2 return fails here regardless of which branch it hides in.

    **It varies the Evidence fields too, not just the keyword arguments.** An earlier version
    held `detection_class`, `detection_confidence` and `hyperbola_width_px` at fixture constants,
    and mutation testing showed a branch keyed on `detection_class == "cavities"` returning
    QL-B2 survived the entire 923-test suite. A sweep that misses a field is not a sweep.
    """
    checked = 0
    for depth_c, position_c, amplitude_c in itertools.product(_CONFIDENCES, repeat=3):
        for detection_class in _TAXONOMY:
            for detection_confidence in _DETECTION_CONFIDENCES:
                for width in _WIDTHS:
                    evidence = _evidence_for(
                        depth_c, position_c, amplitude_c,
                        detection_class=detection_class,
                        detection_confidence=detection_confidence,
                        hyperbola_width_px=width,
                    )
                    for channels in (1, 2, 3, 10):
                        for post_processed in (False, True, None):
                            for verified in (False, True):
                                assessment = quality_level(
                                    evidence,
                                    corroborating_channels=channels,
                                    post_processed=post_processed,
                                    verified_by_excavation=verified,
                                )
                                checked += 1
                                assert assessment.level not in GPR_UNREACHABLE_LEVELS, (
                                    f"reached {assessment.level} from class={detection_class} "
                                    f"conf={detection_confidence} width={width} depth={depth_c} "
                                    f"position={position_c} amplitude={amplitude_c} "
                                    f"channels={channels} post_processed={post_processed} "
                                    f"verified={verified}"
                                )
                                assert "QL-B1" not in assessment.label
                                assert "QL-B2" not in assessment.label
    expected = 27 * len(_TAXONOMY) * 2 * 3 * 4 * 3 * 2
    assert checked == expected, f"sweep covered {checked} combinations, expected {expected}"
    assert len(_TAXONOMY) == 9  # the real taxonomy, not an empty list that would pass vacuously


def test_ql_a_is_reachable_only_by_a_recorded_excavation() -> None:
    for depth_c, position_c, amplitude_c in itertools.product(_CONFIDENCES, repeat=3):
        evidence = _evidence_for(depth_c, position_c, amplitude_c)
        for channels in range(1, 11):
            assert quality_level(evidence, corroborating_channels=channels).level != "QL-A"


def test_a_recorded_excavation_still_earns_ql_a() -> None:
    assessment = quality_level(_evidence(), verified_by_excavation=True)
    assert assessment.level == "QL-A"
    assert "not inferred" in assessment.rationale


def test_b1_and_b2_remain_on_the_ladder_even_though_we_cannot_reach_them() -> None:
    # They are real PAS levels. Externally-graded data has to be representable on the same
    # ladder, or a report mixing our findings with a third party's cannot be ordered at all.
    assert "QL-B1" in QUALITY_LADDER
    assert "QL-B2" in QUALITY_LADDER
    assert GPR_UNREACHABLE_LEVELS == frozenset({"QL-B1", "QL-B2"})


# ------------------------------------------------------------------- the default: ungraded


def test_the_best_possible_evidence_is_still_ungraded() -> None:
    best = _evidence(depth_confidence="calibrated", amplitude_confidence="calibrated")
    assessment = quality_level(best, corroborating_channels=3)
    assert assessment.level == "ungraded"
    assert assessment.label == "ungraded"


def test_ungraded_says_why_it_is_ungraded() -> None:
    assessment = quality_level(_evidence(depth_confidence="calibrated"))
    assert "no ground-truth accuracy validation" in assessment.rationale
    assert "no georeferenced horizontal position" in assessment.rationale


def test_ungraded_still_reports_the_evidence_it_has() -> None:
    # The grade is withheld; the measurements are not. A reader must still learn that the
    # depth was measured rather than assumed, and how many channels agreed.
    measured = quality_level(_evidence(depth_confidence="calibrated"), corroborating_channels=2)
    assert "own hyperbola" in measured.rationale
    assert "2 frequency channels" in measured.rationale

    assumed = quality_level(_evidence(depth_confidence="estimated"))
    assert "assumed velocity" in assumed.rationale

    none_at_all = quality_level(_evidence(depth_m=None, depth_confidence="unavailable"))
    assert "no depth" in none_at_all.rationale


def test_a_finding_with_no_position_says_so() -> None:
    assessment = quality_level(_evidence(position_m=None, position_confidence="unavailable"))
    assert assessment.level == "ungraded"
    assert "no along-line position" in assessment.rationale


def test_ungraded_is_not_a_detection_grade() -> None:
    assert quality_level(_evidence()).is_detection_grade is False
    assert quality_level(_evidence(), verified_by_excavation=True).is_detection_grade is False
    assert quality_from_records_only().is_detection_grade is False


def test_ungraded_is_not_on_the_ladder() -> None:
    # It is the absence of a grade, not a rung below the lowest one. Putting it on the ladder
    # would let it be compared and sorted as though it ranked.
    assert "ungraded" not in QUALITY_LADDER


# ---------------------------------------------------------------- the provisional ceiling


def test_the_provisional_ceiling_is_a_condition_never_a_grade() -> None:
    assessment = quality_level(_evidence(depth_confidence="calibrated"), corroborating_channels=2)
    assert assessment.level == "ungraded"
    assert assessment.provisional_ceiling is not None
    # It may name a rung, but only as something that could be supported later.
    assert "QL-B2P" in assessment.provisional_ceiling
    assert "would" in assessment.provisional_ceiling or "could" in assessment.provisional_ceiling
    # And naming it must not leak into the grade itself.
    assert assessment.label == "ungraded"


def test_the_ceiling_names_every_blocker_not_just_one() -> None:
    ceiling = quality_level(_evidence(depth_confidence="calibrated")).provisional_ceiling
    assert ceiling is not None
    assert "ground truth" in ceiling
    assert "georeferenc" in ceiling


def test_an_excavated_finding_has_no_provisional_ceiling() -> None:
    # It is already graded as high as the ladder goes; a "could reach" note is meaningless.
    assert quality_level(_evidence(), verified_by_excavation=True).provisional_ceiling is None


# ------------------------------------------------------------------ the P suffix, derived


def test_the_p_suffix_is_recorded_even_while_ungraded() -> None:
    # The suffix describes the data, not the grade. It must survive being ungraded so that the
    # moment a grade becomes supportable, the P is already known rather than re-guessed.
    assessment = quality_level(_evidence(depth_confidence="calibrated"), post_processed=True)
    assert assessment.post_processed is True
    assert assessment.label == "ungraded"


def test_the_p_suffix_applies_to_an_external_b_band_grade() -> None:
    assert quality_from_external("QL-B2", "EML + GPR agreed", post_processed=True).label == "QL-B2P"
    assert quality_from_external("QL-B2", "EML + GPR agreed").label == "QL-B2"


def test_the_p_suffix_is_not_applied_outside_the_b_band() -> None:
    # There is no "QL-DP" or "QL-AP" in PAS 128 — the suffix is about geophysical interpretation.
    assert quality_from_external("QL-A", "dug and measured", post_processed=True).label == "QL-A"
    assert quality_from_records_only().label == "QL-D"


# ------------------------------------------------------------------- the external escape hatch


def test_external_grading_can_reach_b1_because_a_person_asserted_it() -> None:
    assessment = quality_from_external("QL-B1", "EML and GPR agreed on site, surveyed in")
    assert assessment.level == "QL-B1"
    assert assessment.rationale.startswith("externally graded")
    assert "EML and GPR agreed" in assessment.rationale


def test_external_grading_refuses_a_level_that_is_not_on_the_ladder() -> None:
    with pytest.raises(ValueError, match="not a PAS 128 level"):
        quality_from_external("QL-B9", "nonsense")
    with pytest.raises(ValueError, match="not a PAS 128 level"):
        quality_from_external("ungraded", "that is not a grade")


def test_external_grading_demands_a_reason() -> None:
    # An externally-asserted grade with no stated basis is exactly the unsourced number this
    # project refuses everywhere else.
    with pytest.raises(ValueError, match="basis"):
        quality_from_external("QL-B1", "   ")


# ------------------------------------------------------------------------------- unchanged


def test_records_only_is_ql_d_and_says_so() -> None:
    assessment = quality_from_records_only()
    assert assessment.level == "QL-D"
    assert "existing records only" in assessment.rationale


def test_zero_corroborating_channels_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        quality_level(_evidence(), corroborating_channels=0)


def test_the_ladder_is_ordered_weakest_to_strongest() -> None:
    assert QUALITY_LADDER[0] == "QL-D"
    assert QUALITY_LADDER[-1] == "QL-A"
    assert QUALITY_LADDER.index("QL-B1") > QUALITY_LADDER.index("QL-B4")


def test_unrecorded_processing_is_carried_through_as_none_not_false() -> None:
    """`None` and `False` are different claims and must not be collapsed en route to the grade.

    `None` means nobody recorded what chain the target was picked on; `False` means the chain
    was checked and altered nothing. Coercing the first into the second asserts raw data about
    a pick whose provenance was never captured — the same fabrication this project refuses for
    depth and position. Caught by mutation testing: hardcoding `False` at the call sites left
    the entire suite green.
    """
    evidence = _evidence(depth_confidence="calibrated")
    assert quality_level(evidence, post_processed=None).post_processed is None
    assert quality_level(evidence, post_processed=False).post_processed is False
    assert quality_level(evidence, post_processed=True).post_processed is True
    # Default is "not recorded", never "raw".
    assert quality_level(evidence).post_processed is None


def test_only_a_recorded_true_earns_the_p_suffix() -> None:
    # An unknown provenance cannot earn a suffix: the P is a positive claim about the data.
    assert quality_from_external("QL-B2", "EML + GPR", post_processed=True).label == "QL-B2P"
    assert quality_from_external("QL-B2", "EML + GPR", post_processed=False).label == "QL-B2"
    assert quality_from_external("QL-B2", "EML + GPR", post_processed=None).label == "QL-B2"
