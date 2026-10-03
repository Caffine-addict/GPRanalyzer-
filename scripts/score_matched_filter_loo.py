"""Leave-one-image-out score for the screenshot matched filter: tune on 6 radargrams, score the 7th."""
from pathlib import Path

import cv2

from reference import radargram_marks as rm

FOLDER = Path("Dataset/DSU_GPR_Files/GPR_24AUG2026/Gandhidham-Mundra (extracted from Sample Docs-DPR rar)/Radargrams")
GRID = [20, 30, 40, 50, 60]
data = {}
for name, axes in rm.AXES.items():
    rgb = cv2.cvtColor(cv2.imread(str(FOLDER / f"{name}.PNG")), cv2.COLOR_BGR2RGB)
    data[name] = (axes, rm.red_circles(rgb), rm.envelope(rm.radargram_only(rgb, axes)))

def hits(name, pct, top=rm.TOP_CANDIDATES):
    axes, circles, energy = data[name]
    rm._ALONG_CURVE_PERCENTILE = pct
    apexes = [(x + axes.plot_left, y + axes.plot_top) for x, y, _ in rm.hyperbola_candidates(energy, axes, top)]
    return sum(any((ax - cx) ** 2 + (ay - cy) ** 2 <= r * r for ax, ay in apexes) for cx, cy, r in circles), len(circles)

table = {(n, p): hits(n, p) for n in data for p in GRID}
total, found = 0, 0
for held in data:
    best = max(GRID, key=lambda p: sum(table[(n, p)][0] for n in data if n != held))
    h, t = table[(held, best)]
    found += h; total += t
    print(f"{held}: tuned on the other 6 -> percentile {best}; held-out {h}/{t}")
print(f"LEAVE-ONE-OUT: {found}/{total} vendor targets found at top {rm.TOP_CANDIDATES} per image")
for p in GRID:
    print(p, "all 7:", sum(table[(n, p)][0] for n in data), "/ 20 at top 10;", sum(hits(n, p, 5)[0] for n in data), "/ 20 at top 5")
