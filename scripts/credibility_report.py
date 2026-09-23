#!/usr/bin/env python3
"""Credible-target and cross-channel-corroborated counts, measured fresh — not remembered.

Runs studio/velocity.py's fit_region + fit_rejection_reason (the same credibility rule the
Studio applies to a manual pick) against every stored candidate box, then
studio/corroborate.py's corroborate() across each job's channels. This is the pipeline that
originally produced the "91 credible, 7 corroborated" figures quoted before 2026-09-15 — no
script for that pass was ever checked in, so this is a fresh measurement against the current
box set, not a bit-for-bit reproduction of an unrecorded one. Re-run this after any change to
detect_candidates.py's threshold, compute_normalized_envelope, or fit_region/
fit_rejection_reason, rather than repeating a number from memory (see CLAUDE.md's own history
of stale-figure mistakes).

Usage:
    .venv/bin/python scripts/credibility_report.py
"""

from __future__ import annotations

from pathlib import Path

from core import boxes as box_store
from studio import corroborate as corroboration
from studio import session
from studio.velocity import fit_region, fit_rejection_reason

_DATASET = Path("Dataset/DSU_GPR_Files")


def main() -> int:
    if not _DATASET.exists():
        print(f"no dataset at {_DATASET}")
        return 1

    rows: list[tuple[str, int, int, int, int, int]] = []
    total_boxes = total_credible = total_multi_channel = total_corroborated = total_objects = 0

    for job_dir in sorted(d for d in _DATASET.iterdir() if d.is_dir()):
        job_name = job_dir.name
        boxes = box_store.load_boxes(job_name)
        apexes = []
        credible = 0
        for box in boxes:
            try:
                frame = session.load_frame(job_dir, box.channel)
            except (session.JobNotFoundError, ValueError):
                continue
            info = session.describe_channel(frame, box.channel)
            traces = session.raw_traces(frame)
            fit = fit_region(
                traces,
                trace_start=int(box.x),
                sample_start=int(box.y),
                trace_span=max(int(box.w), 3),
                sample_span=max(int(box.h), 3),
                trace_spacing_m=info.trace_spacing_m,
                sample_interval_ns=info.sample_interval_ns,
                seed=0,
            )
            if fit is None:
                continue
            if fit_rejection_reason(fit, n_samples=info.n_samples, sample_interval_ns=info.sample_interval_ns):
                continue
            credible += 1
            apexes.append(
                corroboration.Apex(
                    id=box.id,
                    channel=box.channel,
                    position_m=fit.apex_trace * info.trace_spacing_m,
                    depth_m=fit.depth_m,
                    dielectric=fit.dielectric,
                    fit_r2=fit.r2,
                    n_inliers=fit.n_inliers,
                )
            )

        clusters = corroboration.corroborate(apexes)
        # Two different numbers, reported side by side because they were being conflated.
        # `n_channels >= 2` only asks whether two channels landed in the same cluster.
        # `.corroborated` additionally requires the channels to agree about the ground they
        # measured — studio/corroborate.py's permittivity check, whose own docstring records
        # that it removed 8 of 14 "corroborated" targets, including pairs whose implied
        # permittivities differed by a factor of 20. This script previously printed the loose
        # count under the heading "corroborated", which overstated it: on the regenerated box
        # set the two are 13 and 5.
        multi_channel = sum(1 for c in clusters if c.n_channels >= 2)
        corroborated = sum(1 for c in clusters if c.corroborated)
        rows.append((job_name, len(boxes), credible, multi_channel, corroborated, len(clusters)))
        total_boxes += len(boxes)
        total_credible += credible
        total_multi_channel += multi_channel
        total_corroborated += corroborated
        total_objects += len(clusters)

    print(
        f"{'job':<10} {'boxes':>6} {'credible':>9} {'on 2+ ch':>9} "
        f"{'corroborated':>13} {'clustered objects':>18}"
    )
    for job_name, n_boxes, n_credible, n_multi, n_corr, n_obj in rows:
        print(f"{job_name:<10} {n_boxes:6d} {n_credible:9d} {n_multi:9d} {n_corr:13d} {n_obj:18d}")
    print(
        f"\nTOTAL: {total_boxes} boxes -> {total_credible} credible -> "
        f"{total_multi_channel} on 2+ channels -> {total_corroborated} corroborated "
        f"(2+ channels AND agreeing on permittivity), of {total_objects} clustered objects"
    )
    print(
        "\n'corroborated' is the number to quote: two channels landing in one cluster while "
        "disagreeing\nabout the ground they measured is a coincidence, not corroboration."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
