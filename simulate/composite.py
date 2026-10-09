"""Training data for an apex detector: simulated echoes on this radar's own empty ground.

Research (docs/TRAINING_PLAN.md, 2026-10-09): a detector trained on simulation alone sits at chance
on real ground (ours: runs/detect/prod1; published: 0.031 mAP on a held-out real site), and
labels should mark a target's apex, not the extent of its hyperbola. So each training image is a
stretch of a real RAD line where the migration picker finds nothing, plus a simulated scene's
scattered field (its buried objects alone) at a realistic local signal-to-noise, and every
visible point reflector is labelled at its apex, which the scene geometry gives exactly.

The tagged sites are test sites: no stretch of them is ever used here.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from detect.migration_picker import pick_targets
from simulate import instrument
from simulate.dataset import assign_splits, load_simulated_frames
from simulate.labels import _point_curve
from simulate.scenes import Scene
from studio import session

WINDOW = 128  # traces per training image: 3.2 m
BOX_TRACES, BOX_SAMPLES = 24, 32  # fixed apex-centred box: 0.6 m by 3.2 ns (1.5 wavelet periods)
EDGE = 16  # an apex this close to an image edge is half cut off: such crops are not used
LIMB_REACH = 40  # traces (1 m): a point outside the crop but this close still throws limbs into it
AREA_ALLOWANCE_M = 0.4  # diffraction off a slab or zone edge reaches past the object itself
SNR_RANGE = (4.0, 12.0)  # peak of the pasted echo over the real background's spread at that depth;
# clearly visible only, because the backgrounds still hold real arches weaker than STRONG_PICK
VISIBLE_SNR = 2.0  # a weaker pasted object is not visible, so it is not labelled
ECHO_START = 40  # samples: below the direct wave (peak at sample ~20)
PICK_CLEARANCE = 24  # traces either side of a picker target that a background must avoid
STRONG_PICK = 5.0  # picker strength (migrated envelope over its median) that makes a stretch not empty;
# measured 2026-10-09 on the 38 training lines: pick strengths median 4.5, 90th percentile 8.2, and
# only 11 stretches have no pick above 4, 62 none above 5
TEST_SITES = frozenset({"Set3", "Set4", "Set5"})  # where the operator tags are
VAL_SITE = "Set2"  # training sites are split by site too, never by image


def to_image(traces: np.ndarray) -> np.ndarray:
    """(n_traces, n_samples) raw traces -> (n_samples, n_traces) uint8: the detector's only view.

    Background removal (median trace), then each time sample scaled by its own robust spread across
    traces, so deep echoes show as clearly as shallow ones. Training and inference both call this.
    """
    x = traces - np.median(traces, axis=0, keepdims=True)
    spread = 1.4826 * np.median(np.abs(x), axis=0, keepdims=True) + 1e-12
    return np.round((np.clip(x / spread / 4.0, -1.0, 1.0).T + 1.0) * 127.5).astype(np.uint8)


def site_of(job: str) -> str:
    return job.split("_", 1)[0]


@dataclass(frozen=True)
class Background:
    site: str
    job: str
    start: int
    traces: np.ndarray  # (WINDOW, n_samples), raw


def empty_backgrounds(dataset_dir: Path, sites: set[str]) -> list[Background]:
    """Stretches of RAD lines from `sites` with no migration-picker target in or near them.

    ponytail: "empty" means no arch above STRONG_PICK; weaker real arches stay in and are learned as
    background (label noise). Verified rejections from the Studio's review are the clean upgrade.
    """
    found: list[Background] = []
    for job_dir in sorted(p for p in dataset_dir.iterdir() if p.is_dir() and site_of(p.name) in sites):
        try:
            rad = [c for c in session.available_channels(job_dir) if c.extension.upper() == "RAD"]
        except session.JobNotFoundError:
            continue  # an unpacked archive folder, not a survey line
        if not rad or not np.isclose(rad[0].sample_interval_ns, instrument.SAMPLE_INTERVAL_NS):
            continue
        frame = session.load_frame(job_dir, rad[0].extension)
        traces = session.raw_traces(frame)
        if traces.shape[1] != instrument.SAMPLES_PER_TRACE or traces.shape[0] < WINDOW:
            continue
        busy = np.zeros(traces.shape[0], dtype=bool)
        for pick in pick_targets(traces, sample_interval_ns=rad[0].sample_interval_ns,
                                 trace_spacing_m=rad[0].trace_spacing_m,
                                 dielectric=session.usable_dielectric(frame) or 9.0, max_picks=100):
            if pick.strength >= STRONG_PICK:
                busy[max(0, pick.trace - PICK_CLEARANCE): pick.trace + PICK_CLEARANCE + 1] = True
        for start in range(0, traces.shape[0] - WINDOW + 1, WINDOW // 4):
            if not busy[start:start + WINDOW].any():
                found.append(Background(site_of(job_dir.name), job_dir.name, start, traces[start:start + WINDOW]))
    return found


@dataclass(frozen=True)
class Patch:
    scene_id: str
    start: int
    field: np.ndarray  # (WINDOW, n_samples): the scattered field, objects only
    apexes: tuple[tuple[int, int], ...]  # (trace within the crop, sample) of every point reflector in it


def _apex(scene: Scene, obj) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    curve = _point_curve(scene, obj, instrument.TRACES_PER_FRAME)
    trace = int(np.argmin(curve))
    return trace, round(float(curve[trace]))


def scene_patches(scene: Scene, scattered: np.ndarray, rng: np.random.Generator) -> Iterator[Patch]:
    """Crops of one simulated scene that hold only point reflectors, each apex well inside."""
    n_traces = scattered.shape[1]
    spacing = instrument.TRACE_SPACING_M
    points = [_apex(scene, obj) for obj in (*scene.pipes, *scene.stones)]
    areas = [(a.x_start_m, a.x_end_m) for a in scene.slabs] + [(a.x_start_m, a.x_end_m) for a in scene.zones] \
        + [(a.x_start_m, a.x_end_m) for a in scene.voids]
    for trace, sample in points:
        if not ECHO_START <= sample < instrument.SAMPLES_PER_TRACE - BOX_SAMPLES // 2:
            continue
        start = trace - int(rng.integers(EDGE, WINDOW - EDGE))
        if start < 0 or start + WINDOW > n_traces:
            continue
        x0, x1 = start * spacing - AREA_ALLOWANCE_M, (start + WINDOW) * spacing + AREA_ALLOWANCE_M
        if any(a0 < x1 and a1 > x0 for a0, a1 in areas):
            continue  # an area's echo would be in the picture with no label
        inside, clean = [], True
        for t, s in points:
            local = t - start
            if EDGE <= local < WINDOW - EDGE:
                inside.append((local, s))
            elif -LIMB_REACH <= local < WINDOW + LIMB_REACH:
                clean = False  # cut off, or its limbs reach in: neither labellable nor absent
        if clean and inside:
            yield Patch(scene.scene_id, start, scattered[:, start:start + WINDOW].T.copy(), tuple(inside))


def composite(background: Background, patch: Patch, snr: float) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Background plus the patch scaled so its strongest apex stands `snr` spreads above the ground there.

    Returns the traces and the apexes still visible at that scale.
    """
    ground = background.traces - np.median(background.traces, axis=0, keepdims=True)
    spread = 1.4826 * np.median(np.abs(ground), axis=0) + 1e-12  # per sample, across traces

    def peak(apex: tuple[int, int]) -> float:
        t, s = apex
        return float(np.abs(patch.field[max(0, t - 4): t + 5, max(0, s - 10): s + 11]).max())

    strongest = max(patch.apexes, key=lambda a: peak(a) / spread[a[1]])
    scale = snr * spread[strongest[1]] / max(peak(strongest), 1e-30)
    visible = [a for a in patch.apexes if scale * peak(a) / spread[a[1]] >= VISIBLE_SNR]
    return background.traces + scale * patch.field, visible


