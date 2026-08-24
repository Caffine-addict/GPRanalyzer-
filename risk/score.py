"""Weighted risk scoring over a frame's detections, plus escalation rules.

score = sum(weight_c * count_c * avg_conf_c) / total_count, grouped by class.
level from thresholds, unless an escalation rule fires (always -> HIGH).
rules_fired survives on the result so the reasoning layer can explain why.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.config import RiskConfig
from core.contracts import Detection, RiskLevel


@dataclass(frozen=True)
class RiskAssessment:
    score: float
    level: RiskLevel
    rules_fired: tuple[str, ...]


def _weighted_score(detections: list[Detection], config: RiskConfig) -> float:
    if not detections:
        return 0.0

    by_class: dict[str, list[float]] = {}
    for d in detections:
        by_class.setdefault(d.class_name, []).append(d.confidence)

    weighted_sum = 0.0
    for class_name, confidences in by_class.items():
        weight = config.weights.get(class_name, config.default_weight)
        count = len(confidences)
        avg_confidence = sum(confidences) / count
        weighted_sum += weight * count * avg_confidence

    return weighted_sum / len(detections)


def _level_from_score(score: float, config: RiskConfig) -> RiskLevel:
    if score < config.low_max:
        return "LOW"
    if score < config.medium_max:
        return "MEDIUM"
    return "HIGH"


def _escalation_rules(detections: list[Detection], config: RiskConfig) -> tuple[str, ...]:
    fired: list[str] = []
    class_names = {d.class_name for d in detections}

    if "cavities" in class_names and class_names & set(config.utility_classes):
        fired.append("cavities_with_utility")

    high_confidence_elongated = sum(
        1
        for d in detections
        if d.class_name == "elongated_linear_target"
        and d.confidence > config.high_confidence_elongated_threshold
    )
    if high_confidence_elongated >= config.high_confidence_elongated_min_count:
        fired.append("multiple_high_confidence_elongated")

    return tuple(fired)


def score_detections(detections: list[Detection], config: RiskConfig) -> RiskAssessment:
    score = _weighted_score(detections, config)
    rules_fired = _escalation_rules(detections, config)
    level: RiskLevel = "HIGH" if rules_fired else _level_from_score(score, config)
    return RiskAssessment(score=score, level=level, rules_fired=rules_fired)
