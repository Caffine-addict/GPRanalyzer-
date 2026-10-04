"""Score target detection against the operator's own tags on SPRScan lines.

An SPRScan line may come with a `.tag` file (parsers/spr_tags.py): points an operator marked on
the radargram. Many sit exactly on hyperbola apexes, most strongly on the mid channel (RA1), so
they are the only labels that exist for the company's own radar. Not all are targets: about half
lie where no channel shows a clear echo, so the absolute numbers understate both methods equally.

A tag counts as found when a pick lies within 0.3 m along the line and 1.5 ns of it. The chance
level is the same picks at random positions along the line. Compared, on each channel:
- the migration picker (detect/migration_picker.py), what the Studio now generates;
- the earlier energy-envelope candidates (detect/classical.find_candidate_boxes).

Usage: .venv/bin/python scripts/validate_on_tags.py <folder containing *.tag beside their channels>
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detect.classical import find_candidate_boxes
from detect.migration_picker import pick_targets
from parsers.spr import parse_spr
from parsers.spr_tags import read_tags

TOLERANCE_M, TOLERANCE_NS = 0.3, 1.5
PICKS = 15
CHANCE_DRAWS = 20


def _found(tag_trace: int, tag_sample: int, picks: list[tuple[int, int]], spacing: float, dt: float) -> bool:
    return any(abs(x - tag_trace) * spacing <= TOLERANCE_M and abs(y - tag_sample) * dt <= TOLERANCE_NS for x, y in picks)


def main(folder: Path) -> int:
    results: dict[tuple[str, str], list[tuple[bool, float]]] = {}
    for tag_file in sorted(folder.rglob("*.tag")):
        for channel in ("RAD", "RA1", "RA2"):
            data_file = tag_file.with_suffix(f".{channel}")
            if not data_file.exists():
                continue
            frame = parse_spr(data_file)
            traces, dt, spacing = frame.traces, frame.sample_interval_ns, frame.trace_spacing_m
            if traces is None or not dt or not spacing:
                continue
            methods = {
                "migration picker": [(p.trace, p.sample) for p in pick_targets(
                    traces, sample_interval_ns=dt, trace_spacing_m=spacing,
                    dielectric=frame.dielectric_assumed or 9.0, max_picks=PICKS)],
                "energy-envelope candidates": [(int(x + w / 2), int(y)) for x, y, w, h in find_candidate_boxes(traces, dt)],
            }
            for tag in (t for t in read_tags(tag_file, traces.shape[1]) if t.in_record):
                rng = np.random.default_rng(tag.trace)
                for name, picks in methods.items():
                    chance = statistics.mean(
                        _found(tag.trace, tag.sample, [(int(rng.integers(0, len(traces))), y) for _, y in picks], spacing, dt)
                        for _ in range(CHANCE_DRAWS))
                    results.setdefault((channel, name), []).append((_found(tag.trace, tag.sample, picks, spacing, dt), chance))
    if not results:
        print(f"no tagged SPR lines under {folder}")
        return 1
    for (channel, name), rows in sorted(results.items()):
        hits = sum(found for found, _ in rows)
        print(f"{channel}  {name:28s} {hits}/{len(rows)} tags found ({100 * hits / len(rows):.0f}%), "
              f"chance {100 * statistics.mean(c for _, c in rows):.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("Dataset/incoming")))
