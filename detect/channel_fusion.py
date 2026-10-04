"""Merge one line's picks across its frequency channels into targets, corroborated ones first.

An SPRScan line is recorded on three channels at once (RAD/RA1/RA2: 0.1, 0.2, 0.4 ns sampling,
shallow to deep). A buried pipe echoes on every channel that reaches it, at the same position and
the same two-way time; clutter and noise rarely line up across channels. So a target seen on
several channels is more likely real, and should be looked at first.

Time must be measured from each channel's own time zero: the direct wave arrives at about 1.6, 4.2
and 6.4 ns on RAD, RA1 and RA2 (56 company lines, 2026-10-05). Measured from there, the same
target lines up across channels to a median 0.8 ns; by raw time it misses by 2 ns, and by sample
index by 5 ns.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from detect.migration_picker import Pick

POSITION_TOLERANCE_M = 0.3
TIME_TOLERANCE_NS = 1.5
_TIME_ZERO_SPREAD_NS = 1.0
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelPicks:
    picks: Sequence[Pick]  # strongest first, as pick_targets returns them
    sample_interval_ns: float
    time_zero_ns: float


@dataclass(frozen=True)
class Target:
    trace: float
    time_ns: float  # from time zero
    channels: tuple[str, ...]  # every channel that saw it, shallow to deep
    members: tuple[tuple[str, Pick], ...]
    score: float  # summed strength over its channels


def time_zero_ns(traces: np.ndarray, sample_interval_ns: float) -> float:
    """When the direct wave arrives: the median over traces of each one's strongest early lobe.

    Per-trace, not on the mean trace: the direct wave is in every trace at the same time, while a
    strong shallow target only dominates the few traces above it, so the median ignores it. A wide
    spread means no consistent direct wave was found; it is logged rather than trusted silently.
    """
    data = np.abs(np.asarray(traces, dtype=np.float64))
    early = data[:, : max(1, data.shape[1] // 4)]
    arrivals = np.argmax(early, axis=1) * sample_interval_ns
    q1, q3 = np.percentile(arrivals, [25, 75])
    if q3 - q1 > _TIME_ZERO_SPREAD_NS:
        logger.warning("time zero uncertain: direct-wave arrivals spread %.1f ns across traces", q3 - q1)
    return float(np.median(arrivals))


def fuse(channels: Mapping[str, ChannelPicks], trace_spacing_m: float) -> list[Target]:
    """Group picks that sit at one position and time across channels; most-corroborated first.

    Greedy, strongest pick first: a pick joins the nearest existing target that has no pick from its
    channel yet and whose every member lies within tolerance of it (no chaining through one member
    to reach another); otherwise it starts a new one. Ties in channel count rank by summed strength.
    """
    tol_traces = POSITION_TOLERANCE_M / trace_spacing_m
    groups: list[dict] = []
    order = sorted(((p.strength, name, p) for name, ch in channels.items() for p in ch.picks), key=lambda x: -x[0])
    for _, name, pick in order:
        ch = channels[name]
        t = pick.sample * ch.sample_interval_ns - ch.time_zero_ns
        best = None
        for g in groups:
            if name in g["channels"]:
                continue
            gaps = [(abs(x - pick.trace), abs(mt - t)) for x, mt in g["points"]]
            if all(dx <= tol_traces and dt <= TIME_TOLERANCE_NS for dx, dt in gaps):
                cost = max(dx + dt for dx, dt in gaps)
                if best is None or cost < best[0]:
                    best = (cost, g)
        if best is None:
            groups.append({"points": [(float(pick.trace), t)], "channels": {name}, "members": [(name, pick)]})
        else:
            g = best[1]
            g["channels"].add(name)
            g["members"].append((name, pick))
            g["points"].append((float(pick.trace), t))
    names = list(channels)
    targets = [
        Target(trace=float(np.mean([p.trace for _, p in g["members"]])),
               time_ns=float(np.mean([p.sample * channels[n].sample_interval_ns - channels[n].time_zero_ns
                                      for n, p in g["members"]])),
               channels=tuple(n for n in names if n in g["channels"]),
               members=tuple(g["members"]),
               score=float(sum(p.strength for _, p in g["members"])))
        for g in groups
    ]
    return sorted(targets, key=lambda t: (-len(t.channels), -t.score))