DIRECT_WAVE_SAMPLE = instrument.DIRECT_WAVE_PEAK_SAMPLE  # time zero on the RAD grid
EPS_RANGE = (4.0, 16.0)  # soil permittivities to draw from (sand to wet clay; Set 1 measured 6.3)
ANALYTIC_FRACTION = 0.85  # the gprMax scenes give only a handful of clean point targets


def direct_wavelets(grounds: list[Background]) -> list[np.ndarray]:
    """This radar's own pulse: the direct wave of each background stretch, peak normalised to 1."""
    found = []
    for g in grounds[:: max(1, len(grounds) // 30)]:
        mean = np.median(g.traces, axis=0)
        peak = 10 + int(np.argmax(np.abs(mean[10:36])))
        w = mean[peak - 8: peak + 15]
        found.append(w / np.abs(w).max())
    return found


def ricker(rng: np.random.Generator) -> np.ndarray:
    """A textbook pulse near the antenna's measured centre frequency, for variety."""
    f = instrument.CENTRE_FREQUENCY_MHZ * 1e-3 * float(rng.uniform(0.8, 1.25))  # GHz = cycles per ns
    t = (np.arange(23) - 8) * instrument.SAMPLE_INTERVAL_NS
    w = (1 - 2 * (np.pi * f * t) ** 2) * np.exp(-((np.pi * f * t) ** 2))
    return w / np.abs(w).max()


def analytic_patch(rng: np.random.Generator, wavelets: list[np.ndarray]) -> Patch:
    """1-3 point reflectors drawn from the hyperbola geometry: t(x) = 2 sqrt(d^2 + (x - x0)^2) / v.

    Amplitude falls along the limbs with spreading and the antenna's beam (d/r)^(1+n). Either
    polarity: an echo reverses for a target denser than the soil (metal, water) and not for a
    lighter one (air). Not full-wave physics: a way to show the detector many apexes on real ground.
    """
    v = 0.2998 / np.sqrt(rng.uniform(*EPS_RANGE))
    dt, spacing = instrument.SAMPLE_INTERVAL_NS, instrument.TRACE_SPACING_M
    field = np.zeros((WINDOW, instrument.SAMPLES_PER_TRACE))
    grid = np.arange(instrument.SAMPLES_PER_TRACE)
    apexes = []
    for _ in range(int(rng.integers(1, 4))):
        x0 = int(rng.integers(EDGE, WINDOW - EDGE))
        apex = int(rng.integers(ECHO_START, instrument.SAMPLES_PER_TRACE - BOX_SAMPLES // 2 - 10))
        depth = (apex - DIRECT_WAVE_SAMPLE) * dt * v / 2
        wavelet = wavelets[int(rng.integers(len(wavelets)))] if rng.random() < 0.5 else ricker(rng)
        wavelet = wavelet * (1 if rng.random() < 0.5 else -1) * rng.uniform(0.4, 1.0)
        centre = int(np.argmax(np.abs(wavelet)))
        beam = rng.uniform(1.0, 4.0)
        for trace in range(WINDOW):
            r = np.hypot(depth, (trace - x0) * spacing)
            gain = (depth / r) ** (1 + beam)
            if gain < 0.02:
                continue
            at = DIRECT_WAVE_SAMPLE + 2 * r / v / dt
            field[trace] += gain * np.interp(grid - at, np.arange(len(wavelet)) - centre, wavelet, left=0, right=0)
        apexes.append((x0, apex))
    return Patch("analytic", 0, field, tuple(apexes))


def yolo_line(apex: tuple[int, int], n_traces: int, n_samples: int) -> str:
    t, s = apex
    return f"0 {t / n_traces:.6f} {s / n_samples:.6f} {BOX_TRACES / n_traces:.6f} {BOX_SAMPLES / n_samples:.6f}"


def build(dataset_dir: Path, runs_dir: Path, out_dir: Path, *, images: int, empty_fraction: float = 0.25,
          seed: int = 0) -> dict:
    """Write a YOLO dataset of composites: train on the training sites, validate on VAL_SITE."""
    rng = np.random.default_rng(seed)
    frames, _skipped = load_simulated_frames(runs_dir)
    scene_split = assign_splits([s.scene_id for s, _ in frames], 0.2)
    patches = {split: [p for s, f in frames if scene_split[s.scene_id] == split
                       for p in scene_patches(s, f.scattered, rng)] for split in ("train", "val")}
    sites = {site_of(p.name) for p in dataset_dir.iterdir() if p.is_dir()} - TEST_SITES
    grounds = empty_backgrounds(dataset_dir, sites)
    by_split = {"train": [g for g in grounds if g.site != VAL_SITE], "val": [g for g in grounds if g.site == VAL_SITE]}
    wavelets = {split: direct_wavelets(gs) for split, gs in by_split.items()}
    counts = {}
    manifest = []
    for split, n in (("train", images), ("val", max(1, images // 5))):
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)
        if not patches[split] or not by_split[split]:
            raise ValueError(f"nothing to build the {split} split from")
        labelled = 0
        for k in range(n):
            ground = by_split[split][int(rng.integers(len(by_split[split])))]
            apexes: list[tuple[int, int]] = []
            traces = ground.traces
            if rng.random() >= empty_fraction:
                patch = (analytic_patch(rng, wavelets[split]) if rng.random() < ANALYTIC_FRACTION
                         else patches[split][int(rng.integers(len(patches[split])))])
                traces, apexes = composite(ground, patch, float(rng.uniform(*SNR_RANGE)))
                if rng.random() < 0.5:  # the line could have been walked either way
                    traces = traces[::-1]
                    apexes = [(WINDOW - 1 - t, s) for t, s in apexes]
            name = f"{split}_{k:05d}"
            cv2.imwrite(str(out_dir / "images" / split / f"{name}.png"), to_image(traces))
            (out_dir / "labels" / split / f"{name}.txt").write_text(
                "".join(yolo_line(a, WINDOW, traces.shape[1]) + "\n" for a in apexes))
            labelled += len(apexes)
            manifest.append({"image": name, "split": split, "ground": f"{ground.job}@{ground.start}",
                             "apexes": apexes})
        counts[split] = {"images": n, "apexes": labelled}
    (out_dir / "data.yaml").write_text(f"path: {out_dir.resolve()}\ntrain: images/train\nval: images/val\nnames:\n  0: apex\n")
    (out_dir / "manifest.jsonl").write_text("\n".join(json.dumps(m) for m in manifest) + "\n")
    summary = {"patches": {k: len(v) for k, v in patches.items()},
               "backgrounds": {k: len(v) for k, v in by_split.items()}, "counts": counts,
               "test_sites_excluded": sorted(TEST_SITES), "val_site": VAL_SITE}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
