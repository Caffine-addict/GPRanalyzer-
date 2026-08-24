"""Tests for risk/score.py — every threshold boundary and every escalation rule, individually."""

from __future__ import annotations

import pytest

from core.config import RiskConfig
from core.contracts import Detection
from risk.score import _escalation_rules, _level_from_score, _weighted_score, score_detections


def _config(**overrides: object) -> RiskConfig:
    base: dict[str, object] = {
        "weights": {"elongated_linear_target": 1.0, "cavities": 0.9, "low_snr_point_reflector": 0.4},
        "default_weight": 0.2,
        "low_max": 0.35,
        "medium_max": 0.65,
        "utility_classes": ("elongated_linear_target", "intersecting_linear_and_point_reflector"),
        "high_confidence_elongated_threshold": 0.8,
        "high_confidence_elongated_min_count": 2,
    }
    base.update(overrides)
    return RiskConfig(**base)  # type: ignore[arg-type]


def _detection(
    class_name: str = "cavities", confidence: float = 0.9, bbox: tuple = (0.0, 0.0, 10.0, 10.0)
) -> Detection:
    return Detection(class_name=class_name, confidence=confidence, bbox_xyxy=bbox)


# --- threshold boundaries ---


def test_level_zero_is_low() -> None:
    assert _level_from_score(0.0, _config()) == "LOW"


def test_level_just_below_low_max_is_low() -> None:
    assert _level_from_score(0.349999, _config()) == "LOW"


def test_level_exactly_low_max_is_medium() -> None:
    # low_max is an exclusive upper bound for LOW.
    assert _level_from_score(0.35, _config()) == "MEDIUM"


def test_level_just_below_medium_max_is_medium() -> None:
    assert _level_from_score(0.649999, _config()) == "MEDIUM"


def test_level_exactly_medium_max_is_high() -> None:
    # medium_max is an exclusive upper bound for MEDIUM.
    assert _level_from_score(0.65, _config()) == "HIGH"


def test_level_well_above_medium_max_is_high() -> None:
    assert _level_from_score(1.0, _config()) == "HIGH"


# --- weighted score formula ---


def test_weighted_score_empty_detections_is_zero() -> None:
    assert _weighted_score([], _config()) == 0.0


def test_weighted_score_single_detection() -> None:
    score = _weighted_score([_detection("cavities", 0.9)], _config())
    assert score == pytest.approx(0.9 * 0.9)  # weight=0.9, count=1, avg_conf=0.9, /1


def test_weighted_score_unweighted_class_uses_default_weight() -> None:
    score = _weighted_score([_detection("disturbed_zone", 0.5)], _config())
    assert score == pytest.approx(0.2 * 0.5)  # not in weights dict -> default_weight=0.2


def test_weighted_score_averages_multiple_detections_of_same_class() -> None:
    detections = [_detection("cavities", 0.8), _detection("cavities", 0.4)]
    score = _weighted_score(detections, _config())
    # weight=0.9, count=2, avg_conf=0.6 -> (0.9*2*0.6)/2 = 0.54
    assert score == pytest.approx(0.54)


def test_weighted_score_combines_multiple_classes() -> None:
    detections = [_detection("cavities", 1.0), _detection("low_snr_point_reflector", 1.0)]
    # (0.9*1*1.0 + 0.4*1*1.0) / 2 = 0.65
    score = _weighted_score(detections, _config())
    assert score == pytest.approx(0.65)


# --- escalation: cavities + utility co-occurrence ---


def test_escalation_cavities_with_utility_fires() -> None:
    detections = [_detection("cavities", 0.5), _detection("elongated_linear_target", 0.5)]
    assert "cavities_with_utility" in _escalation_rules(detections, _config())


def test_escalation_cavities_with_second_utility_class_fires() -> None:
    detections = [
        _detection("cavities", 0.5),
        _detection("intersecting_linear_and_point_reflector", 0.5),
    ]
    assert "cavities_with_utility" in _escalation_rules(detections, _config())


def test_escalation_cavities_alone_does_not_fire() -> None:
    assert _escalation_rules([_detection("cavities", 0.9)], _config()) == ()


