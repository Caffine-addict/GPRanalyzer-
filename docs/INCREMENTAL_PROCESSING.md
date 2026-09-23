# Incremental (live) processing — design plan

**Status: plan only. No code has been written against this.** Written 2026-09-23.

Every figure marked **[M]** was measured on this machine against the four delivered lines and
the raw output is in `docs/pilot/VERIFICATION_2026-09-23.md`. Every figure marked **[A]** is an
assumption; the ones that matter most are open company questions #7 and #8.

---

## 1. The problem this solves

The product goal is live mapping: findings appear while the cart is moving. The pipeline today
cannot do that, and the reason is not speed.

**Speed is fine.** One line (3 channels, every stage, cold) costs **~1.36 s** **[M]**, against a
**~6.9 s** walking budget for a 9.65 m line **[M for the line length, A for the 1.4 m/s pace]** —
about 5× headroom.

**Correctness is the blocker.** Three stages are defined over a *whole line* that does not exist
yet when the data is arriving:

| Stage | What it does today | Why it breaks live |
|---|---|---|
| Background removal | subtracts the mean trace over **all** traces (`studio/processing.py::remove_background`, mode `"mean"`) | the mean shifts as traces arrive, so a chunk processed at t=2s is subtracted against a different background than the same chunk at t=8s |
| Detection threshold | 82nd percentile of the **whole channel**'s normalised envelope (`_THRESHOLD_PERCENTILE`, `scripts/detect_candidates.py`) | the threshold moves under you; a target detected early can stop being detected once more data changes the percentile |
| Row background | median across **all** traces, per sample row (`detect_candidates.py`) | same failure, on the depth-normalisation axis |

A whole-line result and a chunked result are therefore **different answers**, not the same answer
arriving sooner. That difference has never been measured, which is why §6 exists.

---

## 2. Assumption register

Everything downstream depends on these. None is confirmed.

| # | Assumption | Status | If wrong |
|---|---|---|---|
| A1 | Survey speed ≈ 1.4 m/s (standard pedestrian pace) | **[A]** — company question #8. The files carry only a survey start time, no per-trace timestamps, so this cannot be derived from the delivered data | the entire latency budget scales linearly. At 0.7 m/s everything is twice as easy; at 2.0 m/s the margin drops to ~3.5× |
| A2 | Trace capture is wheel-encoder-triggered at `SPR_SHAFT_INTERVAL` = 0.025 m | **[A]** — company question #7. Headers carry *both* `SPR_SHAFT_INTERVAL` (0.025) **[M]** and `SPR_TIMER_FREQUENCY` (10) **[M]**, which imply different models | if time-triggered at 10 Hz, the rate is fixed at 10 traces/s/channel regardless of speed — 5.6× less demanding than A1+A2 imply, and chunk sizing in §3 should be re-derived in time, not distance |
| A3 | The live device streams traces incrementally at all | **[A]** — company question #3, open since the project started. If the unit only exports a file at the end of a line, none of this is reachable and the whole plan is moot | this document is unbuildable; the source seam (`sources/edge_gateway.py`) stays `NotImplementedError` |
| A4 | Ground is statistically similar along a line, so a background estimated from one window is valid for nearby traces | **[A]** — standard GPR practice, but not validated on this corridor | background removal leaves structured residue that the detector reads as targets |
| A5 | The first N traces of a line are target-free (needed only by the calibration-frozen variant, §5) | **[A]**, and **often false** — an operator may start a line directly over a service | a frozen background contains a real target, which is then subtracted out of the whole line: a *missed* utility, the worst failure mode this system has |
| A6 | `find_candidate_boxes` behaviour is what we want to preserve chunk-to-chunk | **[A]** — it is the current detector, not a validated one. §6 measures agreement with it, which is consistency, not accuracy |

---

## 3. Chunk size and latency target

**Measured inputs.** Trace spacing 0.025 m **[M]**. Lines are 383–393 traces ≈ 9.6–9.8 m **[M]**.
Per-channel non-fit cost is ~8 ms **[M]**; the fit dominates at ~420 ms/channel **[M]**.

