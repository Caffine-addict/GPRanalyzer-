"""Tests for reference/sue_marking.py — boxing call-outs back onto the vendor's PDFs."""

from __future__ import annotations

from pathlib import Path

import pytest

from reference import sue_marking as sm
from reference.sue_sheets import Callout


def callout(depth: float | None) -> Callout:
    return Callout(utility="UC", depth_m=depth, chainage_m=10.0, chainage_source="plan_ticks", box=(0, 0, 1, 1))


@pytest.mark.parametrize(("depth", "safe", "expected"), [
    (1.54, 2.0, "reaches_safe_path"),  # 1.54 × 1.3 = 2.002
    (1.53, 2.0, "clear"),  # 1.53 × 1.3 = 1.989
    (1.0, 1.3, "reaches_safe_path"),  # 1.0 × 1.3 = 1.3 exactly: the >= boundary itself
    (None, 2.0, "depth_unread"),
    (0.5, None, "no_safe_path"),
])
def test_status_allows_for_the_vendors_own_thirty_percent(depth: float | None, safe: float | None,
                                                         expected: str) -> None:
    assert sm.status(callout(depth), safe) == expected


def test_a_box_on_an_unrotated_page_flips_only_vertically() -> None:
    assert sm.pdf_rect((10, 20, 30, 40), 842, 595, 0, pad=0) == (10, 555, 30, 575)


def test_a_box_on_a_page_rotated_half_a_turn_flips_horizontally_and_keeps_its_top() -> None:
    # Displayed (x, top) is unrotated (W - x, top): the page is turned, y measured from the other edge.
    assert sm.pdf_rect((10, 20, 30, 40), 842, 595, 180, pad=0) == (812, 20, 832, 40)


def test_a_rotation_that_is_not_a_quarter_turn_is_refused() -> None:
    with pytest.raises(ValueError, match="rotation 45"):
        sm.pdf_rect((10, 20, 30, 40), 842, 595, 45)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_word_read_off_a_rotated_page_is_boxed_where_it_was_drawn(rotation: int, tmp_path: Path) -> None:
    # Draw a word at a known spot in the page's own (unrotated) space, turn the page, read the
    # word back the way sue_sheets does, and check the box lands back on that spot.
    import pdfplumber
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas

    plain, turned = tmp_path / "plain.pdf", tmp_path / "turned.pdf"
    sheet = canvas.Canvas(str(plain), pagesize=(595, 842))
    sheet.drawString(100, 700, "ELECTRIC")  # baseline at (100, 700), unrotated space
    sheet.save()
    writer = PdfWriter(clone_from=PdfReader(plain))
    writer.pages[0].rotate(rotation)
    writer.write(turned)

    with pdfplumber.open(turned) as pdf:
        page = pdf.pages[0]
        (word,) = page.extract_words()
        box = (word["x0"], word["top"], word["x1"], word["bottom"])
        x0, y0, x1, y1 = sm.pdf_rect(box, page.width, page.height, rotation, pad=0)
    assert x0 <= 101 and 700 <= y1 and x1 > 130 and y0 < 705
