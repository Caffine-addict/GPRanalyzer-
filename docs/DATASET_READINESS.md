# Dataset readiness: what happens when the real data arrives

Written 2026-09-14, ahead of the company's bulk dataset. Purpose: know now, on four known
lines, exactly what the pipeline does with a survey and where it stops — so the answer when
thousands arrive is a run, not an investigation.

Every number below is measured on this machine against the four delivered jobs, not estimated.

## What we have today, measured

| | Value |
|---|---|
| Jobs | 4 (`Job_0696`, `Job_0703`, `Job_0720`, `Job_0730`) |
| Total size | 5.3 MB |
| Per job | ~864 KB — `Single-01.RAD` / `.RA1` / `.RA2`, plus `.gps` and `.map` |
| Per channel | 383 traces × 256 samples |
| Line length | 9.58 m (383 traces × 0.025 m wheel encoder) |
| Time window | 25.6 ns (RAD) / 51.2 ns (RA1) / 102.4 ns (RA2) |
| Parse time | **~0.8 ms per channel** (~2.4 ms for a whole 3-channel job) |
| Candidate boxes | 158 across 4 jobs, 144 KB of annotations |

Parsing is not the bottleneck and will not become one. A 10,000-line survey at this shape is
~8.6 GB of raw data and roughly **24 seconds of parsing**. Everything expensive is downstream.

## The chain, and what each stage does with a new line

| Stage | Module | State on real data |
|---|---|---|
| Parse | `parsers/spr.py` | **Works.** Validates record stride, file size, per-record header length, and refuses any `SPR_FILE_VERSION` but `7`. |
| Channel metadata | `studio/session.py` | **Works.** Trace spacing from `SPR_SHAFT_INTERVAL`, window from the sample interval, `max_depth_m` only when a dielectric is recorded. |
| Candidate boxes | `scripts/detect_candidates.py` | **Works.** Classical background removal + energy envelope. Not a trained detector. |
| Shape diagnosis | `detect/hyperbola.py` + `studio/diagnose.py` | **Works.** RANSAC hyperbola fit, coherence, aspect, implied permittivity per box. |
| Cross-channel agreement | `studio/corroborate.py` | **Works.** DBSCAN clusters apexes, RANSAC fits each run, distinct-channel count gates corroboration. |
| Trained detection | `detect/model.py` | **Blocked — no `weights/best.pt`.** `ModelNotFoundError` is the designed state; the orchestrator logs and continues per frame. |
| Evidence | `evidence/extract.py` | **Works, with one sharp edge — see below.** |
| Quality level | `evidence/quality.py` | **Works.** PAS 128 / ASCE 38 grade per finding. |
| Risk | `risk/score.py` | **Works.** All 9 taxonomy classes now weighted. |
| Reasoning | `reason/engine.py` | **Works** with `GROQ_API_KEY`; falls back to the smaller model, then degrades to measurements only. |
| Live device feed | `sources/edge_gateway.py`, `direct_device.py` | **Blocked — protocol unconfirmed.** Do not guess it. |

## The one thing that will bite on bulk data

`evidence/extract.py:_extract_depth` opens with `if frame.image is None: return None, "unavailable"`.
`parsers/spr.py` returns `image=None`. So **an SPR frame through the live pipeline reports depth
as unavailable**, even though it carries a real `sample_interval_ns` and the header's ε = 9.0.

Calibrated depth currently exists only on the Studio path, where a velocity is fitted from the
target's own hyperbola. That is deliberate and documented — the guard exists because box
coordinates from a *rendered* image were once treated as raw-trace indices, a bug caught before
it produced a wrong "calibrated" value — but it means a bulk pipeline run over real SPR files
yields no depths at all. **Reconcile the two coordinate spaces before the bulk run**, or accept
that bulk output is position-and-class only and that depth comes from Studio picks.

## Scaling limits, in the order they will actually be hit

1. **Interpretation is billed per call.** Every interpret request is a Groq call. Caching on an
   evidence fingerprint is the control; without it, a reviewer clicking through a large job pays
   per click. **Do this before the data lands.**
2. **`diagnose_job` re-reads and re-diagnoses a whole job on every request.** Fine for 158
   boxes; it is an O(boxes × channel) recompute per API call. Cache on the boxes file's mtime.
3. **`corroborate` is O(n²) in apexes per line.** Deliberate — a few hundred apexes per line is
   the right size for the naive scan, and it avoids a ~100 MB scikit-learn dependency on a
   company PC whose OS is still unconfirmed. If apex counts per line ever reach thousands, that
   is the moment to revisit, not before.
4. **Annotations are one JSON file per job, read-modify-write under a process-local lock.** Two
   *separate* processes writing one job can still collide. Single Studio process: fine. Multiple
   workers over a shared volume: not.
5. **Synthetic simulation is the real cost centre**, unchanged: ~55 min per 384-trace scene at
   the 6 mm production grid on this CPU. Real labelled data displaces simulation, which is the
   strongest argument for prioritising ground truth.

## What to do the day the data arrives

1. `python -m studio` and open one new job. Confirm the parser accepts it — a different
   `SPR_FILE_VERSION`, channel set, or sample count will fail loudly and immediately, by design.
2. Run `scripts/detect_candidates.py --all` then `scripts/diagnose_candidates.py --all` over the
   new jobs and check the implied-permittivity distribution against the site's ε. A median far
   from the header value means the sampling-interval assumption does not hold for this batch.
3. Feed each line's fitted apexes through `studio/corroborate.py` and count targets seen on two
   or more channels. That count, not the raw box count, is the number worth showing anyone.
4. Grade each with `evidence/quality.py`. Expect **QL-B4** for header-derived depth and **QL-B2**
   for fitted-velocity picks; QL-B1 needs both a fitted velocity and cross-channel agreement.
5. Only then talk about training. A trained detector needs labels, and the open question is
   still company question #1 and #6 in `COMPANY_QUESTIONS.md`.

## Labelled data: the one external option found

An open dataset of **2,239 expert-verified radargrams** in YOLO and VOC format (GSSI 200/400
MHz, urban and industrial sites, Morocco 2019–2024; classes: utilities, voids, intact) is
published on Mendeley Data, DOI `10.17632/ww7fd9t325.1`.

It does not replace the company's ground truth — different instrument, different ground — but it
is real labelled data, and the honest use for it is **measuring the sim-to-real gap** rather than
assuming it. Published work on exactly this transfer reports mAP@0.5 rising from 0.357
(field-only training) to 0.591 (synthetic→real transfer) to 0.643 with topological features
added, which is also a realistic ceiling to quote to anyone.

**Check the licence before use.** The dataset landing page states CC BY 4.0 while the
accompanying paper states CC BY-NC 4.0. Non-commercial would matter here, and the discrepancy
needs resolving with the authors rather than assumed either way.
