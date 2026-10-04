"""Put a survey's lines on one map, and trace targets across them.

An experienced interpreter confirms a buried utility by following it from line to line: a pipe
crosses every parallel survey line at the same place and a steady depth, while a stone shows on
one line only. That needs the lines' real positions, which a GNSS receiver records per trace.

On the University of Twente surveys (docs/EXTERNAL_DATA.md), placing picks on the map and keeping
only those that line up on 3+ lines found 17.4% of the excavated utilities per line against 10.3%
for the same picks with scrambled positions: better on 22 surveys, worse on 4 of 39. Without
positions, nothing could be told apart from chance.

Positions come from GNSS (centimetres to a metre, lagging on short lines); distance along a line
comes from the measuring wheel (millimetres). So a line is placed as GNSS start + direction, with
the wheel saying how far along it each trace is.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

# GNSS lags the wheel on short lines, so the fitted scale is loose; the fit residual is what
# catches a track that is not a straight walk.
_SCALE_RANGE = (0.6, 1.4)
_MAX_RESIDUAL_M = 0.3


@dataclass(frozen=True)
class LinePlacement:
    start: np.ndarray  # (2,) map position at wheel distance 0
    direction: np.ndarray  # (2,) unit vector of travel

    def at(self, distance_m: np.ndarray | float) -> np.ndarray:
        """Map position(s) at wheel distance(s) along the line."""
        return self.start + np.multiply.outer(distance_m, self.direction)


def place_line(positions_m: np.ndarray, distances_m: np.ndarray) -> LinePlacement | None:
    """Fit position = start + distance * direction; None when the track is not a straight walk."""
    positions_m, distances_m = np.asarray(positions_m, float), np.asarray(distances_m, float)
    if len(positions_m) < 3 or len(positions_m) != len(distances_m) or np.ptp(distances_m) <= 0:
        return None
    design = np.c_[np.ones(len(distances_m)), distances_m]
    (start, step), *_ = np.linalg.lstsq(design, positions_m, rcond=None)
    scale = float(np.linalg.norm(step))
    residual = float(np.sqrt(np.mean(np.sum((design @ np.vstack([start, step]) - positions_m) ** 2, axis=1))))
    if not _SCALE_RANGE[0] < scale < _SCALE_RANGE[1] or residual >= _MAX_RESIDUAL_M:
        return None
    return LinePlacement(start=start, direction=step / scale)


def survey_axis(placements: Sequence[LinePlacement]) -> np.ndarray:
    """The survey's common line direction. Lines walked either way count the same (zigzag)."""
    if not placements:
        raise ValueError("no placed lines")
    reference = placements[0].direction
    aligned = [p.direction if p.direction @ reference >= 0 else -p.direction for p in placements]
    mean = np.mean(aligned, axis=0)
    return mean / np.linalg.norm(mean)


def along_axis(placement: LinePlacement, distances_m: np.ndarray | float, axis: np.ndarray) -> np.ndarray:
    """Where points on a line sit along the survey axis: the coordinate a crossing pipe shares."""
    return placement.at(distances_m) @ axis


def traced_across_lines(
    picks: Sequence[Sequence[tuple[float, float]]],
    *,
    min_lines: int,
    along_tolerance_m: float = 0.3,
    depth_tolerance_m: float = 0.15,
) -> list[list[bool]]:
    """For each line's (along-axis position, depth) picks: does it line up on `min_lines` lines?

    A line counts once, however many of its picks agree.
    ponytail: all-pairs comparison, O(picks^2); fine for a survey's few hundred picks.
    """
    keep = []
    for i, line in enumerate(picks):
        row = []
        for along, depth in line:
            agreeing = 1 + sum(
                any(abs(a - along) <= along_tolerance_m and abs(d - depth) <= depth_tolerance_m for a, d in other)
                for j, other in enumerate(picks) if j != i
            )
            row.append(agreeing >= min_lines)
        keep.append(row)
    return keep
