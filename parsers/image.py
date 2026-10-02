"""Loads jpg/png/bmp files into a ScanFrame — image populated, no traces, position unknown.

Radargram images are often screenshots out of vendor software (RADAN, ReflexW, ...): the plot sits
inside white margins carrying axis ticks and labels, and an interpreter may have circled targets
in red. Both are cleaned here, at the parser, so nothing downstream mistakes a tick label or an
ink circle for a reflection:

- the plot area is cropped out of its margins (provenance "plot_crop"), and
- red ink is painted out of the data and kept as the annotator's marks (provenance
  "annotator_marks", [x, y, w, h] in cropped-image pixels) — a person's call about where
  targets are, worth comparing detections against, never evidence of its own.

An image with no white margins and no red ink comes through unchanged.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from core.contracts import ScanFrame
from parsers.base import register

_WHITE_LEVEL = 235  # a pixel this bright in all channels is page background, not radargram
_MARGIN_WHITE_FRACTION = 0.5  # a row/column more than half page background is margin
_MIN_PLOT_FRACTION = 0.3  # a "plot" narrower than this share of the image is a mis-detection
_MIN_MARK_PIXELS = 200  # smaller red specks are colour noise, not a drawn mark


def _is_red_ink(rgb: np.ndarray) -> np.ndarray:
    r, g, b = (rgb[..., i].astype(np.int16) for i in range(3))
    return (r > 150) & (g < 90) & (b < 90)


def _plot_span(white_fraction: np.ndarray) -> tuple[int, int]:
    """The longest run of non-margin rows (or columns): [start, stop)."""
    best, best_len, start = (0, len(white_fraction)), -1, None
    for i, is_margin in enumerate(np.append(white_fraction > _MARGIN_WHITE_FRACTION, True)):
        if not is_margin and start is None:
            start = i
        elif is_margin and start is not None:
            if i - start > best_len:
                best, best_len = (start, i), i - start
            start = None
    return best


def _plot_crop(rgb: np.ndarray) -> tuple[int, int, int, int]:
    """(top, bottom, left, right) of the plot inside any white page margins."""
    white = np.all(rgb >= _WHITE_LEVEL, axis=2)
    top, bottom = _plot_span(white.mean(axis=1))
    left, right = _plot_span(white.mean(axis=0))
    height, width = white.shape
    if bottom - top < _MIN_PLOT_FRACTION * height or right - left < _MIN_PLOT_FRACTION * width:
        return 0, height, 0, width
    return top, bottom, left, right


def _marks(ink: np.ndarray) -> list[list[int]]:
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8))
    return [
        [int(v) for v in stats[i, :4]]
        for i in range(1, n)
        if stats[i, cv2.CC_STAT_AREA] >= _MIN_MARK_PIXELS
    ]


@register("jpg", "jpeg", "png", "bmp")
def parse_image(path: Path) -> ScanFrame:
    path = Path(path)
    with Image.open(path) as im:
        rgb = np.array(im.convert("RGB"))

    crop = _plot_crop(rgb)
    full = (0, rgb.shape[0], 0, rgb.shape[1])
    top, bottom, left, right = crop
    rgb = rgb[top:bottom, left:right]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)  # grayscale, matches B-scan convention

    ink = _is_red_ink(rgb)
    provenance: dict[str, object] = {"path": str(path)}
    if crop != full:
        provenance["plot_crop"] = list(crop)
    if ink.any():
        provenance["annotator_marks"] = _marks(ink)
        # Dilated so the anti-aliased rim of the stroke goes too, then filled from its surroundings.
        mask = cv2.dilate(ink.astype(np.uint8) * 255, np.ones((5, 5), np.uint8))
        gray = cv2.inpaint(gray, mask, 3, cv2.INPAINT_TELEA)

    return ScanFrame(
        source_type="image_file",
        provenance=provenance,
        image=gray,
        traces=None,
        position=None,
        position_source="unknown",
    )
