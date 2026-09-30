"""Reading SUE sheets whose text was converted to outlines, by OCR.

Some delivered drawings (Rajbhavan Road, and the per-sheet copies) carry no text layer — every
letter is vector outline — so `reference.sue_sheets.read_pdf` finds nothing on them. Here each
page is rendered, its long straight lines (leaders, road edges) erased so they stop cutting
through the labels, and read with Tesseract. The word boxes come back in the same coordinates
`sue_sheets` uses, scaled to the A3 template (842 pt wide), so the same pairing rules apply.

OCR misses labels and never fills them in: a word it can't read is simply absent, so a sheet
read this way gives a *lower bound* on its call-outs. Its accuracy is measured, not assumed —
`scripts/process_sue_drawings.py --ocr-check` scores it against the text-layer drawings, where
the right answer is known.

Needs the `tesseract` binary (Homebrew/apt/UB-Mannheim on Windows); it is not a Python package.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium

from reference.sue_sheets import Callout, Sheet, Word, is_tick_label, read_pages

TEMPLATE_WIDTH = 842.0  # A3 landscape in points; larger sheets are the same template scaled up
PX_PER_POINT = 12  # render density; 8 and 16 read the same labels on the trial sheet, 12 is the middle
_INK = 200  # grey below this is ink
# Leader lines and road edges are long and straight; letters are not. 160 px at 12 px/pt is
# 13 pt, well over any character's height on these sheets.
_HOUGH = {"rho": 1, "theta": np.pi / 360, "threshold": 120, "minLineLength": 160, "maxLineGap": 4}


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def _erase_lines(ink: np.ndarray) -> np.ndarray:
    lines = cv2.HoughLinesP(ink, **_HOUGH)  # type: ignore[call-overload]
    cleaned = ink.copy()
    for x0, y0, x1, y1 in ([] if lines is None else lines.reshape(-1, 4)):
        cv2.line(cleaned, (int(x0), int(y0)), (int(x1), int(y1)), 0, 5)
    return cleaned


def _tesseract_words(image: np.ndarray, px_per_point: float) -> list[Word]:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "page.png"
        cv2.imwrite(str(path), image)
        result = subprocess.run(["tesseract", str(path), "-", "--psm", "11", "tsv"],
                                capture_output=True, text=True, check=True)
    words = []
    for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        if not text or float(row["conf"]) < 0:
            continue
        x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        words.append({"text": text, "x0": x / px_per_point, "x1": (x + w) / px_per_point,
                      "top": y / px_per_point, "bottom": (y + h) / px_per_point})
    return words


def _tick_words(clean: np.ndarray, px_per_point: float) -> list[Word]:
    """Chainage tick labels, which these sheets print vertically: OCR the page turned each way.

    Only words that are tick labels are kept — everything else on a turned page is the
    horizontal text read sideways, i.e. noise. Boxes are mapped back to the upright page.
    """
    height, width = clean.shape
    found = []
    for turn in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
        for w in _tesseract_words(cv2.rotate(clean, turn), 1.0):
            if not is_tick_label(w["text"]):
                continue
            left, top, right, bottom = w["x0"], w["top"], w["x1"], w["bottom"]
            if turn == cv2.ROTATE_90_CLOCKWISE:  # upright (x, y) was turned to (H-1-y, x)
                box = (top, height - right, bottom, height - left)
            else:  # upright (x, y) was turned to (y, W-1-x)
                box = (width - bottom, left, width - top, right)
            found.append({"text": w["text"], "x0": box[0] / px_per_point, "top": box[1] / px_per_point,
                          "x1": box[2] / px_per_point, "bottom": box[3] / px_per_point})
    return found


def page_words(page: pdfium.PdfPage) -> tuple[list[Word], float, float]:
    """OCR words of one page in template points, the page height in the same units, and the scale.

    `scale` is template points per page point: divide by it to get back to the page.
    """
    width, height = page.get_size()
    scale = TEMPLATE_WIDTH / width
    grey = np.asarray(page.render(scale=PX_PER_POINT * scale).to_pil().convert("L"))
    ink = (grey < _INK).astype(np.uint8) * 255
    clean = 255 - _erase_lines(ink)
    words = _tesseract_words(clean, PX_PER_POINT) + _tick_words(clean, PX_PER_POINT)
    return words, height * scale, scale


def _to_page(callout: Callout, scale: float) -> Callout:
    """A call-out read in template points, with its box back in the page's own points for marking."""
    return dataclasses.replace(callout, box=tuple(v / scale for v in callout.box))  # type: ignore[arg-type]


def read_pdf_ocr(path: Path) -> list[Sheet]:
    """Like `sue_sheets.read_pdf`, for a drawing with no text layer. Boxes are in page points."""
    document = pdfium.PdfDocument(str(path))
    try:
        read = [page_words(document[index]) for index in range(len(document))]
    finally:
        document.close()
    sheets = read_pages([(words, TEMPLATE_WIDTH, height) for words, height, _ in read])
    return [dataclasses.replace(sheet, callouts=tuple(_to_page(c, scale) for c in sheet.callouts))
            for sheet, (_, _, scale) in zip(sheets, read, strict=True)]
