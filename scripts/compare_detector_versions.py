#!/usr/bin/env python3
"""Like-for-like comparison of the pre-fix and post-fix detector, both on CLEAN box sets.

The original claim ("the fix recovered real signal; corroboration roughly doubled") compared a
158-box pre-fix count against the 233-box UNION, which included the 158. This runs each detector
on its own, from scratch, and compares the two funnels honestly.

Pre-fix behaviour is reconstructed as a flat 1/8 of the record (32 of 256 samples) blanked on
every channel, against today's per-channel round(3.07 ns / sample_interval) = 31/15/8 samples.

That 1/8 is not a guess. The fix's own comment describes the old skip as "12%" blanking
"0.15/0.31/0.61 m" on RAD/RA1/RA2, but 0.12 reproduces 155 boxes where the vault records 158
historically. Sweeping the constant, **0.125 — exactly 1/8, i.e. 32 samples — reproduces 158
exactly**, and its implied depths (0.16/0.32/0.64 m) are the comment's figures rounded down.
So the comment's "12%" was itself an approximation of 1/8, and 1/8 is the faithful
reconstruction. Still a reconstruction, not the original code: the pre-fix detector was never
committed separately (it arrived inside the squashed commit 78b0f3b), so this matches on the
box count and on the documented depths, which is the strongest check available.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import detect_candidates as dc

from detect import classical
from studio import corroborate as corroboration
from studio import session
from studio.velocity import fit_region, fit_rejection_reason

DATASET = Path("Dataset/DSU_GPR_Files")
JOBS = sorted(d for d in DATASET.iterdir() if d.is_dir())
real_skip = classical.direct_wave_skip_samples


_PREFIX_SKIP_FRACTION = 1 / 8  # reproduces the historical 158-box count exactly; see docstring


def prefix_skip(n_samples, sample_interval_ns):
    return min(n_samples, round(_PREFIX_SKIP_FRACTION * n_samples))  # flat, channel-blind


def funnel(use_prefix: bool):
    classical.direct_wave_skip_samples = prefix_skip if use_prefix else real_skip
    rows, t_boxes, t_cred, t_corr, t_obj = [], 0, 0, 0, 0
    for job in JOBS:
        apexes, boxes_n, credible = [], 0, 0
        for ext, _ in session.CHANNEL_ORDER:
            p = job / f"Single-01.{ext}"
            if not p.exists():
                continue
            frame = session.load_frame(job, ext)
            info = session.describe_channel(frame, ext)
            traces = session.raw_traces(frame)
            for (x, y, w, h) in dc.find_candidate_boxes(traces, info.sample_interval_ns):
                boxes_n += 1
                fit = fit_region(traces, trace_start=int(x), sample_start=int(y),
                                 trace_span=max(int(w), 3), sample_span=max(int(h), 3),
                                 trace_spacing_m=info.trace_spacing_m,
                                 sample_interval_ns=info.sample_interval_ns, seed=0)
                if fit is None:
                    continue
                if fit_rejection_reason(fit, n_samples=info.n_samples,
                                        sample_interval_ns=info.sample_interval_ns):
                    continue
                credible += 1
                apexes.append(corroboration.Apex(
                    id=f"{ext}-{x}-{y}", channel=ext,
                    position_m=fit.apex_trace * info.trace_spacing_m,
                    depth_m=fit.depth_m, dielectric=fit.dielectric,
                    fit_r2=fit.r2, n_inliers=fit.n_inliers))
        clusters = corroboration.corroborate(apexes)
        # The strict definition on both sides: 2+ channels AND agreeing on permittivity.
        # Counted as apexes rather than clusters, consistently, so the two runs compare.
        n_in_corr = sum(len(c.apex_ids) for c in clusters if c.corroborated)
        rows.append((job.name, boxes_n, credible, n_in_corr, len(clusters)))
        t_boxes += boxes_n; t_cred += credible; t_corr += n_in_corr; t_obj += len(clusters)
    classical.direct_wave_skip_samples = real_skip
    return rows, (t_boxes, t_cred, t_corr, t_obj)


for label, pre in (("PRE-FIX  (flat 1/8 skip, 32 samples on every channel)", True),
                   ("POST-FIX (per-channel 3.07 ns skip: 31/15/8 samples)", False)):
    rows, tot = funnel(pre)
    print(f"\n{label}")
    print(f"  {'job':<12}{'boxes':>7}{'credible':>10}{'corroborated':>14}{'objects':>9}")
    for r in rows:
        print(f"  {r[0]:<12}{r[1]:>7}{r[2]:>10}{r[3]:>14}{r[4]:>9}")
    print(f"  {'TOTAL':<12}{tot[0]:>7}{tot[1]:>10}{tot[2]:>14}{tot[3]:>9}")
    if tot[1]:
        print(f"  corroborated as a share of credible: {100*tot[2]/tot[1]:.1f}%")
