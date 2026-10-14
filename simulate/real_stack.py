"""Training data from real echoes only: three channels stacked, the picker's surest targets as labels.

docs/TRAINING_PLAN.md, step 1b (2026-10-14). The composite model learned the look of pasted analytic
echoes. Here every echo is real: images are this radar's own lines with RAD/RA1/RA2 aligned at their
own time zero and stacked as colour layers; labels are merged picker targets seen on 2+ channels and
clear of the direct wave; real echoes cut from those targets are pasted elsewhere for more examples.
Train on Job/Set1, choose the checkpoint on Set2's targets; the tagged sites (Set3-5) are never used.

ponytail: the labels are the picker's own, so the model mostly learns to agree with it; weaker real
arches left unlabelled are label noise. Verified labels (studio/verify.py) are the upgrade.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from detect.channel_fusion import time_zero_ns
from simulate.composite import TEST_SITES, VAL_SITE, WINDOW, site_of, to_image, yolo_line
from studio import session, site_map

GRID_NS, ROWS, LEAD_NS = 0.2, 256, 2.0  # common time grid: 51 ns from 2 ns before each channel's time zero
CHANNELS = ("RAD", "RA1", "RA2")
MIN_CHANNELS, MIN_TIME_NS = 2, 2.5  # a "sure" target: seen on 2+ channels, below the direct wave's tail
EDGE = 12  # traces: an apex this close to a window edge is not labelled there
PATCH_T, PATCH_UP, PATCH_DOWN = 24, 10, 60  # a pasted echo: apex +-0.6 m, from 2 ns above to 12 ns below


def stack(channels: dict[str, tuple[np.ndarray, float]]) -> np.ndarray:
    """{ext: (traces (n_traces, n_samples), dt)} -> (n_traces, ROWS, 3) on the common grid, time zero aligned."""
    out = []
    for ext in CHANNELS:
        traces, dt = channels[ext]
        times = time_zero_ns(traces, dt) - LEAD_NS + np.arange(ROWS) * GRID_NS
        native = np.arange(traces.shape[1]) * dt
        out.append(np.stack([np.interp(times, native, trace, left=0.0, right=0.0) for trace in traces]))
    return np.stack(out, axis=-1)


def window_image(window: np.ndarray) -> np.ndarray:
    """(n, ROWS, 3) float -> (ROWS, n, 3) uint8: each channel through the same view as simulate.composite."""
    return np.stack([to_image(window[:, :, c]) for c in range(3)], axis=-1)


def row_of(time_from_zero_ns: float) -> int:
    return round((time_from_zero_ns + LEAD_NS) / GRID_NS)


def _job_line(job_dir: Path) -> tuple[np.ndarray, list[tuple[int, int]]] | None:
    """A line's stack and its sure targets as (trace, row)."""
    infos = {i.extension.upper(): i for i in session.available_channels(job_dir)}
    if not set(CHANNELS) <= set(infos):
        return None
    data = {ext: (session.raw_traces(session.load_frame(job_dir, infos[ext].extension)), infos[ext].sample_interval_ns)
            for ext in CHANNELS}
    sure = [(int(t), row_of(tns)) for t, tns, chans, _ in site_map._detected(str(job_dir), site_map._mtime(job_dir))
            if len(chans) >= MIN_CHANNELS and tns >= MIN_TIME_NS and row_of(tns) < ROWS - PATCH_DOWN // 2]
    return stack(data), sure


def _patch(line: np.ndarray, t: int, r: int) -> np.ndarray | None:
    """A real echo around its apex, background removed and tapered at the sides, or None at an edge."""
    if t - PATCH_T < 0 or t + PATCH_T + 1 > len(line) or r - PATCH_UP < 0 or r + PATCH_DOWN > ROWS:
        return None
    ground = line - np.median(line, axis=0, keepdims=True)
    cut = ground[t - PATCH_T: t + PATCH_T + 1, r - PATCH_UP: r + PATCH_DOWN]
    return cut * np.hanning(2 * PATCH_T + 1)[:, None, None]


def build(dataset_dir: Path, out_dir: Path, *, copies: int = 3, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    lines: dict[str, list[tuple[str, np.ndarray, list[tuple[int, int]]]]] = {"train": [], "val": []}
    for job in session.list_jobs(dataset_dir):
        site = site_of(job)
        if "_" not in job or site in TEST_SITES:
            continue
        found = _job_line(session.resolve_job(job, dataset_dir))
        if found:
            lines["val" if site == VAL_SITE else "train"].append((job, *found))
    patches = [p for _, line, sure in lines["train"] for t, r in sure if (p := _patch(line, t, r)) is not None]
    counts = {}
    for split, group in lines.items():
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)
        n_img = n_lab = 0
        for job, line, sure in group:
            for start in range(0, max(1, len(line) - WINDOW + 1), WINDOW // 4):
                for copy in range(copies if split == "train" else 1):
                    window = line[start:start + WINDOW].copy()
                    apexes = [(t - start, r) for t, r in sure if EDGE <= t - start < WINDOW - EDGE]
                    if split == "train" and copy and patches:  # paste 1-2 real echoes from elsewhere
                        for _ in range(int(rng.integers(1, 3))):
                            p = patches[int(rng.integers(len(patches)))]
                            p = p[::-1] if rng.random() < 0.5 else p
                            t = int(rng.integers(max(EDGE, PATCH_T), WINDOW - max(EDGE, PATCH_T)))
                            r = int(rng.integers(row_of(MIN_TIME_NS) + PATCH_UP, ROWS - PATCH_DOWN))
                            window[t - PATCH_T: t + PATCH_T + 1, r - PATCH_UP: r + PATCH_DOWN] += p * rng.uniform(0.6, 1.4)
                            apexes.append((t, r))
                    name = f"{job}_{start:04d}_{copy}"
                    cv2.imwrite(str(out_dir / "images" / split / f"{name}.png"), window_image(window))
                    (out_dir / "labels" / split / f"{name}.txt").write_text(
                        "".join(yolo_line(a, WINDOW, ROWS) + "\n" for a in apexes))
                    n_img, n_lab = n_img + 1, n_lab + len(apexes)
        counts[split] = {"lines": len(group), "images": n_img, "apexes": n_lab}
    (out_dir / "data.yaml").write_text(f"path: {out_dir.resolve()}\ntrain: images/train\nval: images/val\nnames:\n  0: apex\n")
    summary = {"counts": counts, "real_patches": len(patches), "test_sites_excluded": sorted(TEST_SITES)}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
