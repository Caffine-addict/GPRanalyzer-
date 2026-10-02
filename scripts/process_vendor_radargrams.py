"""Put the vendor's circled radargram targets into metres, and score our detector against them.

    python scripts/process_vendor_radargrams.py "Dataset/.../Gandhidham-Mundra (extracted from Sample Docs-DPR rar)/Radargrams"

Writes into a `_processed` folder beside the dataset's GPR_24AUG2026 folder (which GPR Studio
lists under Survey documents):

- `radargrams/<image> - marked.png` — the calibrated grid (thin, to check `AXES` by eye), the
  classical detector's boxes (cyan; green when centred in a vendor circle), the screenshot
  matched filter's apexes (magenta crosses) and the vendor circles (red);
- `vendor_radargram_targets.csv` — each circle's position and depth in metres, whether our
  detector put a box on it, and what the hyperbola under it says about the report's velocity
  (dielectric 7.3): see `reference.radargram_marks.check_velocity`.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reference.radargram_marks import (
    AXES,
    REPORTED_DIELECTRIC,
    Axes,
    check_velocity,
    detector_boxes,
    envelope,
    hits,
    hyperbola_candidates,
    radargram_only,
    red_circles,
)

_GRID = (170, 170, 170)


def _draw_grid(image: np.ndarray, axes: Axes) -> None:
    h, w = image.shape[:2]
    per_x, per_d = axes.metres_per_px()
    left_m, top_m = axes.to_metres(axes.plot_left, axes.plot_top)
    right_m, bottom_m = axes.to_metres(w - 1, h - 1)
    for metres in np.arange(np.ceil(left_m), right_m, 1.0):
        x = round(axes.plot_left + (metres - left_m) / per_x)
        cv2.line(image, (x, axes.plot_top), (x, h - 1), _GRID, 1)
    for depth in np.arange(0.5, bottom_m, 0.5):
        y = round(axes.plot_top + (depth - top_m) / per_d)
        cv2.line(image, (axes.plot_left, y), (w - 1, y), _GRID, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    args = parser.parse_args(argv)
    out = next(p for p in args.folder.resolve().parents if p.name == "GPR_24AUG2026") / "_processed"
    (out / "radargrams").mkdir(parents=True, exist_ok=True)

    rows = []
    for name, axes in AXES.items():
        path = args.folder / f"{name}.PNG"
        if not path.exists():
            print(f"{path.name}: missing, skipped")
            continue
        bgr = cv2.imread(str(path))
        if bgr is None:
            print(f"{path.name}: not a readable image, skipped")
            continue
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        circles = red_circles(rgb)
        boxes = detector_boxes(radargram_only(rgb, axes))
        offset = (axes.plot_left, axes.plot_top)
        found = hits(circles, boxes, offset)
        energy = envelope(radargram_only(rgb, axes))
        apexes = [(x + offset[0], y + offset[1]) for x, y, _ in hyperbola_candidates(energy, axes)]

        marked = bgr.copy()
        _draw_grid(marked, axes)
        for box in boxes:
            bx, by, bw, bh = box
            on_target = hits(circles, [box], offset)
            colour = (0, 200, 0) if any(on_target) else (230, 200, 0)
            cv2.rectangle(marked, (bx + offset[0], by + offset[1]), (bx + bw + offset[0], by + bh + offset[1]), colour, 1)
        for (cx, cy, r), hit in zip(circles, found, strict=True):
            cv2.circle(marked, (round(cx), round(cy)), round(r), (0, 0, 255), 2)
            on_it = any((ax_ - cx) ** 2 + (ay - cy) ** 2 <= r**2 for ax_, ay in apexes)
            x_m, depth_m = axes.to_metres(cx, cy)
            fit = check_velocity(energy, axes, (cx, cy, r))
            rows.append({"image": path.name, "x_m": round(x_m, 2), "depth_m": round(depth_m, 2),
                         "radius_m": round(r * axes.metres_per_px()[1], 2), "detector_box_on_it": hit,
                         "matched_filter_on_it": on_it,
                         "apex_depth_m": None if fit is None else round(fit.apex_depth_m, 2),
                         "fit_r2": None if fit is None else round(fit.fit_r2, 3),
                         "implied_dielectric": None if fit is None else round(fit.implied_dielectric, 1),
                         "fit_rejected": "no hyperbola fits" if fit is None else fit.rejected})
        for ax_, ay in apexes:
            cv2.drawMarker(marked, (ax_, ay), (255, 0, 255), cv2.MARKER_CROSS, 14, 2)
        cv2.imwrite(str(out / "radargrams" / f"{name} - marked.png"), marked)
        print(f"{path.name}: {len(circles)} vendor targets, {sum(found)} with a detector box ({len(boxes)} boxes); "
              f"matched filter: {len(apexes)} apexes")

    with (out / "vendor_radargram_targets.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"{sum(r['detector_box_on_it'] for r in rows)}/{len(rows)} vendor targets have a detector box centred on them")
    print(f"{sum(r['matched_filter_on_it'] for r in rows)}/{len(rows)} have a matched-filter apex inside them")
    credible = sorted(float(r["implied_dielectric"]) for r in rows if r["implied_dielectric"] is not None and not r["fit_rejected"])
    if credible:
        print(f"{len(credible)} credible hyperbola fits imply dielectric {credible} "
              f"(median {credible[len(credible) // 2]}; the report used {REPORTED_DIELECTRIC})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
