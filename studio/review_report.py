"""PDF reports for the reasoning layers: a line's supervisor review, and the survey briefing.

The review report is the document a supervisor signs off: the radargram with every model claim
circled in its review colour, and a table saying, claim by claim, what the model said, who
decided and when. It leads with the one sentence a reader must not miss — only rows marked
CONFIRMED were checked by a named person; everything else is a model's reading.

Every free-text value is escaped before it reaches a reportlab Paragraph: identities, reasons
and notes come from a language model or a person, and reportlab parses markup (a stray "<"
crashes the build; a well-formed tag silently restyles the audit trail — reports/generate.py
learned this in Session 9).
"""

from __future__ import annotations

import io
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from xml.sax.saxutils import escape

import numpy as np
from PIL import Image as PILImage
from PIL import ImageDraw
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from studio.reviews import Review
from studio.session import ChannelInfo

STATUS_RGB = {"proposed": (245, 166, 35), "confirmed": (52, 199, 123), "rejected": (255, 93, 93)}
_PAGE = landscape(A4)
_MARGIN = 1.4 * cm
_WIDTH = _PAGE[0] - 2 * _MARGIN

_styles = getSampleStyleSheet()
_BODY = ParagraphStyle("body", parent=_styles["BodyText"], fontSize=9, leading=12)
_SMALL = ParagraphStyle("small", parent=_BODY, fontSize=7.5, leading=9.5)
_WARN = ParagraphStyle("warn", parent=_BODY, textColor=colors.HexColor("#8a4b00"), fontName="Helvetica-Bold")


def _p(text: object, style: ParagraphStyle = _BODY) -> Paragraph:
    return Paragraph(escape(str(text)), style)


def marked_image(rgb: np.ndarray, reviews: Sequence[Review]) -> PILImage.Image:
    """The radargram (rows = samples, columns = traces) with each review's circle on its box."""
    image = PILImage.fromarray(rgb.astype(np.uint8)).convert("RGB")
    draw = ImageDraw.Draw(image)
    for number, review in enumerate(reviews, start=1):
        colour = STATUS_RGB.get(review.status, STATUS_RGB["proposed"])
        pad = 3
        box = (review.x - pad, review.y - pad, review.x + review.w + pad, review.y + review.h + pad)
        draw.ellipse(box, outline=colour, width=2)
        draw.text((max(0, box[0]), max(0, box[1] - 11)), str(number), fill=colour)
    return image


def _png(image: PILImage.Image) -> io.BytesIO:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _build(story: list[Any], title: str) -> bytes:
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=_PAGE, leftMargin=_MARGIN, rightMargin=_MARGIN, topMargin=_MARGIN,
                      bottomMargin=_MARGIN, title=title).build(story)
    return buffer.getvalue()


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def review_report_pdf(job: str, info: ChannelInfo, rgb: np.ndarray, reviews: Sequence[Review]) -> bytes:
    """One channel of one line: the circled radargram and every claim with its decision."""
    reviews = [r for r in reviews if r.channel == info.extension]
    counts = Counter(r.status for r in reviews)
    story: list[Any] = [
        _p(f"Supervisor review — {job} · {info.label}", _styles["Title"]),
        _p(f"Line length {info.line_length_m:.1f} m · {len(reviews)} model claims: {counts['confirmed']} confirmed, "
           f"{counts['rejected']} rejected, {counts['proposed']} still waiting · generated {_now()}"),
        Spacer(1, 4),
        _p("Only rows marked CONFIRMED were checked by a named person. Every other identification is a language "
           "model's reading of measured values and must not be reported as a finding.", _WARN),
        Spacer(1, 6),
    ]
    image = marked_image(rgb, reviews)
    scale = min(_WIDTH / image.width, (9 * cm) / image.height)
    story += [Image(_png(image), width=image.width * scale, height=image.height * scale),
              _p("Circles: amber = waiting for review, green = confirmed, red = rejected. Numbers match the table. "
                 "Horizontal: traces; vertical: samples (raw data, no processing).", _SMALL),
              Spacer(1, 8)]
    if not reviews:
        story.append(_p("No claims on this channel."))
        return _build(story, f"{job} {info.extension} review")
    header = ["#", "Along line", "Identity", "Material", "Conf.", "Status", "Reviewer", "Decided", "Note", "Why (model)"]
    rows: list[list[Any]] = [[_p(h, _SMALL) for h in header]]
    for number, r in enumerate(reviews, start=1):
        along = f"{(r.x + r.w / 2) * info.trace_spacing_m:.2f} m"
        rows.append([_p(v, _SMALL) for v in (number, along, r.identity, r.material, r.confidence, r.status.upper(),
                                             r.reviewer or "—", r.decided_at or "—", r.note or "—", r.why)])
    widths = [0.6, 1.6, 3.4, 2.6, 1.2, 2.4, 2.4, 2.8, 3.2, 6.8]
    table = Table(rows, colWidths=[w * cm for w in widths], repeatRows=1)
    style = [("GRID", (0, 0), (-1, -1), 0.3, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8ecf0")),
             ("VALIGN", (0, 0), (-1, -1), "TOP")]
    for row, r in enumerate(reviews, start=1):
        red, green, blue = STATUS_RGB.get(r.status, STATUS_RGB["proposed"])
        style.append(("BACKGROUND", (5, row), (5, row), colors.Color(red / 255, green / 255, blue / 255, alpha=0.25)))
    table.setStyle(TableStyle(style))
    story += [table, Spacer(1, 6),
              _p(f"Model claims: {', '.join(sorted({f'{r.layer} ({r.model})' for r in reviews}))}.", _SMALL)]
    return _build(story, f"{job} {info.extension} review")


def briefing_pdf(reply: dict[str, Any]) -> bytes:
    """The last survey briefing, with the exact context the model was given as an appendix."""
    story: list[Any] = [_p("Survey briefing", _styles["Title"]),
                        _p(f"Written by {reply.get('model') or 'no model'} · generated {_now()}"),
                        Spacer(1, 4),
                        _p("A language model's summary of the measured and extracted data listed in the appendix. "
                           "It adds no measurements of its own.", _WARN),
                        Spacer(1, 6)]
    if reply.get("error"):
        story.append(_p(f"No briefing: {reply['error']}"))
    for paragraph in str(reply.get("answer", "")).split("\n\n"):
        if paragraph.strip():
            story += [_p(paragraph.strip()), Spacer(1, 4)]
    if reply.get("next_steps"):
        story.append(_p("Next steps", _styles["Heading3"]))
        story += [_p(f"• {step}") for step in reply["next_steps"]]
    story += [Spacer(1, 8), _p("Appendix — what the model was given", _styles["Heading3"]),
              Preformatted(str(reply.get("context_text", "")), ParagraphStyle("mono", fontName="Courier", fontSize=6.5,
                                                                              leading=7.5))]
    return _build(story, "Survey briefing")
