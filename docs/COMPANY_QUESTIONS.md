# Open questions for the company

Tracks what we're still waiting on. Update status inline as answers arrive.

| # | Question | Why it matters | Status |
|---|----------|-----------------|--------|
| 1 | What's actually buried under the 4 scanned lines (`Job_0703/0720/0696/0730`)? Utility drawings, as-built plans, or a follow-up walk with known findings. | No labels exist anywhere in the delivered dataset — can't train `weights/best.pt` without this. | OPEN |
| 2 | What unit is `SPR_SAMPLING_INTERVAL` in? | Assumed picoseconds. Now has real supporting evidence (see below) but still not vendor-confirmed — do not flip depth from "estimated" to "calibrated" on this alone. | OPEN (evidence-backed) |
| 3 | Live device protocol — does the field unit stream live, or is manual file export (like this dataset) the normal workflow? | Determines whether `sources/edge_gateway.py` / `direct_device.py` can be implemented, or if batch/replay is the permanent model. | OPEN |
| 4 | What OS/hardware will this run on in the field? | Code must stay cross-platform; currently unconfirmed (Windows vs. Mac). | OPEN |
| 5 | Confirm: is the ~64KB leading block in every file just a fixed firmware seek-table template (see Resolved below), or does it ever carry real per-survey data on other units/firmware versions? | Low priority — not blocking, just closing the loop if a spec ever surfaces. | OPEN (low priority) |
| 6 | The two deliverable sheets now in `reference/hyperbolas/sheets/`: which CAD call-out (depth `D-x.xx` and utility type) belongs to each numbered GPR crop ①–⑧? | Turns 16 confirmed-but-unlabelled crops into 16 labelled examples with a class and a surveyed depth — the first ground truth in the repo, and a direct check on the depth axis. | OPEN |

## Independent cross-check on #2 (2026-09-09)

Fit 112 candidate boxes across all 4 jobs to the classical GPR point-diffractor hyperbola model
(`scripts/diagnose_candidates.py` — RANSAC + least-squares, standard technique per the GPR
literature, see `docs/GPR_PATTERN_REFERENCE.md`). Each fitted hyperbola's curvature implies a
propagation velocity, and from that a dielectric constant — computed independently of the
`SPR_MEDIUM_DIELECTRIC` header value, using only the confirmed-real `SPR_SHAFT_INTERVAL` and
the *assumed* picosecond unit for `SPR_SAMPLING_INTERVAL`.

**Result: median implied dielectric across all 112 fits = 11.5**, close to the header's stated
`9.0`. Individually, many of the best-constrained fits (highest inlier count) land within 10-20%
of 9.0. This is real, reproducible evidence the picosecond assumption is correct — if the true
unit were off by a large factor (e.g. actually nanoseconds, not picoseconds), implied dielectric
would be off by orders of magnitude, not clustering near a physically ordinary soil value.

**Caveat, not swept under the rug**: there's a long tail of implausible values (up into the
hundreds/thousands) from weakly-constrained fits — few inlier ridge points, technically under
the speed-of-light bound but not realistic for any real material. Only trust a specific box's
`implied_dielectric` when its `n_ridge_inliers` is reasonably high (rough guide: 10+); the
aggregate/median signal across many boxes is the trustworthy part, not any single number.

This upgrades #2 from "inferred, unconfirmed" to "inferred, now with independent physical
evidence" — still not the same as the vendor actually telling us the unit.

**Reproduced interactively in GPR Studio (2026-09-10).** The Studio hyperbola
tool (`studio/velocity.py`, same RANSAC fitter) on Job_0696 RAD, traces
100–119: R² 0.983, v = 0.1011 m/ns, **ε 8.8 against the header's 9.0** (2% apart).
One strong fit, not a survey — but it is the same answer from a fit an
operator can repeat by hand on any target.

## Resolved
- Hardware vendor confirmed: Subsurface Imaging Systems SPRScan 3D (not US Radar — the earlier Radar Studio/SEG-Y lead is moot).
- The ~64KB leading block: mostly-zero buffer containing a sparse 113-entry table of uint16 values incrementing by exactly 576 (the trace record size), wrapping at 16 bits (113 × 576 ≈ 65536). Identical structure/size across all 4 jobs regardless of survey length — this is a fixed firmware artifact (likely an internal seek/ring-buffer template baked in by the acquisition software), not per-survey data. Safe to ignore; parser already locates real trace records independently of this block.
