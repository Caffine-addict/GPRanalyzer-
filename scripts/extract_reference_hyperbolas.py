#!/usr/bin/env python3
"""Cut the individual B-scan crops out of the company's GPR survey deliverable sheets.

The two sheets in `reference/hyperbolas/sheets/` are real Puravankara-site
deliverables: a CAD utility plan with numbered call-outs, alongside a strip
of the GPR scan crops that justified each call-out. That pairing is the
useful part — every crop is a hyperbola an actual surveyor looked at,
marked on the plan, and signed off on. That makes them the only confirmed
ground truth in this repo (`Dataset/DSU_GPR_Files/` is raw and unlabelled).

Extraction is geometric, not hand-typed: the crops are the only large,
solidly-filled greyscale regions on an otherwise white page covered in
coloured CAD line-work, so a low-saturation mask plus a fill-ratio test
isolates them. The fill test is what separates a crop from a false
positive — real crops come out at 0.77-0.99 contour fill, incidental
greyscale line-work at 0.08-0.32, with nothing in between.

What this does NOT do is claim a class for any crop. The plan labels each
call-out with a depth (`D-1.22`) and a utility type in the legend, but
tying crop N to the right call-out N means reading the CAD text, which
isn't attempted here. Provenance recorded, interpretation left open —
same rule as scripts/box_store.py.

Usage:
    .venv/bin/python scripts/extract_reference_hyperbolas.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

_SHEETS_DIR = Path("reference/hyperbolas/sheets")
_CROPS_DIR = Path("reference/hyperbolas/crops")
_MANIFEST_PATH = Path("reference/hyperbolas/manifest.json")

# A crop is greyscale (low saturation) and neither paper-white nor ink-black.
_MAX_SATURATION = 40
_MIN_VALUE, _MAX_VALUE = 30, 225
# Separates real crops from incidental greyscale CAD line-work. Measured on
# both sheets: crops fill 0.77-0.99 of their bounding box, line-work 0.08-0.32.
_MIN_FILL_RATIO = 0.70
_MIN_AREA_PX = 20_000
_ROW_TOLERANCE_PX = 60  # crops within this vertical distance are the same row


def _greyscale_regions(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Bounding boxes of the solid greyscale blocks on a sheet, reading order."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation, value = hsv[:, :, 1], hsv[:, :, 2]
    mask = (
        (saturation < _MAX_SATURATION) & (value > _MIN_VALUE) & (value < _MAX_VALUE)
    ).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h < _MIN_AREA_PX:
            continue
        if cv2.contourArea(contour) / (w * h) < _MIN_FILL_RATIO:
            continue
        boxes.append((x, y, w, h))

    if not boxes:
        return []

    # A contour that bleeds into the ①-style number label underneath comes out
    # 10-15% taller than its siblings. The crops on a sheet are one uniform
    # size, so the median height is the true one — trim rather than keep a
    # crop with a caption baked into it.
    median_h = int(np.median([h for _, _, _, h in boxes]))
    boxes = [(x, y, w, min(h, median_h)) for x, y, w, h in boxes]

    # Reading order: group into rows first (y alone mis-sorts a row whose
    # boxes differ by a pixel or two), then left-to-right within each row.
    boxes.sort(key=lambda b: b[1])
    rows: list[list[tuple[int, int, int, int]]] = []
    for box in boxes:
        if rows and abs(box[1] - rows[-1][0][1]) <= _ROW_TOLERANCE_PX:
            rows[-1].append(box)
        else:
            rows.append([box])
    return [box for row in rows for box in sorted(row, key=lambda b: b[0])]


def extract_sheet(sheet_path: Path, crops_dir: Path) -> list[dict]:
    """Write one sheet's crops as PNGs and return their manifest entries."""
    image = cv2.imread(str(sheet_path))
    if image is None:
        raise FileNotFoundError(f"could not read sheet: {sheet_path}")

    stem = sheet_path.stem.replace("GPR_Sample_Hyperbolas", "sheet")
    entries = []
    for index, (x, y, w, h) in enumerate(_greyscale_regions(image), start=1):
        crop_name = f"{stem}_{index:02d}.png"
        cv2.imwrite(str(crops_dir / crop_name), image[y : y + h, x : x + w])
        entries.append(
            {
                "id": f"{stem}-{index:02d}",
                "sheet": sheet_path.name,
                "callout": index,  # the ①..⑧ number printed under this crop on the sheet
                "file": f"crops/{crop_name}",
                "sheet_bbox": {"x": x, "y": y, "w": w, "h": h},
                "confirmed_by": "site surveyor (crop appears on a signed-off deliverable plan)",
                "label_class": None,  # see module docstring: not read off the CAD text
                "label_depth_m": None,
            }
        )
    return entries


def main() -> int:
    sheets = sorted(_SHEETS_DIR.glob("*.jpg"))
    if not sheets:
        print(f"no sheets found in {_SHEETS_DIR}", file=sys.stderr)
        return 1

    _CROPS_DIR.mkdir(parents=True, exist_ok=True)
    crops = [entry for sheet in sheets for entry in extract_sheet(sheet, _CROPS_DIR)]

    _MANIFEST_PATH.write_text(
        json.dumps(
            {
                "source": "Puravankara GPR utility-survey deliverable sheets (Bangalore ORR / "
                "Devarabisanahalli), same corridor as Dataset/DSU_GPR_Files",
                "note": "Crops are confirmed hyperbolas — a surveyor marked each one on the "
                "accompanying utility plan. Class and depth labels are NOT transcribed from "
                "the CAD call-outs; see scripts/extract_reference_hyperbolas.py.",
                "crops": crops,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"wrote {len(crops)} crops to {_CROPS_DIR} and {_MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
