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

from core import boxes as box_store
from detect.classical import find_candidate_boxes
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
