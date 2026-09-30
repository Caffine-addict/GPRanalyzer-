"""Read vendor SUE drawings, mark them, and tabulate what they say.

    python scripts/process_sue_drawings.py "Dataset/DSU_GPR_Files/GPR_24AUG2026/Sky group File dwg" \
        "Dataset/.../Rajbhavan Road/1065 Lhs/1065 Lhs.pdf"

Each argument is a PDF, or a folder whose PDFs (directly inside it, not in subfolders) are
all read. A drawing with a text layer is read from it exactly; one whose text was converted to
outlines is read by OCR (`reference/sue_ocr.py`, needs the `tesseract` binary), which misses
some labels, and its rows say so. Writes, into the `_processed` folder beside the survey's
GPR_24AUG2026 folder (which GPR Studio lists under Survey documents):

- `<drawing> - marked.pdf` — the vendor's drawing with every call-out boxed (see
  `reference/sue_marking.py` for the colours);
- `sue_utilities.csv` — one row per call-out;
- `sue_geo_points.csv` — the Lat/Long points printed on each sheet.

`--ocr-check` instead OCRs the text-layer drawings given and scores OCR against their text,
where the right answer is known — how far to trust the OCR-read rows.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reference.sue_marking import mark_pdf, status
from reference.sue_ocr import read_pdf_ocr, tesseract_available
from reference.sue_sheets import Sheet, has_text_layer, read_pdf


def _pdfs(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        found.extend(sorted(path.glob("*.pdf")) if path.is_dir() else [path])
    return found


def _output_folder(first: Path) -> Path:
    survey = next((p for p in first.resolve().parents if p.name == "GPR_24AUG2026"), None)
    return (survey if survey is not None else first.resolve().parent) / "_processed"


def _match(truth: Sheet, read: Sheet) -> tuple[int, int, int]:
    """(found, extra, missed): OCR call-outs matched to text call-outs by type, depth and place."""
    left = list(truth.callouts)
    found = extra = 0
    for c in read.callouts:
        cx, cy = (c.box[0] + c.box[2]) / 2, (c.box[1] + c.box[3]) / 2
        hit = next((t for t in left if t.utility == c.utility and t.depth_m == c.depth_m
                    and abs((t.box[0] + t.box[2]) / 2 - cx) < 8 and abs((t.box[1] + t.box[3]) / 2 - cy) < 6), None)
        if hit is None:
            extra += 1
        else:
            left.remove(hit)
            found += 1
    return found, extra, len(left)


def ocr_check(pdfs: list[Path]) -> int:
    if not tesseract_available():
        print("no tesseract binary on PATH — nothing to check")
        return 1
    totals = [0, 0, 0]
    for pdf in pdfs:
        if not has_text_layer(pdf):
            print(f"{pdf.name}: no text layer, nothing to score against — skipped")
            continue
        try:
            scores = [_match(t, o) for t, o in zip(read_pdf(pdf), read_pdf_ocr(pdf), strict=True)]
        except Exception as exc:  # noqa: BLE001 — one failed drawing must not cost the others their score
            print(f"{pdf.name}: FAILED, skipped — {type(exc).__name__}: {exc}")
            continue
        found, extra, missed = (sum(s[i] for s in scores) for i in range(3))
        totals = [totals[0] + found, totals[1] + extra, totals[2] + missed]
        print(f"{pdf.name}: OCR found {found}, missed {missed}, read {extra} not matching the text")
    found, extra, missed = totals
    if found + missed:
        print(f"recall {found / (found + missed):.2f}, precision {found / max(1, found + extra):.2f} "
              f"({found} found, {missed} missed, {extra} not matching)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", type=Path, nargs="+")
    parser.add_argument("--ocr-check", action="store_true", help="score OCR against text-layer drawings")
    args = parser.parse_args(argv)
    pdfs = _pdfs(args.paths)
    if not pdfs:
        parser.error("no PDFs found")
    if args.ocr_check:
        return ocr_check(pdfs)
    out = _output_folder(args.paths[0])
    out.mkdir(exist_ok=True)

    utilities, geo, failed = [], [], []
    for pdf in pdfs:
        try:
            if has_text_layer(pdf):
                sheets, read_by = read_pdf(pdf), "text"
            elif tesseract_available():
                sheets, read_by = read_pdf_ocr(pdf), "ocr"
            else:
                raise RuntimeError("no text layer, and no tesseract binary to OCR it")
            counts = mark_pdf(pdf, sheets, out / f"{pdf.stem} - marked.pdf")
        except Exception as exc:  # noqa: BLE001 — one unreadable drawing must not cost the others their output
            print(f"{pdf.name}: FAILED, skipped — {type(exc).__name__}: {exc}")
            failed.append(pdf.name)
            continue
        print(f"{pdf.name}: {sum(counts.values())} call-outs on {len(sheets)} sheets, read by {read_by} {counts}")
        for sheet in sheets:
            for c in sheet.callouts:
                utilities.append({
                    "drawing": pdf.name, "sheet": sheet.page, "utility": c.utility,
                    "depth_m": c.depth_m, "chainage_m": c.chainage_m, "chainage_source": c.chainage_source,
                    "safe_path_m": sheet.safe_path_m, "status": status(c, sheet.safe_path_m), "read_by": read_by,
                })
            for g in sheet.geo:
                geo.append({"drawing": pdf.name, "sheet": sheet.page, "lat": g.lat, "lon": g.lon,
                            "chainage_m": g.chainage_m, "read_by": read_by})

    for name, rows in (("sue_utilities.csv", utilities), ("sue_geo_points.csv", geo)):
        if not rows:
            continue
        with (out / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(f"wrote {len(utilities)} call-outs and {len(geo)} geo points to {out}")
    if failed:
        print(f"{len(failed)} drawing(s) failed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