**Derived rate**, under A1+A2: 1.4 / 0.025 = **56 traces/s/channel**, 168/s across three.

**Proposed chunk: 64 traces = 1.6 m ≈ 1.14 s of travel** at A1.

Reasoning, not preference:
- **Lower bound** is set by the widest thing we want to detect whole. Candidate box widths
  measured across all 233 stored boxes: p50 = 21 traces (0.53 m), p90 = 65 traces (1.62 m),
  p99 = 186 traces (4.65 m) **[M]**. A chunk much narrower than a target means every wide
  target straddles a boundary. 64 traces covers the median comfortably and the p90 marginally.
- **Upper bound** is the latency the operator will tolerate. A chunk is not reportable until it
  is complete, so chunk size *is* the floor on time-to-first-finding: 64 traces ≈ 1.14 s.
- **Boundary handling**: process a sliding window with overlap, emit only the centre. With a
  64-trace stride and p90 targets at 65 traces, overlap must be ≥ 1 target width — propose
  **128-trace window, 64-trace stride, emit the middle 64**. A target wider than the overlap
  (p99, 186 traces) will still be split and should be reported as *provisional* until its
  neighbours arrive, not silently truncated.

**Latency target per chunk: ≤ 500 ms**, against the 1.14 s the next chunk takes to arrive
**[A1]**. That leaves ~2× margin for jitter and is comfortably met by everything except the fit
(§7). This mirrors the 500 ms fast-path target the batch orchestrator already holds itself to.

---

## 4. Sliding-window background removal, and an uncomfortable finding

**The mechanism.** Replace the whole-line mean trace with a mean (or median) over a trailing
window of W traces, recomputed per chunk. Incrementally this is cheap — a running sum, or
`scipy.ndimage.uniform_filter1d` over the window — and the current whole-line version costs only
1.5 ms/channel **[M]**, so cost is not the issue.

**Sizing W is the issue, and the measurement is awkward.** A target occupying E traces inside a
W-trace window contributes E/W of the background estimate, and is therefore partly subtracted
from itself. To keep self-subtraction under ~10%, W ≥ 10·E.

| Target extent E | Required W | In metres | Warm-up at A1 |
|---|---|---|---|
| p50, 21 traces **[M]** | 210 | 5.3 m | 3.8 s |
| p90, 65 traces **[M]** | 650 | 16.3 m | 11.6 s |
| p99, 186 traces **[M]** | 1860 | 46.5 m | 33 s |

**The finding, and its correction — measured 2026-09-23.** The table above reasons from trace
counts, and on that basis an earlier version of this section claimed the existing whole-line
background already self-subtracts a p90 target by roughly 65/386 ≈ 17%. **That was wrong.** A
target contributes to the mean trace in proportion to its *amplitude*, not its share of the
trace count, and the direct wave and ringing dominate the mean far more than any target does.

Measured across all 88 credible targets on the four lines
(`scripts/background_self_subtraction.py`, raw output in `VERIFICATION_2026-09-23.md`) **[M]**:
**1.74% median amplitude loss, 4.08% at p90, 11.35% worst — and zero depth-pick shift**, 0.0 mm
on every target, 0.00 samples of apex movement. The ridge picks driving the fit are argmax
positions and do not move for a few percent of amplitude.

So self-subtraction is **not** a reason to change background removal. The justification for the
sliding window is purely the live one: a whole-line mean cannot be computed from data that has
not arrived. Worth re-measuring only if a site brings genuinely long linear features — the 11.35%
worst case was a 130-trace-wide target, and width is what drives this.

**Consequences for the design:**
- Use a **median**, not a mean, over the window. A median is far less moved by a target
  occupying a minority of the window, which is the whole problem above. Cost rises but stays
  small at this data size. `remove_background` already has a `"moving"` mode to build on.
