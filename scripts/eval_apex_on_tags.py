"""Score an apex detector against the operator tags, exactly as scripts/validate_on_tags.py scores the picker.

The model sees each tagged RAD line in overlapping windows of simulate.composite.WINDOW traces,
through the same simulate.composite.to_image the training data was made with. Its apexes are merged
across windows (one per 0.3 m and 1.5 ns, most confident kept), and the 15 most confident per line
are matched to the tags: the picker's budget, tolerance and random-position chance control.

The tagged sites (Set3-5) are never used for training (simulate.composite.TEST_SITES).

Usage: .venv/bin/python scripts/eval_apex_on_tags.py runs/detect/apex_v1/weights/best.pt [folder] [--stack]
--stack: a 3-channel model (simulate/real_stack.py); its apexes are mapped back to RAD samples.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parsers.spr import parse_spr
from parsers.spr_tags import read_tags
from simulate.composite import WINDOW, to_image

TOLERANCE_M, TOLERANCE_NS = 0.3, 1.5
PICKS = 15  # validate_on_tags.PICKS: the same budget the picker gets per line
CHANCE_DRAWS = 20


def apexes(model, traces: np.ndarray, spacing: float, dt: float, image=to_image) -> list[tuple[int, int, float]]:  # type: ignore[no-untyped-def]
    """(trace, sample, confidence) over the whole line, most confident first, one per tolerance cell."""
    n = traces.shape[0]
    starts = sorted({*range(0, max(1, n - WINDOW + 1), WINDOW // 2), max(0, n - WINDOW)})
    found: list[tuple[int, int, float]] = []
    for start in starts:
        window = traces[start:start + WINDOW]
        result = model.predict(image(window), imgsz=256, conf=0.01, verbose=False)[0]
        for (cx, cy, _w, _h), conf in zip(result.boxes.xywh.tolist(), result.boxes.conf.tolist(), strict=True):
            found.append((start + round(cx), round(cy), float(conf)))
    kept: list[tuple[int, int, float]] = []
    for t, s, c in sorted(found, key=lambda a: -a[2]):
        if all(abs(t - k[0]) * spacing > TOLERANCE_M or abs(s - k[1]) * dt > TOLERANCE_NS for k in kept):
            kept.append((t, s, c))
    return kept


def main(weights: Path, folder: Path, stacked: bool = False) -> int:
    from ultralytics import YOLO

    model = YOLO(str(weights))
    rows: list[tuple[bool, float]] = []
    for tag_file in sorted(folder.rglob("*.tag")):
        data = tag_file.with_suffix(".RAD")
        if not data.exists():
            continue
        frame = parse_spr(data)
        traces, dt, spacing = frame.traces, frame.sample_interval_ns, frame.trace_spacing_m
        if stacked:
            from detect.channel_fusion import time_zero_ns
            from simulate import real_stack

            frames = {ext: parse_spr(tag_file.with_suffix(f".{ext}")) for ext in real_stack.CHANNELS}
            line = real_stack.stack({e: (f.traces, f.sample_interval_ns) for e, f in frames.items()})
            zero = time_zero_ns(traces, dt)
            found = apexes(model, line, spacing, real_stack.GRID_NS, real_stack.window_image)
            picks = [(t, round((r * real_stack.GRID_NS - real_stack.LEAD_NS + zero) / dt)) for t, r, _ in found[:PICKS]]
        else:
            picks = [(t, s) for t, s, _ in apexes(model, traces, spacing, dt)[:PICKS]]
        for tag in (t for t in read_tags(tag_file, traces.shape[1]) if t.in_record):
            def hit(points: list[tuple[int, int]], tag=tag, spacing=spacing, dt=dt) -> bool:
                return any(abs(x - tag.trace) * spacing <= TOLERANCE_M and abs(y - tag.sample) * dt <= TOLERANCE_NS
                           for x, y in points)
            rng = np.random.default_rng(tag.trace)
            chance = statistics.mean(hit([(int(rng.integers(0, len(traces))), y) for _, y in picks])
                                     for _ in range(CHANCE_DRAWS))
            rows.append((hit(picks), chance))
    if not rows:
        print(f"no tagged RAD lines under {folder}")
        return 1
    hits = sum(found for found, _ in rows)
    print(f"RAD  apex detector ({weights.parent.parent.name}) {hits}/{len(rows)} tags found "
          f"({100 * hits / len(rows):.0f}%), chance {100 * statistics.mean(c for _, c in rows):.0f}%")
    print("RAD  migration picker (for comparison)       10/23 tags found (43%), chance 14%  [validate_on_tags.py]")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--stack"]
    raise SystemExit(main(Path(args[0]), Path(args[1]) if len(args) > 1 else Path("Dataset/incoming"), "--stack" in sys.argv))
