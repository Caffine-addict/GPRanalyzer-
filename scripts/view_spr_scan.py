#!/usr/bin/env python3
"""Render a real, axis-labelled view of one SPR survey (all 3 channels + GPS path).

This is a manual inspection tool, not part of the detection pipeline — no
ScanSource/orchestrator involved, just parsers.spr directly. It exists
because render/bscan.py's traces_to_image() is deliberately a crude
min-max placeholder with no axes at all (see its own docstring); this adds
real distance/depth axes and shows all 3 channels together.

Axis honesty, matching this project's confidence-labelling discipline:
- Distance (x) axis is trustworthy: SPR_SHAFT_INTERVAL is a documented
  wheel-encoder distance per trace, not inferred.
- Depth (y) axis is NOT trustworthy: it depends on SPR_SAMPLING_INTERVAL's
  unit, which is an unconfirmed assumption (see parsers/spr.py, docs/
  COMPANY_QUESTIONS.md #2). Labelled as such directly on the image so it
  can't be mistaken for a calibrated reading.

Usage:
    .venv/bin/python scripts/view_spr_scan.py Dataset/DSU_GPR_Files/Job_0703
"""

from __future__ import annotations

import csv
import math
import sys
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

import parsers.spr  # noqa: F401 - registers the "rad"/"ra1"/"ra2" parsers as a side effect
from core.contracts import ScanFrame
from parsers.base import get_parser

_SPEED_OF_LIGHT_M_PER_NS = 0.2998
_CHANNEL_HEIGHT_PX = 480
_HEADER_STRIP_PX = 18
_PANEL_WIDTH_PX = 1600
_MARGIN_PX = 60
_GPS_PANEL_SIZE_PX = 320


def _load_channel(path: Path) -> ScanFrame:
    return get_parser(path)(path)


