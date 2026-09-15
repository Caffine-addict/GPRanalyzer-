"""Does the same buried object appear on more than one channel — and where does it run?

The SPRScan 3D records three channels at once (RAD/RA1/RA2) from different receivers at
different sampling intervals. A reflector seen independently on two of them, agreeing in
position *and* in depth, is far stronger evidence than one strong fit on one channel: the
receivers, the time axes and the fits are all independent, so agreement is not a shared
artefact. This module is that test, and the geometry that follows from it.

Two things are computed, in the order the literature does them:

1. **Cluster apexes in space.** Published 2D->3D reconstruction work clusters detected apex
   points across parallel B-scans with DBSCAN, then fits a line per cluster, and found RANSAC
   beats total least squares (RMSE 0.056 m vs 0.083 m) because it rejects bad picks rather than
   averaging them in. The same procedure applies to apexes from several channels of one pass.
2. **Fit each cluster's run.** A cluster of apexes from one object should be collinear in
   (along-line, depth); the fitted line is the object's run, and its residual says how well the
   collinear-object story actually holds.

Replaces an earlier hand-rolled rule that chained corroboration transitively — A agrees with B,
B with C, therefore all three are one object — which merged distinct targets into one chain
(RA1+RA1+RAD+RA1+RAD+RAD+RA2) and then averaged depths that disagreed. Clustering has no such
failure mode: membership is decided by density in the space itself, and a point that reaches
its cluster only through a chain of neighbours is exactly what `eps` is for.

**Why DBSCAN is implemented here rather than imported.** It was written this way to avoid adding
scikit-learn and scipy under the project's old cross-platform deployment rule. **That rule was
relaxed on 2026-09-15, so this is no longer a constraint** — the code stays because it works, is
tested, and is ~30 lines over a few hundred apexes where the naive O(n^2) neighbour scan is the
right choice anyway. If scikit-learn arrives for another reason, swapping to `sklearn.cluster.DBSCAN`
is reasonable; note it defaults to Euclidean distance, and the per-axis (Chebyshev) metric below is
load-bearing, so pass `metric="chebyshev"` or the project's one real corroborated target is rejected.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

# A target is "the same target" across channels when it agrees to roughly this much. Both come
# from the one corroborated real target the project has (Job_0703 at ~8.5-8.6 m, ~1.2-1.4 m
# deep on RAD and RA1): those two fits agreed to 0.10 m in position and 0.18 m in depth, so a
# tolerance below that would have rejected the project's own best evidence.
DEFAULT_POSITION_EPS_M = 0.30
DEFAULT_DEPTH_EPS_M = 0.35

# A point can belong to a cluster and still not sit on that cluster's run: cluster membership
# asks "is this the same object?", the run fit asks "does this apex lie on its line?". Reusing
# the cluster tolerance here made every member an inlier by construction, which quietly turned
# the RANSAC fit into the least-squares fit it was chosen over — one bad pick then tilted the
# line exactly as the literature says it would.
RUN_RESIDUAL_TOL_M = DEFAULT_DEPTH_EPS_M / 3

# If two receivers really saw the same object, the wave crossed the same ground to reach it, so the
# permittivity each fit implies must agree. Position and depth agreeing while permittivity does not
# is a coincidence, not a corroboration — on the four real lines this single check removed 8 of 14
# "corroborated" targets, including pairs whose implied permittivities differed by a factor of 20.
MAX_PERMITTIVITY_RATIO = 2.0

_NOISE = -1


@dataclass(frozen=True)
class Apex:
    """One fitted apex, in physical units, tagged with the channel it came from."""

    id: str
    channel: str
    position_m: float  # distance along the line (wheel encoder)
    depth_m: float
    dielectric: float | None = None
    fit_r2: float | None = None
    n_inliers: int | None = None


@dataclass(frozen=True)
class Corroboration:
    """One cluster of apexes judged to be the same object."""

    apex_ids: tuple[str, ...]
    channels: tuple[str, ...]  # distinct channels, sorted
    position_m: float  # cluster centre
    depth_m: float
    position_spread_m: float  # max - min, so a reader sees the disagreement, not just the mean
    depth_spread_m: float
    run_slope_m_per_m: float | None  # depth change per metre along the line, None if < 2 apexes
    run_residual_m: float | None  # RMS residual of the RANSAC line fit
    dielectric_range: tuple[float, float] | None = None  # None when no member carried one

    @property
    def n_channels(self) -> int:
        return len(self.channels)

    @property
    def permittivity_ratio(self) -> float | None:
        """How far apart the *receivers* are on permittivity, as a factor. None if unknown.

        Computed from `dielectric_range`, which holds one value per channel — each channel's
        best-constrained fit — not the extremes across every member. Taking min/max over all
        members let a single weak fit veto the cluster: on Job_0703 at 8.54 m the known target's
        two strong fits (eps 8.34 on 77 inliers, eps 11.30 on 74) agree to a factor of 1.36, but a
        third apex at eps 2.14 on 17 inliers stretched the range to 5.3 and blocked it. That is the
        same "one bad pick dominates" failure `_ransac_line` was fixed for, one layer up.
        """
        if self.dielectric_range is None:
            return None
        low, high = self.dielectric_range
        return high / low if low > 0 else None

    @property
    def permittivity_agrees(self) -> bool:
        """Do the members agree about the ground they measured?

        Unknown permittivities count as agreement: absence of the measurement is not evidence
        against the target, and treating it as such would silently discard every cluster built from
        fits that could not imply a velocity.
        """
        ratio = self.permittivity_ratio
        return ratio is None or ratio <= MAX_PERMITTIVITY_RATIO

    @property
    def corroborated(self) -> bool:
        """Two distinct channels saw it *and* they agree about the ground.

        Two fits on the same channel are not corroboration: they share a receiver, a time axis and
        whatever systematic error that channel carries. And two channels that disagree about
        permittivity did not measure the same ground, so their agreement on position and depth is
        coincidence — that second half was missing until the first real run exposed it.
        """
        return self.n_channels >= 2 and self.permittivity_agrees


def _neighbours(scaled: np.ndarray, index: int) -> np.ndarray:
    """Indices within one tolerance of `index` on *every* axis (Chebyshev distance).

    Per-axis, not Euclidean, and that is the whole contract: the rule this module replaces asked
    for agreement in position AND in depth, each with its own tolerance. Euclidean distance
    rejects a pair that satisfies both tolerances individually but fails their combination — the
    project's one corroborated real target sits at 0.33 of the position tolerance and 0.52 of the
    depth tolerance, a Euclidean distance of 1.26, so Euclidean threw away the best evidence of a
    buried object the project has.
    """
    return np.flatnonzero(np.max(np.abs(scaled - scaled[index]), axis=1) <= 1.0)


def dbscan(scaled: np.ndarray, *, min_samples: int) -> np.ndarray:
    """Standard DBSCAN labels for points already scaled so that eps == 1 in every axis.

    Scaling per axis before clustering is what lets position and depth carry different
    tolerances: a 0.30 m position difference and a 0.35 m depth difference both become a
    distance of 1. Labels are 0.. for clusters and -1 for noise, matching the usual convention.
    """
    n = len(scaled)
    labels = np.full(n, _NOISE, dtype=int)
    visited = np.zeros(n, dtype=bool)
    cluster = 0

    for seed in range(n):
        if visited[seed]:
            continue
        visited[seed] = True
        reachable = _neighbours(scaled, seed)
        if len(reachable) < min_samples:
            continue  # stays noise for now; a later cluster may still absorb it as a border point

        labels[seed] = cluster
        queue = [i for i in reachable if i != seed]
        while queue:
            current = queue.pop()
            if labels[current] == _NOISE:
                labels[current] = cluster
            if visited[current]:
                continue
            visited[current] = True
            current_reachable = _neighbours(scaled, current)
            if len(current_reachable) >= min_samples:
                queue.extend(i for i in current_reachable if labels[i] == _NOISE)
        cluster += 1

    return labels


def _ransac_line(positions: np.ndarray, depths: np.ndarray, seed: int = 0) -> tuple[float, float]:
    """(slope, rms_residual) of a RANSAC line through (position, depth), inliers only.

    RANSAC rather than least squares for the reason the reconstruction literature gives: one
    mis-picked apex drags a least-squares line, while RANSAC leaves it out of the fit entirely.
    """
    n = len(positions)
    if n < 2:
        raise ValueError("a line needs at least two apexes")
    if n == 2:
        span = positions[1] - positions[0]
        slope = float((depths[1] - depths[0]) / span) if span else 0.0
        return slope, 0.0

    rng = np.random.default_rng(seed)
    tolerance = RUN_RESIDUAL_TOL_M
    best_inliers: np.ndarray | None = None
    best_score: tuple[int, float] | None = None
    for _ in range(200):
        i, j = rng.choice(n, size=2, replace=False)
        span = positions[j] - positions[i]
        if span == 0:
            continue
        slope = (depths[j] - depths[i]) / span
        intercept = depths[i] - slope * positions[i]
        residuals = np.abs(depths - (slope * positions + intercept))
        inliers = residuals <= tolerance
        # Rank on inlier count, then on how tightly those inliers actually fit. Counting alone
        # ties too easily: a line drawn through one good apex and a bad one can catch exactly as
        # many points as the true line, and with a strict `>` the winner was then decided by
        # whichever sample the RNG happened to draw first — which is how a level run came back
        # with a slope of 1.6, keeping the outlier and discarding a good apex. Total residual
        # breaks that tie the way it should: 0.0 for the true line, 0.21 for the impostor.
        score = (int(inliers.sum()), -float(residuals[inliers].sum()))
        if best_score is None or score > best_score:
            best_score, best_inliers = score, inliers

    if best_inliers is None or best_inliers.sum() < 2:
        best_inliers = np.ones(n, dtype=bool)

    slope, intercept = np.polyfit(positions[best_inliers], depths[best_inliers], 1)
    residuals = depths[best_inliers] - (slope * positions[best_inliers] + intercept)
    return float(slope), float(np.sqrt(np.mean(residuals**2)))


def corroborate(
    apexes: Sequence[Apex],
    *,
    position_eps_m: float = DEFAULT_POSITION_EPS_M,
    depth_eps_m: float = DEFAULT_DEPTH_EPS_M,
    min_samples: int = 2,
) -> list[Corroboration]:
    """Group apexes into objects, strongest corroboration first.

    Returns one entry per cluster, ordered by distinct-channel count then by inlier weight, so
    the targets worth putting to the company come first. Apexes DBSCAN calls noise are dropped:
    a lone apex is a candidate, not a corroborated object, and `studio/candidates.py` already
    reports candidates.
    """
    if position_eps_m <= 0 or depth_eps_m <= 0:
        raise ValueError(
            f"tolerances must be positive, got position={position_eps_m} depth={depth_eps_m}"
        )
    if not apexes:
        return []

    scaled = np.column_stack(
        [
            np.array([a.position_m for a in apexes], dtype=np.float64) / position_eps_m,
            np.array([a.depth_m for a in apexes], dtype=np.float64) / depth_eps_m,
        ]
    )
    labels = dbscan(scaled, min_samples=min_samples)

    results: list[Corroboration] = []
    for label in sorted(set(labels) - {_NOISE}):
        members = [apex for apex, member in zip(apexes, labels == label, strict=True) if member]
        positions = np.array([m.position_m for m in members], dtype=np.float64)
        depths = np.array([m.depth_m for m in members], dtype=np.float64)

        slope = residual = None
        if len(members) >= 2:
            slope, residual = _ransac_line(positions, depths)

        # One permittivity per receiver — the best-constrained fit that channel produced — because
        # corroboration asks whether the *receivers* agree, not whether every fit in the cluster
        # does. A channel that saw the target twice contributes its better look, not its worse one.
        best_per_channel: dict[str, tuple[int, float]] = {}
        for m in members:
            if m.dielectric is None or m.dielectric <= 0:
                continue
            inliers = m.n_inliers if m.n_inliers is not None else 0
            current = best_per_channel.get(m.channel)
            if current is None or inliers > current[0]:
                best_per_channel[m.channel] = (inliers, m.dielectric)
        known_eps = [eps for _inliers, eps in best_per_channel.values()]
        dielectric_range = (min(known_eps), max(known_eps)) if known_eps else None

        results.append(
            Corroboration(
                apex_ids=tuple(m.id for m in members),
                channels=tuple(sorted({m.channel for m in members})),
                position_m=float(positions.mean()),
                depth_m=float(depths.mean()),
                position_spread_m=float(positions.max() - positions.min()),
                depth_spread_m=float(depths.max() - depths.min()),
                run_slope_m_per_m=slope,
                run_residual_m=residual,
                dielectric_range=dielectric_range,
            )
        )

    def strength(item: Corroboration) -> tuple[int, int]:
        return (item.n_channels, len(item.apex_ids))

    return sorted(results, key=strength, reverse=True)
