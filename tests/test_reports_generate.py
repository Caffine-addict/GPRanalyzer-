"""Tests for reports/generate.py — verifies real PDF content via pypdf text extraction,
not just "a file was created." A report that silently fabricated a number, dropped a
finding, or mis-sorted risk would still "successfully create a PDF" — only reading the
actual rendered text catches that.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pypdf import PdfReader

from core.contracts import Evidence, Finding, ScanFrame
from reports.generate import _format_value, generate_report
from store.duckdb_store import DuckDBStore


@pytest.fixture
def store(tmp_path: Path) -> DuckDBStore:
    s = DuckDBStore(tmp_path / "test.duckdb")
    yield s
    s.close()


def _frame(**overrides: object) -> ScanFrame:
    base: dict[str, object] = {
        "source_type": "replay",
        "provenance": {"path": "001.jpg"},
        "image": np.zeros((10, 10), dtype=np.uint8),
        "position": 1.0,
        "position_source": "synthetic",
    }
    base.update(overrides)
    return ScanFrame(**base)  # type: ignore[arg-type]


def _evidence(**overrides: object) -> Evidence:
    base: dict[str, object] = {
        "detection_class": "cavities",
        "detection_confidence": 0.8,
        "depth_m": 0.5,
        "depth_confidence": "estimated",
        "position_m": None,
        "position_confidence": "unavailable",
        "amplitude": None,
        "amplitude_confidence": "unavailable",
        "hyperbola_width_px": 10.0,
    }
    base.update(overrides)
    return Evidence(**base)  # type: ignore[arg-type]


def _finding(**overrides: object) -> Finding:
    base: dict[str, object] = {
        "evidence": _evidence(),
        "risk_level": "MEDIUM",
        "risk_score": 0.5,
        "risk_rules_fired": (),
    }
    base.update(overrides)
    return Finding(**base)  # type: ignore[arg-type]


def _extract_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() for page in reader.pages)


def _value_after_line(text: str, label: str) -> str:
    # _summary_table renders each [label, count] row as two adjacent table
    # cells, which pypdf's extraction puts on two consecutive lines — pairs
    # a label with its own count rather than searching for the count
    # anywhere in the whole document.
    lines = text.split("\n")
    return lines[lines.index(label) + 1]


def test_generate_report_creates_a_real_pdf_file(store: DuckDBStore, tmp_path: Path) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    store.save_finding("s1", "l1", frame_id, _finding())

    out = generate_report("s1", store, tmp_path / "report.pdf")

    assert out.exists()
    assert out.read_bytes()[:5] == b"%PDF-"
    reader = PdfReader(str(out))
    assert len(reader.pages) >= 1


def test_generate_report_creates_missing_parent_directories(store: DuckDBStore, tmp_path: Path) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    store.save_finding("s1", "l1", frame_id, _finding())

    out = generate_report("s1", store, tmp_path / "nested" / "dir" / "report.pdf")

    assert out.exists()


def test_report_includes_survey_id_and_summary_counts(store: DuckDBStore, tmp_path: Path) -> None:
    frame_id = store.save_frame("survey-42", "l1", _frame())
    store.save_finding("survey-42", "l1", frame_id, _finding(risk_level="HIGH", evidence=_evidence(detection_class="cavities")))
    store.save_finding("survey-42", "l1", frame_id, _finding(risk_level="HIGH", evidence=_evidence(detection_class="cavities")))
    store.save_finding("survey-42", "l1", frame_id, _finding(risk_level="LOW", evidence=_evidence(detection_class="disturbed_zone")))

    out = generate_report("survey-42", store, tmp_path / "report.pdf")
    text = _extract_text(out)

    assert "survey-42" in text
    assert "Total findings" in text and "3" in text
    assert "cavities" in text
    assert "disturbed_zone" in text


def test_report_shows_no_findings_message_for_empty_survey(store: DuckDBStore, tmp_path: Path) -> None:
    out = generate_report("nonexistent", store, tmp_path / "report.pdf")
    text = _extract_text(out)

    assert "No findings recorded" in text
    assert "Total findings" in text and "0" in text


def test_report_never_shows_a_fabricated_number_for_unavailable_confidence(
    store: DuckDBStore, tmp_path: Path
) -> None:
    # The one invariant this whole project is built around: a value must
    # never appear where its confidence says it's "unavailable."
    frame_id = store.save_frame("s1", "l1", _frame())
    store.save_finding(
        "s1",
        "l1",
        frame_id,
        _finding(
            evidence=_evidence(
                position_m=None,
                position_confidence="unavailable",
                amplitude=None,
                amplitude_confidence="unavailable",
            )
        ),
    )

    out = generate_report("s1", store, tmp_path / "report.pdf")
    text = _extract_text(out)

    assert "unavailable" in text
    # No stray digit-dot-digit pattern anywhere near "Position"/"Amplitude"
    # cells would be hard to assert generically without a real layout
    # inspector — instead, pin the exact non-fabricated rendering directly.
    assert _format_value(None, "unavailable") == "unavailable"


def test_report_confidence_caveats_reflect_actual_mixed_data(store: DuckDBStore, tmp_path: Path) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    store.save_finding("s1", "l1", frame_id, _finding(evidence=_evidence(depth_confidence="calibrated", depth_m=0.5)))
    store.save_finding("s1", "l1", frame_id, _finding(evidence=_evidence(depth_confidence="estimated", depth_m=0.3)))
    store.save_finding("s1", "l1", frame_id, _finding(evidence=_evidence(depth_confidence="unavailable", depth_m=None)))

    out = generate_report("s1", store, tmp_path / "report.pdf")
    text = _extract_text(out)

    assert "1 calibrated, 1 estimated, 1 unavailable" in text.replace("\n", " ")


def test_findings_sorted_highest_risk_first(store: DuckDBStore, tmp_path: Path) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    # Saved in a deliberately non-sorted order (LOW, HIGH, MEDIUM) — the
    # report must reorder, not just reflect insertion order. Distinguished
    # by detection_confidence rather than detection_class: several 9-class
    # taxonomy names (e.g. "clear_point_reflector") are long single "words"
    # with no spaces, which reportlab legitimately word-wraps mid-token at
    # this column width — correct rendering, but not a stable substring to
    # search extracted text for. "HIGH"/"MEDIUM"/"LOW" are short enough to
    # never wrap and each appears exactly once per row in this section.
    store.save_finding("s1", "l1", frame_id, _finding(risk_level="LOW", evidence=_evidence(detection_confidence=0.11)))
    store.save_finding("s1", "l1", frame_id, _finding(risk_level="HIGH", evidence=_evidence(detection_confidence=0.22)))
    store.save_finding("s1", "l1", frame_id, _finding(risk_level="MEDIUM", evidence=_evidence(detection_confidence=0.33)))

    out = generate_report("s1", store, tmp_path / "report.pdf")
    text = _extract_text(out)

    findings_section = text.split("Findings (highest risk first)")[1]
    high_pos = findings_section.index("HIGH")
    medium_pos = findings_section.index("MEDIUM")
    low_pos = findings_section.index("LOW")
    assert high_pos < medium_pos < low_pos


def test_report_shows_risk_rules_fired_when_present(store: DuckDBStore, tmp_path: Path) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    store.save_finding(
        "s1", "l1", frame_id, _finding(risk_level="HIGH", risk_rules_fired=("cavities_with_utility",))
    )

    out = generate_report("s1", store, tmp_path / "report.pdf")
    text = _extract_text(out)

    assert "cavities_with_utility" in text


def test_report_shows_reasoning_unavailable_when_finding_has_no_reasoning(
    store: DuckDBStore, tmp_path: Path
) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    store.save_finding("s1", "l1", frame_id, _finding())  # default has what=None

    out = generate_report("s1", store, tmp_path / "report.pdf")
    text = _extract_text(out)

    assert "reasoning unavailable" in text


def test_report_shows_actual_reasoning_when_present(store: DuckDBStore, tmp_path: Path) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    store.save_finding(
        "s1",
        "l1",
        frame_id,
        _finding(what="a linear reflector", recommended_action="confirm with a second pass"),
    )

    out = generate_report("s1", store, tmp_path / "report.pdf")
    text = _extract_text(out)

    assert "a linear reflector" in text
    assert "confirm with a second pass" in text


class TestFormatValue:
    def test_returns_unavailable_when_confidence_is_unavailable(self) -> None:
        assert _format_value(None, "unavailable") == "unavailable"

    def test_returns_unavailable_when_confidence_is_unavailable_even_with_a_real_value(self) -> None:
        # The actual defense-in-depth case, distinct from the two tests
        # below: Evidence.__post_init__ forbids "unavailable" confidence
        # paired with a non-None value, but this formatter claims to guard
        # that combination independently — a test that only ever passes
        # value=None can't tell "checks confidence" apart from "checks
        # value is None", since both would return "unavailable" either way.
        assert _format_value(5.0, "unavailable") == "unavailable"

    def test_returns_unavailable_when_value_is_none_regardless_of_confidence(self) -> None:
        # Defense in depth: Evidence.__post_init__ forbids this combination,
        # but this formatter must never fabricate a number even if a
        # malformed row somehow got past that guard.
        assert _format_value(None, "calibrated") == "unavailable"

    def test_formats_a_calibrated_value_with_unit_and_label(self) -> None:
        assert _format_value(0.523, "calibrated", "m") == "0.523m (calibrated)"

    def test_formats_an_estimated_value(self) -> None:
        assert _format_value(1.2, "estimated") == "1.200 (estimated)"


def test_confidence_caveats_pin_each_field_to_its_own_correct_breakdown(store: DuckDBStore, tmp_path: Path) -> None:
    # A generic "some breakdown appears somewhere in the text" assertion
    # can't catch a field mixup (e.g. Depth's line actually counting
    # position_confidence) — the exact bug class Session 6 hit with a real
    # DB column swap. Each field gets a distinct, distinguishable pattern
    # here so a mixup between any two fields is visible.
    frame_id = store.save_frame("s1", "l1", _frame())
    for _ in range(2):
        store.save_finding(
            "s1",
            "l1",
            frame_id,
            _finding(
                evidence=_evidence(
                    depth_confidence="calibrated",
                    depth_m=0.5,
                    position_confidence="estimated",
                    position_m=1.0,
                    amplitude_confidence="unavailable",
                    amplitude=None,
                )
            ),
        )

    out = generate_report("s1", store, tmp_path / "report.pdf")
    text = _extract_text(out)

    depth_line = next(line for line in text.split("\n") if line.startswith("Depth:"))
    position_line = next(line for line in text.split("\n") if line.startswith("Position:"))
    amplitude_line = next(line for line in text.split("\n") if line.startswith("Amplitude:"))

    assert "2 calibrated, 0 estimated, 0 unavailable" in depth_line
    assert "0 calibrated, 2 estimated, 0 unavailable" in position_line
    assert "0 calibrated, 0 estimated, 2 unavailable" in amplitude_line


def test_summary_table_pins_each_risk_level_to_its_own_correct_count(store: DuckDBStore, tmp_path: Path) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    for _ in range(3):
        store.save_finding("s1", "l1", frame_id, _finding(risk_level="HIGH"))
    store.save_finding("s1", "l1", frame_id, _finding(risk_level="MEDIUM"))
    # Zero LOW findings deliberately: store.get_survey_summary's GROUP BY
    # naturally omits absent levels — _summary_table's `.get(level, 0)`
    # must still render "Risk: LOW" with a real 0, not silently drop the row.

    out = generate_report("s1", store, tmp_path / "report.pdf")
    text = _extract_text(out)

    assert _value_after_line(text, "Risk: HIGH") == "3"
    assert _value_after_line(text, "Risk: MEDIUM") == "1"
    assert _value_after_line(text, "Risk: LOW") == "0"


def test_report_shows_every_risk_rule_with_a_separator_when_multiple_fire(
    store: DuckDBStore, tmp_path: Path
) -> None:
    frame_id = store.save_frame("s1", "l1", _frame())
    store.save_finding(
        "s1",
        "l1",
        frame_id,
        _finding(risk_level="HIGH", risk_rules_fired=("cavities_with_utility", "high_confidence_elongated")),
    )

    out = generate_report("s1", store, tmp_path / "report.pdf")
    # Normalize away incidental whitespace/newlines from column word-wrap
    # (legitimate PDF layout behaviour, not a bug — see
    # test_findings_sorted_highest_risk_first) so this checks the actual
    # content: both rule names present, joined WITH a separator between
    # them — catches both "only the first rule renders" and "no separator"
    # (which "each name appears somewhere" alone couldn't distinguish from
    # a correctly separated join).
    normalized = _extract_text(out).replace("\n", "").replace(" ", "")
    assert "cavities_with_utility,high_confidence_elongated" in normalized


def test_report_free_text_with_markup_characters_does_not_crash_and_is_not_interpreted(
    store: DuckDBStore, tmp_path: Path
) -> None:
    # reportlab's Paragraph() parses its argument as a small XML-like
    # markup language, not plain text. survey_id (an HTTP path parameter
    # once reports are wired into api/server.py) and finding.what/
    # recommended_action (LLM-generated, never constrained to avoid '<'/'&')
    # must not be able to crash report generation or have injected markup
    # silently take effect.
    frame_id = store.save_frame("s1<script>", "l1", _frame())
    store.save_finding(
        "s1<script>",
        "l1",
        frame_id,
        _finding(
            evidence=_evidence(detection_class="cavities"),
            what='depth < 1m and >2m <font color="red">injected</font>',
            recommended_action="dig <here & verify",
        ),
    )

    out = generate_report("s1<script>", store, tmp_path / "report.pdf")  # must not raise
    text = _extract_text(out)

    # Rendered as literal text, not stripped and not applied as real markup.
    assert "s1<script>" in text
    assert "<font" in text and "injected" in text
    assert "dig <here & verify" in text
