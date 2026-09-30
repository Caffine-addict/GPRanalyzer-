"""Marking the utility call-outs read by `reference.sue_sheets` back onto the vendor's own PDFs.

Every call-out gets a box on its plan label, coloured by what it means for the HDD bore:

- red   — the utility could reach the sheet's recommended safe path once the vendor's own
          ±30% depth error is allowed for (depth × 1.3 ≥ safe path);
- amber — the depth could not be read off the sheet, so it can't be cleared;
- blue  — clear of the safe path even at +30%;
- grey  — the sheet states no safe path to compare against.

Hovering a box shows what was read. Nothing on the original drawing is changed: the marks are
PDF annotations on a copy.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Rectangle
from pypdf.generic import ArrayObject, FloatObject, NameObject, NumberObject, TextStringObject

from reference.sue_sheets import Callout, Sheet

DEPTH_ERROR = 0.30  # "+ 30% Instrumental error in depth of utilities", printed on every sheet
COLOURS = {"reaches_safe_path": (0.9, 0.0, 0.0), "depth_unread": (1.0, 0.6, 0.0), "clear": (0.0, 0.35, 0.9),
           "no_safe_path": (0.5, 0.5, 0.5)}


def status(callout: Callout, safe_path_m: float | None) -> str:
    """"reaches_safe_path", "depth_unread", "clear", or "no_safe_path" when the sheet gives none."""
    if callout.depth_m is None:
        return "depth_unread"
    if safe_path_m is None:
        return "no_safe_path"
    return "reaches_safe_path" if callout.depth_m * (1 + DEPTH_ERROR) >= safe_path_m else "clear"


def pdf_rect(box: tuple[float, float, float, float], width: float, height: float,
             rotation: int, pad: float = 1.5) -> tuple[float, float, float, float]:
    """A pdfplumber box (as displayed, top-left origin) in the page's unrotated PDF space.

    pdfplumber reports words where they appear after the page's /Rotate; annotation rectangles
    live in the page's own coordinates, before it. `width`/`height` are the displayed size.
    """
    x0, top, x1, bottom = box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad
    if rotation % 360 == 0:
        return x0, height - bottom, x1, height - top
    if rotation % 360 == 180:
        return width - x1, top, width - x0, bottom
    raise ValueError(f"page rotation {rotation} is not handled — only 0 and 180 occur in these drawings")


def _label(callout: Callout, sheet: Sheet) -> str:
    depth = "depth not readable" if callout.depth_m is None else f"depth {callout.depth_m:.2f} m (±30%)"
    where = "chainage unknown" if callout.chainage_m is None else \
        f"CH {callout.chainage_m:.0f} m ({callout.chainage_source.replace('_', ' ')})"
    safe = "" if sheet.safe_path_m is None else f"; safe path {sheet.safe_path_m:.2f} m"
    return f"{callout.utility}: {depth}, {where}{safe}"


def mark_pdf(source: Path, sheets: Iterable[Sheet], destination: Path) -> dict[str, int]:
    """Write a copy of `source` with every call-out boxed. Returns how many boxes of each colour."""
    writer = PdfWriter(clone_from=PdfReader(source))
    counts: dict[str, int] = {}
    for sheet in sheets:
        page = writer.pages[sheet.page - 1]
        rotation = int(page.get("/Rotate", 0))
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        if rotation % 180:
            width, height = height, width
        for callout in sheet.callouts:
            kind = status(callout, sheet.safe_path_m)
            colour = COLOURS[kind]
            counts[kind] = counts.get(kind, 0) + 1
            left, bottom = float(page.mediabox.left), float(page.mediabox.bottom)
            x0, y0, x1, y1 = pdf_rect(callout.box, width, height, rotation)
            annotation = Rectangle(rect=(x0 + left, y0 + bottom, x1 + left, y1 + bottom))
            annotation[NameObject("/C")] = ArrayObject([FloatObject(c) for c in colour])
            annotation[NameObject("/Contents")] = TextStringObject(_label(callout, sheet))
            annotation[NameObject("/F")] = NumberObject(4)  # print with the page
            writer.add_annotation(sheet.page - 1, annotation)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        writer.write(handle)
    return counts