- **Warm-up**: no output until W traces exist. Propose **W = 256 traces (6.4 m, 4.6 s at A1)** as
  the starting point — above the p50 requirement, knowingly below p90 — and treat the p90 gap as
  a measured, stated limitation rather than a solved problem.
- During warm-up, emit the radargram (operators need to see something) but **no detections**,
  flagged as "warming up" rather than "nothing found". An empty result and a not-yet-ready result
  must never look alike.

---

## 5. AGC and detection threshold

Two options; propose running **both** and comparing in §6 rather than choosing on argument.

**Option A — windowed.** AGC and the 82nd-percentile threshold computed over the same trailing
W-trace window as the background. Self-consistent, adapts to changing ground, no target-free
assumption. Risk: the threshold is a *percentile*, so it is definitionally relative to window
content — a window containing many targets raises its own threshold and suppresses them. This
failure mode is specific to percentile thresholds and is why option B exists.

**Option B — calibration-frozen.** Compute AGC gain curve, row background and threshold once over
the first W traces, then freeze for the line. Stable and reproducible; a target cannot raise its
own threshold. Risk: depends on **A5** (first W traces target-free), which is often false, and
does not adapt if ground conditions change along the line.

**Recommendation:** default to **A (windowed)**, because A5 is the more dangerous assumption —
its failure mode is a *missed* utility, whereas A's failure mode is a *raised* threshold that
shows up as reduced sensitivity and can be detected by §6's recall measurement. Offer B as an
operator-selectable mode for sites where the start of the line is known clear.

---

## 6. How to validate — the part that decides whether this ships

**Protocol.** Replay each of the four delivered lines trace-by-trace through the incremental
pipeline, and compare its output against the whole-line batch result on the same line.

The batch result is the **reference**, not the truth — there is no ground truth (company question
#1). This measures *agreement with existing behaviour*, which is the only thing measurable today,
and must be reported in exactly those words.

**Metrics, per line and per channel:**
1. **Recall vs batch** — of the boxes batch finds, what fraction does incremental find? A box
   matches if centres agree within half the smaller box's width. This is the number that matters:
   a missed utility is the failure that hurts someone.
2. **Precision vs batch** — boxes incremental finds that batch does not. Not automatically wrong
   (a windowed threshold may legitimately find a target the whole-line percentile suppressed) but
   every one needs eyeballing before being called an improvement.
3. **Depth drift** — for matched boxes, the difference in fitted apex depth between incremental
   and batch. Report the distribution, not a mean; a mean near zero with wide spread is a
   different (worse) result than a small consistent bias.
4. **Boundary effect** — metrics 1–3 split by whether a box straddles a chunk boundary. If
   straddling boxes are materially worse, the overlap in §3 is too small; this is the specific
   number that tunes it.
5. **Warm-up cost** — how many real (batch-found) boxes fall inside the warm-up region and are
   therefore structurally unfindable live. At W=256 on a 386-trace line this is ~66% of the line,
   which is a striking number and exactly why it must be reported rather than buried.

**Gate to ship:** recall ≥ 0.95 vs batch outside the warm-up region, depth drift within the
existing corroboration tolerance (`DEFAULT_DEPTH_EPS_M` = 0.35 m), and a stated, quantified
warm-up limitation. Anything less is reported honestly and not shipped as "live mapping".

---

## 7. RANSAC — what actually works, measured

The fit is **93% of the per-line cost** **[M]**. Two ideas I proposed earlier were checked and
**one of them is wrong**; both are recorded here so nobody re-proposes them.

**✗ Gating the fit behind the credibility pre-filter does not work.** I suggested fitting only
boxes that pass the credibility check. That is circular: `fit_rejection_reason()` takes a
`VelocityFit` as its argument — the credibility check *is* the fit's output (apex time, apex
sample, inlier count, physical plausibility). There is nothing to gate with.

**✗ A cheap ridge-point pre-filter rejects nothing on this data.** A fit needs ≥ 8 inliers
(`MIN_CREDIBLE_INLIERS`), so a box with fewer than 8 ridge points cannot possibly pass, and
ridge extraction is cheap (0.055 ms/box **[M]**). Measured: **0 of 233 boxes** **[M]** have fewer
than 8 ridge points. The gate is sound in principle and fires on nothing here.

