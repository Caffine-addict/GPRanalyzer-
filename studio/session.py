"""Job discovery, channel loading, and the axis metadata every view needs.

Sits between `parsers/spr.py` and everything in the Studio that wants data.
Two jobs:

**Cache parses.** A survey line is ~386 x 256 samples and parses in about a
millisecond, but the viewer re-runs the processing chain on every parameter
change; re-reading the file each time would be waste for no gain. Frames are
cached by (path, mtime), so a file replaced on disk is picked up rather than
served stale.

**State the axes honestly, once.** Both axes come with a provenance string
rather than being handed over as bare numbers, because they are not equally
trustworthy and the difference is not visible from the values:

- Distance is *measured* — `SPR_SHAFT_INTERVAL` is a wheel-encoder reading,
  0.025 m per trace.
- Depth is *inferred twice over* — it needs `SPR_SAMPLING_INTERVAL` to be in
  picoseconds (an inference, `parsers/spr.py`) and the header's operator-set
  `SPR_MEDIUM_DIELECTRIC` to match the actual ground.

Anything rendering a depth axis is expected to carry that caveat through to
what the operator sees. `studio/velocity.py` is the way out of the second
assumption; nothing yet resolves the first except the company confirming it.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from math import sqrt
from pathlib import Path

import numpy as np

import parsers.spr  # noqa: F401 - registers the rad/ra1/ra2 parsers as an import side effect
from core.contracts import ScanFrame
from parsers.base import get_parser
from studio.processing import SPEED_OF_LIGHT_M_PER_NS

DEFAULT_DATASET_DIR = Path("Dataset/DSU_GPR_Files")

# The three receiver channels an SPRScan 3D writes per line, shallow to deep.
# Ordering is the sampling interval, which is the physical difference between
# them: a longer interval buys a deeper time window at coarser resolution.
CHANNEL_ORDER: tuple[tuple[str, str], ...] = (
    ("RAD", "Ch 0 — shallow"),
    ("RA1", "Ch 1 — mid"),
    ("RA2", "Ch 2 — deep"),
)
_CHANNEL_LABELS = dict(CHANNEL_ORDER)

DISTANCE_PROVENANCE = "measured (wheel encoder, SPR_SHAFT_INTERVAL)"
DEPTH_PROVENANCE = (
    "inferred — assumes SPR_SAMPLING_INTERVAL is in picoseconds (unconfirmed) "
    "and the header's SPR_MEDIUM_DIELECTRIC matches the ground"
)


class JobNotFoundError(FileNotFoundError):
    """No such job folder, or it holds no SPR channel files."""


@dataclass(frozen=True)
class ChannelInfo:
    """One channel's shape and axis calibration, ready to hand to a client."""

    extension: str
    label: str
    n_traces: int
    n_samples: int
    trace_spacing_m: float
    sample_interval_ns: float
    dielectric_assumed: float | None
    line_length_m: float
    time_window_ns: float
    max_depth_m: float | None  # None when no dielectric is recorded — never a guessed default


def list_jobs(dataset_dir: Path = DEFAULT_DATASET_DIR) -> list[str]:
    """Job folder names holding at least one SPR channel file, alphabetically.

    Re-read on every call rather than cached: a folder dropped into the dataset
    while the server runs should just appear.
    """
    if not dataset_dir.exists():
        return []
    return sorted(
        entry.name
        for entry in dataset_dir.iterdir()
        if entry.is_dir() and any((entry / f"Single-01.{ext}").exists() for ext, _ in CHANNEL_ORDER)
    )


def resolve_job(job_name: str, dataset_dir: Path = DEFAULT_DATASET_DIR) -> Path:
    """Map a job name to its folder, rejecting anything not actually in the dataset.

    Membership is checked against the discovered list, not by joining the path
    — that makes traversal (`../..`) impossible by construction rather than by
    a filter someone has to remember to keep correct.
    """
    if job_name not in list_jobs(dataset_dir):
        raise JobNotFoundError(f"unknown job: {job_name!r}")
    return dataset_dir / job_name


def channel_path(job_dir: Path, extension: str) -> Path:
    if extension not in _CHANNEL_LABELS:
        raise ValueError(f"unknown channel {extension!r} (have {sorted(_CHANNEL_LABELS)})")
    path = job_dir / f"Single-01.{extension}"
    if not path.exists():
        raise JobNotFoundError(f"job {job_dir.name} has no {extension} channel")
    return path