def _render_bscan_panel(frame: ScanFrame, label: str) -> np.ndarray:
    traces = frame.traces
    assert traces is not None

    finite = traces[np.isfinite(traces)]
    lo, hi = np.percentile(finite, [2, 98])
    scaled = np.clip((traces.astype(np.float64) - lo) / max(hi - lo, 1e-9), 0.0, 1.0)
    gray_native = (scaled * 255).astype(np.uint8).T  # depth (rows) x traces (cols), native resolution

    n_samples, n_traces = gray_native.shape
    # No CLAHE/bilateral here deliberately — this stays a plain linear
    # rescale of the real amplitude values (2nd/98th percentile clip for
    # visible range only), no smoothing or contrast redistribution that
    # would change what's shown relative to the raw signal. Resize uses
    # nearest-neighbour, not linear/cubic, so no interpolated (invented)
    # pixel values are introduced either — every displayed pixel is a real
    # sample, just drawn bigger, not blended with its neighbours.
    plot_w = _PANEL_WIDTH_PX - _MARGIN_PX
    plot_h = _CHANNEL_HEIGHT_PX
    resized = cv2.resize(gray_native, (plot_w, plot_h), interpolation=cv2.INTER_NEAREST)
    bgr = cv2.cvtColor(resized, cv2.COLOR_GRAY2BGR)

    total_h = _HEADER_STRIP_PX + plot_h
    canvas = np.full((total_h, _PANEL_WIDTH_PX, 3), 30, dtype=np.uint8)
    canvas[_HEADER_STRIP_PX:, _MARGIN_PX:] = bgr

    shaft_interval_m = float(frame.provenance["raw_header"].get("SPR_SHAFT_INTERVAL", "nan"))
    distance_m = n_traces * shaft_interval_m

    velocity_m_per_ns = None
    max_depth_m = None
    if frame.dielectric_assumed:
        velocity_m_per_ns = _SPEED_OF_LIGHT_M_PER_NS / math.sqrt(frame.dielectric_assumed)
    if velocity_m_per_ns and frame.sample_interval_ns:
        two_way_ns = n_samples * frame.sample_interval_ns
        max_depth_m = (two_way_ns * velocity_m_per_ns) / 2.0

    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = _HEADER_STRIP_PX + int(frac * (plot_h - 1))
        depth_label = f"{frac * max_depth_m:.2f}m" if max_depth_m else "?"
        cv2.putText(canvas, depth_label, (2, y + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.32, (200, 200, 200), 1)

    caveat = "depth: UNCONFIRMED unit (see COMPANY_QUESTIONS.md #2)" if max_depth_m else "depth: unavailable"
    header_txt = f"{label}  |  distance 0-{distance_m:.2f}m (real, shaft-encoder)  |  {caveat}"
    cv2.putText(canvas, header_txt, (_MARGIN_PX + 4, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

    return canvas


def _parse_gps(path: Path) -> list[tuple[float, float]]:
    if not path.exists():
        return []
    points = []
    with path.open(newline="") as fh:
        for row in csv.reader(fh):
            if len(row) < 4:
                continue
            try:
                lat, lon = float(row[2]), float(row[3])
            except ValueError:
                continue
            points.append((lat, lon))
    return points


def _render_gps_panel(points: list[tuple[float, float]]) -> np.ndarray:
    canvas = np.full((_GPS_PANEL_SIZE_PX, _GPS_PANEL_SIZE_PX, 3), 30, dtype=np.uint8)
    cv2.putText(canvas, "GPS path (real fixes, coarse)", (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    if len(points) < 2:
        cv2.putText(canvas, "no GPS data", (6, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 150, 150), 1)
        return canvas

    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    lat_range = max(max(lats) - min(lats), 1e-9)
    lon_range = max(max(lons) - min(lons), 1e-9)
    pad = 30
    plot_size = _GPS_PANEL_SIZE_PX - 2 * pad

    def to_px(lat: float, lon: float) -> tuple[int, int]:
        x = pad + int((lon - min(lons)) / lon_range * plot_size)
        y = pad + int((1 - (lat - min(lats)) / lat_range) * plot_size)
        return x, y

    for a, b in pairwise(points):
        cv2.line(canvas, to_px(*a), to_px(*b), (0, 200, 0), 1)
    cv2.circle(canvas, to_px(*points[0]), 4, (0, 255, 255), -1)
    cv2.circle(canvas, to_px(*points[-1]), 4, (0, 0, 255), -1)
    return canvas


_CHANNEL_ORDER = (("RAD", "Channel 0 (100ps, shallow)"), ("RA1", "Channel 1 (200ps, mid)"), ("RA2", "Channel 2 (400ps, deep)"))


def panel_geometry(job_dir: Path) -> dict:
    """Describe exactly where each channel's real (trace, sample) data sits
    in the combined canvas render_job_image() produces — the single source
    of truth for converting a click on the rendered image back into native
    data coordinates, so annotation tooling never has to duplicate the
    layout constants used for drawing.
    """
    channels = []
    panel_h = _HEADER_STRIP_PX + _CHANNEL_HEIGHT_PX
    plot_w = _PANEL_WIDTH_PX - _MARGIN_PX
    for i, (ext, name) in enumerate(_CHANNEL_ORDER):
        candidate = job_dir / f"Single-01.{ext}"
        if not candidate.exists():
            continue
        frame = _load_channel(candidate)
        assert frame.traces is not None
        n_traces, n_samples = frame.traces.shape
        channels.append(
            {
                "extension": ext,
                "label": name,
                "n_traces": n_traces,
                "n_samples": n_samples,
                # bounding box of this channel's real data within the full canvas, in canvas pixels
                "plot_x0": _MARGIN_PX,
                "plot_y0": i * panel_h + _HEADER_STRIP_PX,
                "plot_w": plot_w,
                "plot_h": _CHANNEL_HEIGHT_PX,
            }
        )
    canvas_w = _PANEL_WIDTH_PX + _GPS_PANEL_SIZE_PX
    canvas_h = len(channels) * panel_h
    return {"canvas_w": canvas_w, "canvas_h": canvas_h, "channels": channels}


def render_job_image(job_dir: Path) -> np.ndarray:
    """Build the combined multi-channel + GPS view as a BGR array. Raises
    FileNotFoundError if job_dir has none of the expected channel files."""
    panels = []
    for ext, name in _CHANNEL_ORDER:
        candidate = job_dir / f"Single-01.{ext}"
        if not candidate.exists():
            continue
        frame = _load_channel(candidate)
        panels.append(_render_bscan_panel(frame, name))

    if not panels:
        raise FileNotFoundError(f"no SPR channel files (RAD/RA1/RA2) found in {job_dir}")

    gps_points = _parse_gps(job_dir / "Single-01.gps")
    gps_panel = _render_gps_panel(gps_points)

    bscan_stack = np.vstack(panels)
    side_panel = np.full((bscan_stack.shape[0], _GPS_PANEL_SIZE_PX, 3), 30, dtype=np.uint8)
    side_panel[: gps_panel.shape[0], :] = gps_panel

    combined = np.hstack([bscan_stack, side_panel])
    cv2.putText(combined, f"job: {job_dir.name}", (4, combined.shape[0] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    return combined


def render_job(job_dir: Path, output_path: Path) -> None:
    combined = render_job_image(job_dir)
    cv2.imwrite(str(output_path), combined)
    print(f"wrote {output_path} ({combined.shape[1]}x{combined.shape[0]})")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print(f"usage: {sys.argv[0]} <job_directory> [output_path]", file=sys.stderr)
        raise SystemExit(1)
    job_directory = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) == 3 else Path("output") / f"{job_directory.name}_view.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    render_job(job_directory, out_path)
