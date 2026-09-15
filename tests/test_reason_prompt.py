"""Tests for reason/prompt.py — the confidence discipline must survive into the prompt text itself."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.contracts import Evidence
from reason.prompt import _describe_field, build_evidence_block, build_prompt
from risk.score import RiskAssessment

_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "reason" / "prompts" / "v1_finding.txt"


def _evidence(**overrides: object) -> Evidence:
    base: dict[str, object] = {
        "detection_class": "cavities",
        "detection_confidence": 0.85,
        "depth_m": None,
        "depth_confidence": "unavailable",
        "position_m": None,
        "position_confidence": "unavailable",
        "amplitude": None,
        "amplitude_confidence": "unavailable",
        "hyperbola_width_px": 12.0,
    }
    base.update(overrides)
    return Evidence(**base)  # type: ignore[arg-type]


def _risk(**overrides: object) -> RiskAssessment:
    base: dict[str, object] = {"score": 0.5, "level": "MEDIUM", "rules_fired": ()}
    base.update(overrides)
    return RiskAssessment(**base)  # type: ignore[arg-type]


def test_unavailable_depth_produces_no_numeric_value() -> None:
    # This is the specific guarantee the project brief calls out: an
    # Evidence with depth_confidence "unavailable" must not let a number
    # slip into the prompt for the model to mistake as real.
    ev = _evidence(depth_confidence="unavailable", depth_m=None)
    block = build_evidence_block(ev)
    depth_line = next(line for line in block.splitlines() if line.startswith("- depth"))
    assert "unavailable" in depth_line
    assert not any(ch.isdigit() for ch in depth_line)


def test_unavailable_position_and_amplitude_also_produce_no_numeric_value() -> None:
    ev = _evidence()  # all three unavailable by default
    block = build_evidence_block(ev)
    for field in ("position", "amplitude"):
        line = next(line for line in block.splitlines() if line.startswith(f"- {field}"))
        assert "unavailable" in line
        assert not any(ch.isdigit() for ch in line)


def test_estimated_depth_is_marked_explicitly() -> None:
    ev = _evidence(depth_confidence="estimated", depth_m=0.8)
    block = build_evidence_block(ev)
    depth_line = next(line for line in block.splitlines() if line.startswith("- depth"))
    assert "ESTIMATED" in depth_line
    assert "0.800" in depth_line


def test_calibrated_depth_shows_value_without_estimated_flag() -> None:
    ev = _evidence(depth_confidence="calibrated", depth_m=1.2)
    block = build_evidence_block(ev)
    depth_line = next(line for line in block.splitlines() if line.startswith("- depth"))
    assert "calibrated measurement" in depth_line
    assert "ESTIMATED" not in depth_line
    assert "1.200" in depth_line


def test_describe_field_unavailable_returns_exact_fixed_message() -> None:
    # Direct unit test of the "unavailable" branch's string, independent of
    # Evidence's own invariant (value is None iff confidence=="unavailable")
    # — that invariant is what a caller normally relies on, but this line
    # itself doesn't enforce it, so a bug here that starts interpolating
    # `value` even when confidence is "unavailable" needs its own guard
    # rather than depending on every caller happening to pass None.
    assert (
        _describe_field("depth", None, "unavailable", "m")
        == "- depth: unavailable — no depth data could be derived; do not state a depth value"
    )


def test_depth_and_amplitude_populated_together_do_not_swap_values() -> None:
    # A survey with calibrated amplitude but only estimated depth is a
    # normal, common case — both fields being non-unavailable at once is
    # exactly the scenario a "vary one field, leave the rest at a shared
    # unavailable default" fixture set can never exercise.
    ev = _evidence(
        depth_m=0.8,
        depth_confidence="estimated",
        amplitude=5.0,
        amplitude_confidence="calibrated",
    )
    block = build_evidence_block(ev)
    depth_line = next(line for line in block.splitlines() if line.startswith("- depth"))
    amplitude_line = next(line for line in block.splitlines() if line.startswith("- amplitude"))

    assert "0.800" in depth_line
    assert "5.000" not in depth_line
    assert "5.000" in amplitude_line
    assert "0.800" not in amplitude_line


def test_depth_and_position_populated_together_do_not_swap_values() -> None:
    ev = _evidence(
        depth_m=0.8,
        depth_confidence="estimated",
        position_m=3.3,
        position_confidence="calibrated",
    )
    block = build_evidence_block(ev)
    depth_line = next(line for line in block.splitlines() if line.startswith("- depth"))
    position_line = next(line for line in block.splitlines() if line.startswith("- position"))

    assert "0.800" in depth_line
    assert "3.300" not in depth_line
    assert "3.300" in position_line
    assert "0.800" not in position_line


def test_all_three_fields_populated_simultaneously_stay_unmixed() -> None:
    # One fixture with three distinct, unmistakable values catches any
    # pairwise or three-way field-routing swap in a single test, rather
    # than needing one test per pair.
    ev = _evidence(
        depth_m=0.111,
        depth_confidence="estimated",
        position_m=0.222,
        position_confidence="calibrated",
        amplitude=0.333,
        amplitude_confidence="calibrated",
    )
    block = build_evidence_block(ev)
    lines = {
        field: next(line for line in block.splitlines() if line.startswith(f"- {field}"))
        for field in ("depth", "position", "amplitude")
    }
    own_values = {"depth": "0.111", "position": "0.222", "amplitude": "0.333"}

    for field, line in lines.items():
        assert own_values[field] in line
        for other_field, other_value in own_values.items():
            if other_field != field:
                assert other_value not in line


def test_neighbours_and_prior_passes_reflected_in_block() -> None:
    ev = _evidence(neighbours=("elongated_linear_target",), prior_passes=({"id": 1}, {"id": 2}))
    block = build_evidence_block(ev)
    assert "elongated_linear_target" in block
    assert "2 found" in block


def test_no_neighbours_or_prior_passes_says_none() -> None:
    ev = _evidence()
    block = build_evidence_block(ev)
    assert "other detections in this frame: none" in block
    assert "prior survey passes at this location: none" in block


def test_build_prompt_includes_detection_class_and_risk_info() -> None:
    ev = _evidence(detection_class="elongated_linear_target", detection_confidence=0.91)
    risk = _risk(score=0.72, level="HIGH", rules_fired=("cavities_with_utility",))
    prompt = build_prompt(ev, risk, _TEMPLATE_PATH)

    assert "elongated_linear_target" in prompt
    assert "91" in prompt  # detection_confidence formatted as a percentage
    assert "HIGH" in prompt
    assert "0.720" in prompt
    assert "cavities_with_utility" in prompt


def test_build_prompt_end_to_end_unavailable_produces_no_depth_claim() -> None:
    ev = _evidence(depth_confidence="unavailable", depth_m=None)
    risk = _risk()
    prompt = build_prompt(ev, risk, _TEMPLATE_PATH)

    depth_line = next(line for line in prompt.splitlines() if line.startswith("- depth"))
    assert "unavailable" in depth_line
    assert "do not state a depth value" in depth_line


def test_evidence_with_unavailable_confidence_and_stray_value_cannot_reach_prompt() -> None:
    # Evidence's own __post_init__ already forbids constructing this
    # combination — confirming that invariant here documents *why*
    # build_evidence_block never needs to defend against it itself.
    with pytest.raises(ValueError, match="unavailable"):
        _evidence(depth_confidence="unavailable", depth_m=1.5)


# --- corroboration reaches the model, and the third prompt (2026-09-14) ------


def test_a_single_receiver_is_described_as_unconfirmed() -> None:
    # "1" alone tells the model nothing. The first real run produced "the lack of prior survey
    # passes means this is the first observation" about a target two receivers had just agreed on,
    # because the count never reached the prompt at all.
    block = build_evidence_block(_evidence())  # default is 1
    line = next(line for line in block.splitlines() if "independent receivers" in line)
    assert "1" in line
    assert "only one" in line


def test_two_receivers_are_described_as_the_strongest_evidence_available() -> None:
    block = build_evidence_block(_evidence(corroborating_channels=2))
    line = next(line for line in block.splitlines() if "independent receivers" in line)
    assert "2" in line
    assert "independently" in line
    assert "only one" not in line


def test_the_candidate_prompt_fills_every_placeholder() -> None:
    # A third caller exists that neither existing prompt describes honestly: targets found by
    # classical signal processing, with no human pick and no trained detector behind them.
    path = _TEMPLATE_PATH.parent / "v1_candidate.txt"
    prompt = build_prompt(_evidence(corroborating_channels=2), _risk(), path)
    for placeholder in (
        "{detection_class}",
        "{detection_confidence}",
        "{evidence_block}",
        "{risk_level}",
        "{risk_score}",
        "{risk_rules_fired}",
    ):
        assert placeholder not in prompt, f"{placeholder} was never substituted"
    assert "no person marked it" in prompt


def test_the_candidate_prompt_refuses_to_let_fit_quality_become_certainty() -> None:
    # The first real run had the model report a curve-fit R2 as "99% classifier confidence" and
    # then call its own assessment high-confidence on that basis. There is no classifier.
    text = (_TEMPLATE_PATH.parent / "v1_candidate.txt").read_text(encoding="utf-8")
    assert "NOT a classifier score" in text
    assert "never describe the assessment as high confidence" in text


def test_the_pipeline_prompt_also_forbids_restating_confidence_as_certainty() -> None:
    text = _TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "not a measure of how certain anyone is about what the object physically is" in text