@lru_cache(maxsize=32)
def _parse_cached(path_str: str, _mtime_ns: int) -> ScanFrame:
    """Cached parse. `_mtime_ns` is part of the key so a rewritten file re-parses."""
    path = Path(path_str)
    return get_parser(path)(path)


def raw_traces(frame: ScanFrame) -> np.ndarray:
    """A frame's traces in source orientation (n_traces, n_samples).

    `ScanFrame.traces` is optional because image-only sources exist; every
    Studio view needs real samples, so this is where that requirement is
    enforced — once, rather than as scattered None checks.
    """
    if frame.traces is None:
        raise ValueError(f"{frame.source_type} frame has no traces — nothing to display")
    return frame.traces


def load_frame(job_dir: Path, extension: str) -> ScanFrame:
    """The parsed channel. Raises if it came back without traces."""
    path = channel_path(job_dir, extension)
    frame = _parse_cached(str(path), path.stat().st_mtime_ns)
    raw_traces(frame)
    return frame


def load_radargram(job_dir: Path, extension: str) -> tuple[np.ndarray, ChannelInfo]:
    """The channel as a radargram: (n_samples, n_traces) plus its axis metadata.

    Transposing here, once, is what lets everything downstream in the Studio
    assume the orientation a radargram is actually read in — rows are time,
    columns are distance.
    """
    frame = load_frame(job_dir, extension)
    return raw_traces(frame).T, describe_channel(frame, extension)


def _trace_spacing_m(frame: ScanFrame) -> float:
    raw = frame.provenance.get("raw_header", {}).get("SPR_SHAFT_INTERVAL")
    try:
        spacing = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"channel has no usable SPR_SHAFT_INTERVAL (got {raw!r})") from exc
    if spacing <= 0:
        raise ValueError(f"SPR_SHAFT_INTERVAL must be positive, got {spacing}")
    return spacing


def describe_channel(frame: ScanFrame, extension: str) -> ChannelInfo:
    """Shape and axis calibration for one parsed channel."""
    n_traces, n_samples = raw_traces(frame).shape
    spacing = _trace_spacing_m(frame)
    if not frame.sample_interval_ns:
        raise ValueError(f"channel {extension} has no sample interval — no time axis is definable")

    time_window_ns = n_samples * frame.sample_interval_ns
    max_depth_m = None
    if frame.dielectric_assumed:
        velocity = SPEED_OF_LIGHT_M_PER_NS / sqrt(frame.dielectric_assumed)
        max_depth_m = velocity * time_window_ns / 2.0

    return ChannelInfo(
        extension=extension,
        label=_CHANNEL_LABELS[extension],
        n_traces=n_traces,
        n_samples=n_samples,
        trace_spacing_m=spacing,
        sample_interval_ns=frame.sample_interval_ns,
        dielectric_assumed=frame.dielectric_assumed,
        line_length_m=n_traces * spacing,
        time_window_ns=time_window_ns,
        max_depth_m=max_depth_m,
    )


def available_channels(job_dir: Path) -> list[ChannelInfo]:
    """Every channel present for a job, shallow to deep."""
    infos = []
    for extension, _ in CHANNEL_ORDER:
        if not (job_dir / f"Single-01.{extension}").exists():
            continue
        infos.append(describe_channel(load_frame(job_dir, extension), extension))
    if not infos:
        raise JobNotFoundError(f"no SPR channel files in {job_dir}")
    return infos


def load_gps_track(job_dir: Path) -> list[dict[str, float]]:
    """Per-trace GPS fixes for the line, as {lat, lon}.

    Coarser than the 0.025 m trace spacing, so this places the *line* on the
    ground; it is not a per-trace position. Rows that don't parse are skipped
    rather than defaulted — a dropped fix is a real thing in the field, and
    inventing a coordinate for it would put a target in the wrong place.
    """
    path = job_dir / "Single-01.gps"
    if not path.exists():
        return []
    points = []
    with path.open(newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 4:
                continue
            try:
                points.append({"lat": float(row[2]), "lon": float(row[3])})
            except ValueError:
                continue
    return points


def job_header(job_dir: Path) -> dict[str, str]:
    """The acquisition header, for the job-info panel. Taken from the shallow channel."""
    for extension, _ in CHANNEL_ORDER:
        if (job_dir / f"Single-01.{extension}").exists():
            return dict(load_frame(job_dir, extension).provenance.get("raw_header", {}))
    raise JobNotFoundError(f"no SPR channel files in {job_dir}")
