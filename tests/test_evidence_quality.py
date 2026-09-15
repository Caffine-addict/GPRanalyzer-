"""Tests for evidence/quality.py — the PAS 128 / ASCE 38 grade for a piece of evidence.

The two rules that must never bend are pinned first: QL-A cannot be reached by measurement, and
QL-B1 cannot be reached from an assumed velocity.
"""

from __future__ import annotations

import pytest

from core.contracts import Evidence
from evidence.quality import (
    QUALITY_LADDER,
    quality_from_records_only,
    quality_level,
)


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


# --------------------------------------------------------------------- the two unbendable rules


def test_no_measurement_can_reach_ql_a() -> None:
    # QL-A means somebody dug and looked. Perfect evidence on every axis must not earn it.
    perfect = _evidence(depth_confidence="calibrated", amplitude_confidence="calibrated")
    assessment = quality_level(perfect, corroborating_channels=3)
    assert assessment.level == "QL-B1"
    assert assessment.level != "QL-A"


def test_ql_a_only_when_a_person_records_an_excavation() -> None:
    assessment = quality_level(_evidence(), verified_by_excavation=True)
    assert assessment.level == "QL-A"
    assert "not inferred" in assessment.rationale


def test_an_assumed_velocity_cannot_reach_ql_b1_however_corroborated() -> None:
    # depth_confidence "estimated" means the velocity came from the file header, not the target.
    assessment = quality_level(_evidence(depth_confidence="estimated"), corroborating_channels=3)
    assert assessment.level == "QL-B4"


# --------------------------------------------------------------------- the ladder


def test_measured_depth_corroborated_across_channels_is_ql_b1() -> None:
    assessment = quality_level(
        _evidence(depth_confidence="calibrated"), corroborating_channels=2
    )
    assert assessment.level == "QL-B1"
    assert "corroborated across 2 independent channels" in assessment.rationale


def test_measured_depth_on_one_channel_is_ql_b2() -> None:
    assessment = quality_level(
        _evidence(depth_confidence="calibrated"), corroborating_channels=1
    )
    assert assessment.level == "QL-B2"
    assert "not independently corroborated" in assessment.rationale


def test_position_without_depth_is_ql_b3() -> None:
    assessment = quality_level(_evidence(depth_m=None, depth_confidence="unavailable"))
    assert assessment.level == "QL-B3"
    assert "no vertical information" in assessment.rationale


def test_estimated_depth_is_ql_b4() -> None:
    assessment = quality_level(_evidence(depth_confidence="estimated"))
    assert assessment.level == "QL-B4"
    assert "assumed velocity" in assessment.rationale


def test_no_position_is_ql_d() -> None:
    assessment = quality_level(
        _evidence(position_m=None, position_confidence="unavailable")
    )
    assert assessment.level == "QL-D"
    assert "cannot be placed on a drawing" in assessment.rationale


def test_records_only_is_ql_d_and_says_so() -> None:
    assessment = quality_from_records_only()
    assert assessment.level == "QL-D"
    assert "existing records only" in assessment.rationale


# --------------------------------------------------------------------- labelling


def test_the_p_suffix_marks_post_processed_detection_grades() -> None:
    assessment = quality_level(
        _evidence(depth_confidence="calibrated"), corroborating_channels=1, post_processed=True
    )
    assert assessment.level == "QL-B2"
    assert assessment.label == "QL-B2P"


def test_the_p_suffix_is_not_applied_outside_the_b_band() -> None:
    # There is no "QL-DP" or "QL-AP" in PAS 128 — the suffix is about geophysical interpretation.
    records = quality_level(
        _evidence(position_m=None, position_confidence="unavailable"), post_processed=True
    )
    assert records.label == "QL-D"
    verified = quality_level(_evidence(), verified_by_excavation=True, post_processed=True)
    assert verified.label == "QL-A"


def test_detection_grade_flag_covers_only_the_b_band() -> None:
    assert quality_level(_evidence()).is_detection_grade is True
    assert quality_level(_evidence(), verified_by_excavation=True).is_detection_grade is False
    assert quality_from_records_only().is_detection_grade is False


# --------------------------------------------------------------------- guards


def test_zero_corroborating_channels_is_refused() -> None:
    # One channel is the floor: the evidence itself came from somewhere.
    with pytest.raises(ValueError, match="at least 1"):
        quality_level(_evidence(), corroborating_channels=0)


def test_the_ladder_is_ordered_weakest_to_strongest() -> None:
    assert QUALITY_LADDER[0] == "QL-D"
    assert QUALITY_LADDER[-1] == "QL-A"
    assert QUALITY_LADDER.index("QL-B1") > QUALITY_LADDER.index("QL-B4")