**~ Hoisting the envelope is real but small.** `fit_region()` calls
`compute_normalized_envelope()` on the **whole channel** for **every box** — 19.4 times per
channel. The envelope costs 1.5 ms **[M]**, so hoisting it to once per channel saves 27.6 ms of
435 ms **[M]** — **6%**. Worth doing (it is a clear redundancy) but it is not the answer.

**✗ Adaptive iteration count — proposed here, implemented, measured, and REJECTED (2026-09-23).**

This section previously projected 13–20× from replacing the fixed `_RANSAC_ITERATIONS = 2000`
with the textbook adaptive count N = log(1−p)/log(1−w³). **That projection was wrong and the
change was reverted.** Keeping the reasoning here because the error is instructive:

The formula bounds the probability of *drawing one outlier-free 3-point sample*. It says nothing
about having found the **maximal consensus set**, and this fitter's answer is a least-squares
refit over whichever inlier set was largest. With a 4-sample residual tolerance admitting
borderline ridge points, larger sets keep appearing late in the run and each one moves the refit.
So early exit returns a *different, less-supported* fit, not the same fit sooner.

Measured against the equivalence harness (`scripts/ransac_adaptive_experiment.py`, raw output in
`docs/pilot/VERIFICATION_2026-09-23.md`) **[M]**:

| Iteration floor | Disagreements | Fits identical | Speedup |
|---|---|---|---|
| 50 | 134 | 83/129 | 7.6× |
| 250 | 85 | 100/130 | 4.7× |
| 1000 | 12 | 126/130 | 1.8× |
| 2000 (= off) | **0** | 130/130 | 1.0× |

Equivalence holds only with the optimisation disabled. At the fast settings it moved apex
positions by several traces, velocities by up to 26%, depths by up to 0.22 m, and flipped
accept/reject — a target the shipped fitter finds can vanish. `detect/hyperbola.py` is unchanged.

**✓ The honest route to the same speed: vectorise the loop.** Draw all 2000 candidate triplets
as numpy arrays instead of iterating in Python. That does *identical* work in a different order,
so it cannot change a fit, and the same harness would confirm it bit-for-bit. Not yet attempted;
this is now the first thing to try, not the adaptive count.

**✓ Parallelism is available and independent of the above.** 10 CPUs **[M]**; each box fit is
independent and seeded. Realistic expectation **6–8×**, not 10×: process-pool dispatch overhead
and memory bandwidth, against a ~200 KB traces array per channel that forks cheaply. Worth doing
*after* the iteration fix, since parallelising a 20× -overhead is wasted effort.

**Combined projection (not yet measured):** 420 ms/channel → roughly 20–30 ms/channel. That turns
the fit from 93% of the budget into a rounding error, and makes the per-chunk 500 ms target easy.

---

## 8. Sequencing

1. ~~**Measure the self-subtraction problem on batch first**~~ — **done 2026-09-23**: it is
   1.74% median, 11.35% worst, with zero depth impact (§4). Not a blocker, and not a
   justification for the sliding window either.
2. ~~**Adaptive RANSAC** (§7)~~ — **done and rejected**: it fails identical-results validation
   (see §7). Replaced by **vectorising the RANSAC loop in numpy**, which cannot change a fit and
   is validated by the same harness.
3. **Chunked replay harness** (§6) against the current whole-line pipeline, before changing any
   processing — establishes the reference and the measurement code.
4. **Windowed background + threshold** (§4, §5 option A), measured through step 3.
5. Calibration-frozen mode (§5 option B) only if step 4's numbers justify the extra mode.
6. Envelope hoisting and parallelism (§7) last — pure speed, no correctness risk, easy to land
   whenever.

Nothing here should start before company questions #7 and #8 are answered, because A1 and A2 set
the chunk size and the latency target, and #3 decides whether live input exists at all.
