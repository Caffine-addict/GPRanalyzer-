#!/usr/bin/env python3
"""How much does the whole-line mean background subtract a real target from itself?

studio/processing.py's "mean" background removal subtracts the average trace over the WHOLE
line. A target occupying part of that line contributes to the average, so it is partly
subtracted from itself. This measures how much, on the four delivered lines, for every target
that passes the credibility check — the ones whose measurements are actually reported.

Method: for each credible target, compare the background-removed peak amplitude in its own box
using (a) the mean over all traces, as shipped, against (b) the mean over traces OUTSIDE the
target's own span, which is the unbiased estimate. The difference is what the target loses to
its own presence. Then refit the hyperbola both ways and compare depth and apex sample.

Usage:
    .venv/bin/python scripts/background_self_subtraction.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np

from core import boxes as box_store
from studio import session
from studio.velocity import fit_region, fit_rejection_reason

D = Path("Dataset/DSU_GPR_Files")
loss, dshift, tshift, rows = [], [], [], []

for job in sorted(d for d in D.iterdir() if d.is_dir()):
    for box in box_store.load_boxes(job.name):
        try:
            frame = session.load_frame(job, box.channel)
        except (session.JobNotFoundError, ValueError):
            # A box on a channel this job does not have. Skipping is the same choice
            # scripts/credibility_report.py makes, for the same reason.
            continue
        info = session.describe_channel(frame, box.channel)
        traces = session.raw_traces(frame).astype(np.float64)   # (n_traces, n_samples)
        x0, y0 = int(box.x), int(box.y)
        w, h = max(int(box.w), 3), max(int(box.h), 3)

        # only credible targets — the ones whose measurements we actually report
        fit_full = fit_region(traces, trace_start=x0, sample_start=y0, trace_span=w, sample_span=h,
                              trace_spacing_m=info.trace_spacing_m,
                              sample_interval_ns=info.sample_interval_ns, seed=0)
        if fit_full is None: continue
        if fit_rejection_reason(fit_full, n_samples=info.n_samples,
                                sample_interval_ns=info.sample_interval_ns): continue

        n_traces = traces.shape[0]
        lo, hi = max(0, x0), min(n_traces, x0 + w)
        mask = np.ones(n_traces, dtype=bool); mask[lo:hi] = False
        if mask.sum() < 10: continue

        mean_all  = traces.mean(axis=0, keepdims=True)          # includes the target
        mean_excl = traces[mask].mean(axis=0, keepdims=True)    # target's own traces removed

        band = slice(y0, min(y0 + h, traces.shape[1]))
        peak_full = np.abs((traces[lo:hi] - mean_all)[:, band]).max()
        peak_excl = np.abs((traces[lo:hi] - mean_excl)[:, band]).max()
        if peak_excl <= 0: continue
        pct = 100.0 * (peak_excl - peak_full) / peak_excl
        loss.append(pct)

        fit_excl = fit_region(traces - (mean_excl - mean_all), trace_start=x0, sample_start=y0,
                              trace_span=w, sample_span=h, trace_spacing_m=info.trace_spacing_m,
                              sample_interval_ns=info.sample_interval_ns, seed=0)
        if fit_excl is not None:
            dshift.append(abs(fit_excl.depth_m - fit_full.depth_m))
            tshift.append(abs(fit_excl.apex_sample - fit_full.apex_sample))
        rows.append((f"{job.name}/{box.channel}", w, pct))

loss = np.array(loss)
print(f"credible targets measured: {len(loss)}\n")
print("amplitude the target loses to its own presence in the whole-line mean background:")
print(f"  median {np.median(loss):.2f}%   p90 {np.percentile(loss,90):.2f}%   max {loss.max():.2f}%   min {loss.min():.2f}%")
print(f"  targets losing >5%: {(loss>5).sum()}/{len(loss)}    >10%: {(loss>10).sum()}/{len(loss)}")
if dshift:
    d = np.array(dshift); t = np.array(tshift)
    print(f"\ndepth-pick shift when the target is excluded from its own background (n={len(d)}):")
    print(f"  median {np.median(d)*1000:.1f} mm   p90 {np.percentile(d,90)*1000:.1f} mm   max {d.max()*1000:.1f} mm")
    print(f"  apex sample shift: median {np.median(t):.2f}  max {t.max():.2f} samples")
print("\nworst 6 by amplitude loss (target width in traces):")
for name, w, pct in sorted(rows, key=lambda r: -r[2])[:6]:
    print(f"  {name:<18} width={w:4d} traces  loss={pct:6.2f}%")