def test_escalation_utility_alone_does_not_fire() -> None:
    rules = _escalation_rules([_detection("elongated_linear_target", 0.9)], _config())
    assert "cavities_with_utility" not in rules


def test_escalation_non_utility_class_with_cavities_does_not_fire_utility_rule() -> None:
    detections = [_detection("cavities", 0.9), _detection("clear_point_reflector", 0.9)]
    assert "cavities_with_utility" not in _escalation_rules(detections, _config())


# --- escalation: multiple high-confidence elongated ---


def test_escalation_multiple_high_confidence_elongated_fires() -> None:
    detections = [_detection("elongated_linear_target", 0.85), _detection("elongated_linear_target", 0.9)]
    assert "multiple_high_confidence_elongated" in _escalation_rules(detections, _config())


def test_escalation_single_high_confidence_elongated_does_not_fire() -> None:
    assert _escalation_rules([_detection("elongated_linear_target", 0.9)], _config()) == ()


def test_escalation_multiple_elongated_below_threshold_does_not_fire() -> None:
    detections = [_detection("elongated_linear_target", 0.5), _detection("elongated_linear_target", 0.6)]
    rules = _escalation_rules(detections, _config())
    assert "multiple_high_confidence_elongated" not in rules


def test_escalation_threshold_is_exclusive() -> None:
    # exactly at 0.8 must not count as "> 0.8"
    detections = [_detection("elongated_linear_target", 0.8), _detection("elongated_linear_target", 0.8)]
    rules = _escalation_rules(detections, _config())
    assert "multiple_high_confidence_elongated" not in rules


def test_escalation_min_count_boundary() -> None:
    detections = [
        _detection("elongated_linear_target", 0.9),
        _detection("elongated_linear_target", 0.9),
        _detection("elongated_linear_target", 0.9),
    ]
    config = _config(high_confidence_elongated_min_count=3)
    assert "multiple_high_confidence_elongated" in _escalation_rules(detections, config)

    config_needs_four = _config(high_confidence_elongated_min_count=4)
    assert "multiple_high_confidence_elongated" not in _escalation_rules(detections, config_needs_four)


def test_escalation_no_rules_fire_for_ordinary_detections() -> None:
    assert _escalation_rules([_detection("clear_point_reflector", 0.5)], _config()) == ()


def test_escalation_both_rules_can_fire_together() -> None:
    detections = [
        _detection("cavities", 0.5),
        _detection("elongated_linear_target", 0.9),
        _detection("elongated_linear_target", 0.85),
    ]
    rules = _escalation_rules(detections, _config())
    assert "cavities_with_utility" in rules
    assert "multiple_high_confidence_elongated" in rules


# --- score_detections: the full assembly ---


def test_score_detections_empty_is_low_with_no_rules() -> None:
    result = score_detections([], _config())
    assert result.score == 0.0
    assert result.level == "LOW"
    assert result.rules_fired == ()


def test_score_detections_low_level() -> None:
    result = score_detections([_detection("low_snr_point_reflector", 0.5)], _config())
    assert result.score == pytest.approx(0.2)
    assert result.level == "LOW"


def test_score_detections_medium_level() -> None:
    result = score_detections([_detection("cavities", 0.5)], _config())
    assert result.score == pytest.approx(0.45)
    assert result.level == "MEDIUM"


def test_score_detections_high_level_from_score_alone() -> None:
    result = score_detections([_detection("elongated_linear_target", 0.7)], _config())
    assert result.score == pytest.approx(0.7)
    assert result.level == "HIGH"
    assert result.rules_fired == ()


def test_score_detections_escalation_overrides_low_score_to_high() -> None:
    # Individually low-confidence, but the co-occurrence still escalates —
    # score is left as computed, only level is forced.
    detections = [_detection("cavities", 0.1), _detection("elongated_linear_target", 0.1)]
    config = _config()
    result = score_detections(detections, config)
    assert result.score < config.low_max
    assert result.level == "HIGH"
    assert "cavities_with_utility" in result.rules_fired
