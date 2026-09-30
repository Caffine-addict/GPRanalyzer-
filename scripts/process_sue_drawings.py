"""Read every vendor SUE drawing in a folder, mark it, and tabulate what it says.

    python scripts/process_sue_drawings.py "Dataset/DSU_GPR_Files/GPR_24AUG2026/Sky group File dwg"

Reads only the PDFs directly in that folder: the copies in its subfolders have their text
converted to outlines, so there is nothing to read in them without OCR. Writes, into a
`_processed` folder beside it (which GPR Studio lists under Survey documents):

- `<road> - marked.pdf` — the vendor's drawing with every call-out boxed (see
  `reference/sue_marking.py` for the colours);
- `sue_utilities.csv` — one row per call-out;
- `sue_geo_points.csv` — the Lat/Long points printed on each sheet.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reference.sue_marking import mark_pdf, status
from reference.sue_sheets import read_pdf


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    args = parser.parse_args(argv)
    pdfs = sorted(args.folder.glob("*.pdf"))
    if not pdfs:
        parser.error(f"no PDFs directly in {args.folder}")
    out = args.folder.parent / "_processed"
    out.mkdir(exist_ok=True)

    utilities, geo = [], []
    failed = []
    for pdf in pdfs:
        try:
            sheets = read_pdf(pdf)
            counts = mark_pdf(pdf, sheets, out / f"{pdf.stem} - marked.pdf")
        except Exception as exc:  # noqa: BLE001 — one unreadable drawing must not cost the others their output
            print(f"{pdf.name}: FAILED, skipped — {type(exc).__name__}: {exc}")
            failed.append(pdf.name)
            continue
        print(f"{pdf.name}: {sum(counts.values())} call-outs on {len(sheets)} sheets {counts}")
        for sheet in sheets:
            for c in sheet.callouts:
                utilities.append({
                    "drawing": pdf.name, "sheet": sheet.page, "utility": c.utility,
                    "depth_m": c.depth_m, "chainage_m": c.chainage_m, "chainage_source": c.chainage_source,
                    "safe_path_m": sheet.safe_path_m, "status": status(c, sheet.safe_path_m),
                })
            for g in sheet.geo:
                geo.append({"drawing": pdf.name, "sheet": sheet.page, "lat": g.lat, "lon": g.lon,
                            "chainage_m": g.chainage_m})

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
