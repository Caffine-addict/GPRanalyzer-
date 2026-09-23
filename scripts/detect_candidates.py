#!/usr/bin/env python3
"""Phase-1 candidate localization: find visible reflector patterns and box them — no class.

This is deliberately NOT a trained detector (no weights exist — see
detect/model.py's ModelNotFoundError). It's a classical signal-processing
pass that a human would otherwise do by eye in the viewer: subtract the
mean trace (standard GPR background removal — removes the horizontal
direct-wave/ringing band, which is survey-invariant, not a discrete
target), compute a smoothed energy envelope of the residual (raw |residual|
oscillates with the wavelet cycle and is unusable directly — see the
envelope construction below), and threshold it. Every parameter here was
tuned by rendering the candidate boxes on top of the real B-scan and
visually confirming alignment with real hyperbolas before trusting it
across jobs — not guessed.

Still phase 1: boxes carry no class field, matching core/boxes.py's
Box dataclass. What they ARE is buried under is company ground truth, not
this script's job to decide.

Usage:
    .venv/bin/python scripts/detect_candidates.py Dataset/DSU_GPR_Files/Job_0703
    .venv/bin/python scripts/detect_candidates.py --all   # every job under Dataset/DSU_GPR_Files
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

from core import boxes as box_store
from detect.measure import direct_wave_skip_samples
from studio.session import CHANNEL_ORDER as _CHANNEL_ORDER
from studio.session import load_frame as _load_channel_by_job

# The detector's stable identity. Replacement in core/boxes.py keys on this name, NOT on the
# version below, so changing the detection logic still replaces this detector's own earlier
# output instead of accumulating beside it. Change the name only if this becomes a genuinely
# different detector that should coexist with this one.
DETECTOR_NAME = "energy-envelope-candidates"


def detector_version() -> str:
    """What this detector was when it ran, for provenance on the boxes it stores.

    Returns the repo's short commit hash, suffixed `-dirty` when the working tree has edits
    (so a box traced back to `3d7bb2d-dirty` is known not to be reproducible from that commit
    alone), or `"unknown"` when git is absent, this is not a repo, the command fails, or it
    does not answer promptly. Never used to decide what gets replaced; see DETECTOR_NAME.
    """
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, timeout=5,
            cwd=Path(__file__).resolve().parent.parent,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, check=True, timeout=5,
            cwd=Path(__file__).resolve().parent.parent,
        ).stdout.strip()
    # A hung git (index lock, credential prompt) must not stall the pipeline for a field that
    # is provenance only — time out and record that we do not know.
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "unknown"
    return f"{head}-dirty" if dirty else head


_ENVELOPE_BLUR_KSIZE = (3, 17)  # (trace, sample) -- collapses the ~6-sample wavelet cycle
_ROW_BACKGROUND_FLOOR_FRACTION = 0.15  # guards depth-wise normalization against near-zero background rows
_THRESHOLD_PERCENTILE = 82.0
_CLOSE_KERNEL = (9, 5)  # (sample, trace) morphological closing to merge split fragments
_OPEN_KERNEL = (3, 3)  # strips thin single-trace noise streaks
_MIN_BOX_W, _MIN_BOX_H, _MIN_BOX_AREA = 8, 5, 150
_MAX_ASPECT_RATIO = 4.0  # combined with _NOISE_STREAK_MAX_WIDTH below -- see find_candidate_boxes
_NOISE_STREAK_MAX_WIDTH = 15  # a real point reflector's tail can be tall+narrow too (aspect > 4 alone
# isn't noise-specific -- confirmed by a real miss: a genuine strong reflector at trace 311-337 got
# discarded by aspect ratio alone before this width co-condition was added). True single/few-trace
# noise streaks measured earlier topped out around width 10-13; 15 leaves margin without also
# catching wide real features.


def find_candidate_boxes(traces: np.ndarray, sample_interval_ns: float) -> list[tuple[int, int, int, int]]:
    """Return candidate (trace, sample, width, height) boxes for one channel's raw traces.

    Depth-wise (AGC-style) normalization is essential here, not optional:
    GPR signal attenuates with depth, so a flat energy threshold is
    systematically biased toward shallow near-surface clutter and misses
    real deeper reflectors entirely — confirmed by testing without it
    first (it caught noise, missed two clearly-visible hyperbolas) before
    adding this.
    """
    _n_traces, n_samples = traces.shape
    mean_trace = traces.astype(np.float64).mean(axis=0, keepdims=True)
    residual = (traces.astype(np.float64) - mean_trace).T  # (n_samples, n_traces)
    power = (residual**2).astype(np.float32)

    envelope = cv2.blur(power, _ENVELOPE_BLUR_KSIZE)
    envelope = np.sqrt(np.clip(envelope, 0, None))

    # A time (DIRECT_WAVE_WINDOW_NS), not a fraction of the record — RAD/RA1/RA2 sample at
    # 0.1/0.2/0.4 ns, so a fraction-based skip was blanking 0.15/0.31/0.61 m respectively on
    # the same "12%". Shared conversion with detect/measure.py, not just the shared constant.
    skip = direct_wave_skip_samples(n_samples, sample_interval_ns)
    envelope[:skip, :] = 0
    if not np.any(envelope > 0):
        return []

    row_background = np.median(envelope, axis=1, keepdims=True)
    floor = np.median(envelope[skip:]) * _ROW_BACKGROUND_FLOOR_FRACTION
    row_background = np.maximum(row_background, floor)
    normalized = (envelope / row_background).astype(np.float32)
    normalized[:skip, :] = 0

    nonzero = normalized[normalized > 0]
    norm_u8 = np.clip(normalized / np.percentile(nonzero, 99.5) * 255, 0, 255).astype(np.uint8)
    thresh_val = np.percentile(norm_u8[norm_u8 > 0], _THRESHOLD_PERCENTILE)
    _, mask = cv2.threshold(norm_u8, thresh_val, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones(_CLOSE_KERNEL, np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones(_OPEN_KERNEL, np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w < _MIN_BOX_W or h < _MIN_BOX_H or w * h < _MIN_BOX_AREA:
            continue
        if max(w, h) / min(w, h) > _MAX_ASPECT_RATIO and min(w, h) < _NOISE_STREAK_MAX_WIDTH:
            continue
        boxes.append((x, y, w, h))
    return boxes


def detect_and_store(job_dir: Path) -> int:
    """Run this detector over every channel of a job and store the result.

    **Replaces this detector's previous output rather than adding to it.** The earlier version
    appended any box whose rounded coordinates were not already present, which silently built a
    union across detector versions: 233 stored boxes against 166 the detector actually found,
    67 of them unreproducible and written before the direct-wave-skip fix. Every published
    number came from that union. `core.boxes.replace_detector_boxes` is where the rule lives;
    human-drawn boxes are never touched by it.
    """
    detected: list[box_store.DetectedBox] = []
    for ext, _label in _CHANNEL_ORDER:
        candidate = job_dir / f"Single-01.{ext}"
        if not candidate.exists():
            continue
        frame = _load_channel_by_job(job_dir, ext)
        assert frame.traces is not None
        assert frame.sample_interval_ns is not None
        detected.extend(
            box_store.DetectedBox(
                channel=ext, x=float(x), y=float(y), w=float(w), h=float(h),
                note="auto: background-removal + energy-envelope candidate, unclassified",
            )
            for x, y, w, h in find_candidate_boxes(frame.traces, frame.sample_interval_ns)
        )

    stored = box_store.replace_detector_boxes(
        job_dir.name,
        detector=DETECTOR_NAME,
        detector_version=detector_version(),
        boxes=detected,
    )
    return len(stored)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <job_directory | --all>", file=sys.stderr)
        raise SystemExit(1)

    if sys.argv[1] == "--all":
        base = Path("Dataset/DSU_GPR_Files")
        job_dirs = sorted(d for d in base.iterdir() if d.is_dir())
    else:
        job_dirs = [Path(sys.argv[1])]

    for jd in job_dirs:
        stored = detect_and_store(jd)
        print(f"{jd.name}: {stored} candidate boxes stored (replacing this detector's previous output)")
