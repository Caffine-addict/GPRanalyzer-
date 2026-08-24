"""End-of-survey PDF: summary stats, a per-field confidence breakdown, and every finding
sorted highest-risk-first.

No separate per-survey SourceCapabilities record exists in the store — capability caveats
are derived instead from each finding's own calibrated/estimated/unavailable confidence
labels (core/contracts.py's Evidence), which is already a more precise, per-field signal
than a single survey-wide capabilities flag would be, and keeps this module reading only
through the Store interface rather than needing a schema change.

Every free-text value that reaches reportlab's Paragraph() — survey_id, detection_class,
and especially LLM-generated finding.what/recommended_action, none of which are
constrained to avoid '<'/'&' — must go through _escape() first. Paragraph doesn't treat
its argument as plain text; it parses a small XML-like markup language, so an unescaped
'<' can either crash doc.build() outright (a plausible '<' in reasoning text like "depth
< 1m" is enough) or, worse, be silently interpreted as real markup.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as _escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.contracts import ConfidenceLevel, Finding
from store.base import Store

_RISK_ORDER: dict[str, int] = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
_RISK_COLORS: dict[str, colors.Color] = {
    "HIGH": colors.HexColor("#c62828"),
    "MEDIUM": colors.HexColor("#e08e00"),
    "LOW": colors.HexColor("#2e7d32"),
}
_CONFIDENCE_FIELDS: tuple[tuple[str, str], ...] = (
    ("depth_confidence", "Depth"),
    ("position_confidence", "Position"),
    ("amplitude_confidence", "Amplitude"),
)


def generate_report(survey_id: str, store: Store, output_path: str | Path) -> Path:
    """Renders the report to output_path and returns it. A survey with zero findings still
    produces a real, honest PDF stating that — never an empty or fabricated table.
    """
    summary = store.get_survey_summary(survey_id)
    findings = store.get_findings_by_survey(survey_id)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    body_style = styles["BodyText"]

    story: list[Any] = [
        Paragraph(f"GPR Survey Report — {_escape(survey_id)}", styles["Title"]),
        Paragraph(f"Generated {datetime.now(UTC).isoformat()}", body_style),
        Spacer(1, 0.5 * cm),
        Paragraph("Summary", styles["Heading2"]),
        _summary_table(summary),
        Spacer(1, 0.5 * cm),
        Paragraph("Data confidence caveats", styles["Heading2"]),
        *_confidence_caveats(findings, body_style),
        Spacer(1, 0.5 * cm),
        Paragraph("Findings (highest risk first)", styles["Heading2"]),
        _findings_table(findings, styles),
    ]

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(letter),
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )
    doc.build(story)
    return output_path


def _summary_table(summary: dict[str, Any]) -> Table:
    rows: list[list[str]] = [["Total findings", str(summary["total_findings"])]]
    for level in ("HIGH", "MEDIUM", "LOW"):
        rows.append([f"Risk: {level}", str(summary["by_risk_level"].get(level, 0))])
    for class_name, count in sorted(summary["by_class"].items()):
        rows.append([f"Class: {class_name}", str(count)])

    table = Table(rows, colWidths=[8 * cm, 4 * cm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
            ]
        )
    )
    return table


def _confidence_caveats(findings: list[Finding], body_style: ParagraphStyle) -> list[Paragraph]:
    if not findings:
        return [Paragraph("No findings recorded for this survey.", body_style)]

    paragraphs = []
    for confidence_attr, label in _CONFIDENCE_FIELDS:
        counts = Counter(getattr(f.evidence, confidence_attr) for f in findings)
        breakdown = ", ".join(
            f"{counts.get(level, 0)} {level}" for level in ("calibrated", "estimated", "unavailable")
        )
        paragraphs.append(
            Paragraph(f"<b>{label}:</b> {breakdown} (of {len(findings)} findings)", body_style)
        )
    return paragraphs


def _format_value(value: float | None, confidence: ConfidenceLevel, unit: str = "") -> str:
    # Same discipline as core/contracts.py's Evidence.__post_init__: a value is only ever
    # shown when its confidence says it's real — never a bare number implying a
    # measurement the source couldn't actually back up.
    if confidence == "unavailable" or value is None:
        return "unavailable"
    return f"{value:.3f}{unit} ({confidence})"


def _findings_table(findings: list[Finding], styles: Any) -> Table:
    if not findings:
        return Table([["No findings recorded for this survey."]])

    cell_style = ParagraphStyle("cell", parent=styles["BodyText"], fontSize=8, leading=10)
    header = ["Class", "Risk", "Conf.", "Depth", "Position", "Amplitude", "What", "Recommended action"]
    ordered = sorted(findings, key=lambda f: _RISK_ORDER[f.risk_level])

    rows: list[list[Any]] = [header]
    for finding in ordered:
        ev = finding.evidence
        risk_cell: str = finding.risk_level
        if finding.risk_rules_fired:
            risk_cell += f" ({_escape(', '.join(finding.risk_rules_fired))})"
        rows.append(
            [
                Paragraph(_escape(ev.detection_class), cell_style),
                Paragraph(risk_cell, cell_style),
                Paragraph(f"{ev.detection_confidence:.0%}", cell_style),
                Paragraph(_format_value(ev.depth_m, ev.depth_confidence, "m"), cell_style),
                Paragraph(_format_value(ev.position_m, ev.position_confidence, "m"), cell_style),
                Paragraph(_format_value(ev.amplitude, ev.amplitude_confidence), cell_style),
                Paragraph(_escape(finding.what) if finding.what else "reasoning unavailable", cell_style),
                Paragraph(_escape(finding.recommended_action) if finding.recommended_action else "—", cell_style),
            ]
        )

    # Risk gets extra room deliberately: risk_rules_fired names
    # (e.g. "cavities_with_utility") have no spaces to wrap at, so a
    # too-narrow column forces an unreadable mid-word character split
    # rather than wrapping at the natural "HIGH" / "(rule_name)" boundary.
    col_widths = [2.2 * cm, 4.3 * cm, 1.4 * cm, 2.4 * cm, 2.4 * cm, 2.1 * cm, 4.0 * cm, 4.0 * cm]
    table = Table(rows, colWidths=col_widths, repeatRows=1)

    style_commands: list[tuple[Any, ...]] = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for row_index, finding in enumerate(ordered, start=1):
        style_commands.append(("TEXTCOLOR", (1, row_index), (1, row_index), _RISK_COLORS[finding.risk_level]))
    table.setStyle(TableStyle(style_commands))
    return table
