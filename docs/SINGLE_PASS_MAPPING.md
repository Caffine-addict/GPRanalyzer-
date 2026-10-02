# Single-pass live mapping — product feasibility plan

**Status: plan only. No code.** Written 2026-09-23. Every claim is marked **[M]**easured (with a
pointer to where) or **[A]**ssumed. Where a claim depends on a company answer, it names the
question number in `docs/COMPANY_QUESTIONS.md`.

**Product framing, restated so the rest of this doc can refer back to it.** A topography map is
surveyed in first. The GPR then runs over it, one pass per corridor, while the system places
each finding on that map, depths it, hypothesises what it is, and — this is the point — tells
the operator *before they leave the site* whether that finding needs a second, crossing pass to
resolve, or whether the first pass already settled it. Success is measured in passes avoided,
not in detections made.

---

## a) What one pass, one position, three frequencies can and cannot determine

The hardware, confirmed from the headers (`docs/pilot/CHANNEL_IDENTITY.md`): one eQuantum
antenna head, one position per trace, three simultaneous frequency channels — **[M]** RAD/RA1/RA2
sample at 100/200/400 ps with antenna-type codes 4/3/2. This is not a multi-static array; there
is one transmit/receive geometry per trace, tripled in frequency, not in position.

**Can determine, from one pass:**

- **Depth**, when a target's own hyperbola fits — measured from curvature, not assumed
  (`studio/velocity.py`). This is the one genuinely calibrated number the system produces.
- **Position along the line** (chainage), from the wheel encoder — **[M]** 0.025 m/trace,
  confirmed accurate on all four lines against `SPR_SHAFT_INTERVAL`.
  `docs/pilot/GPS_DIAGNOSTIC.md`.
- **A point vs. an extended reflector**, from box shape and continuity along the line
  (`detect/refine.py`'s `POINT_REFLECTOR`/`LINEAR_REFLECTOR`/area classes) — a service crossing
  the line under it draws a compact hyperbola; a service running *along* the line draws a
  continuous band, sometimes with no clear apex at all.
- **A rough material/void hint from echo polarity** — `detect/measure.py::echo_matches_direct_wave_polarity`:
  a void reflects with the direct pulse's own polarity, a soil/water/metal interface reverses it.
  Already implemented, and already flagged in the codebase as checked against only 2–3 simulated
  objects — not validated on real target diversity.
- **A frequency-response hint**, in principle: if a reflector is visible on RAD (highest
  frequency, finest resolution, shallowest reach) but faint or absent on RA2 (lowest frequency,
  deepest reach), that is consistent with a small, shallow object; the reverse pattern (weak on
  RAD, strong on RA2) is consistent with something larger or the direct-wave/ringing band
  swamping the shallow channel. **Not implemented today** — nothing in `evidence/extract.py`
  or `reason/prompt.py` currently compares amplitude *across* channels for one target, only
  position/depth agreement. This is a real, buildable signal (Part 4's parity table returns to
  it) but it is speculative until measured against labelled data — **[A]** its diagnostic value,
  not its existence.

**Cannot determine, from one pass, on physical grounds — not a software gap:**

- **Which direction a linear feature runs**, beyond "parallel to the line" vs. "crosses it".
  One antenna position sweeping along one line has no cross-line baseline; a utility running at
  30° to the line and one running at 60° draw indistinguishably continuous bands on this line
  alone. Direction requires a second, non-parallel observation — see (b).
- **Whether a feature that appears to run alongside the line continues past where the line
  ends**, or whether it's a short feature the line happened to run beside.
- **Material identity beyond a coarse polarity call.** A void and an air-filled duct can share
  the same reversed-echo signature; a stone and a small pipe draw the same hyperbola
  (`docs/pilot/CAPABILITY_STATEMENT.md` already states this, correctly, for the current system).
- **Absolute horizontal position on a site plan**, without georeferencing — see (d). Chainage is
  real and accurate; it is not yet a map coordinate.

---

## b) Traced-feature data model

**Why this has to exist separately from a pick.** PAS 128 grades a linear section of a utility,
not a point anomaly (`evidence/quality.py`'s docstring, corrected this session). No object in the
current codebase represents "a utility run assembled from several detections on adjacent lines" —
`Evidence`/`Finding` (`core/contracts.py`) are per-detection. This is the gap Part 4's grading
correction deferred to Part 5.

**Proposed shape** (not implemented — describing the contract, not code):

```
TracedFeature:
    id
    member_finding_ids: tuple[str, ...]      # the Findings/picks strung together
    sections: tuple[FeatureSection, ...]      # split at 5 m intervals along the traced run
    continuity_confidence: "high" | "medium" | "low" | "single_line_only"
    orientation_estimate: Angle | None         # only computable with 2+ non-parallel lines, see (a)

FeatureSection:  # the PAS 128 grading unit — attaches here, never to a pick
    start_chainage_m, end_chainage_m
    depth_range_m: (min, max)
    quality: QualityAssessment                 # evidence/quality.py, still "ungraded" until
                                                 # ground truth + georeferencing exist (Part B/C)
    supporting_finding_ids: tuple[str, ...]
```

**Linking rule, concretely — [A] the specific geometry, since no adjacent-line data exists yet
to validate it against.** Two findings on adjacent, roughly-parallel lines are candidates for
the same feature when:
1. their depths agree within the existing corroboration tolerance (`DEFAULT_DEPTH_EPS_M` =
   0.35 m, `studio/corroborate.py`) — reused rather than invented, since it is the one distance
   tolerance already validated against a real corroborated target, and
2. their chainage positions project onto a straight or gently curving line across the two
   passes' known lateral offset — which requires (d)'s georeferencing to even compute a lateral
   offset between lines. **This is the load-bearing dependency**: traced features cannot exist
   before chainage-to-map placement exists, because "adjacent line" only means something in map
   space.

**Why this is a genuine extension of, not a replacement for, `studio/corroborate.py`.** Corroboration
today clusters apexes *across channels at the same position* (same trace, different frequency).
Tracing clusters apexes *across positions on adjacent lines* (same feature, different trace). The
same DBSCAN + RANSAC-line-fit approach `studio/corroborate.py`'s docstring already cites from the
literature (apex clustering → RANSAC line fit, 0.056 m vs. 0.083 m RMSE for least-squares) is the
right tool for this second, lateral use too — it is not a new algorithm, it is the same one run
across a different axis.

---

## c) Live coverage/confidence overlay

**Purpose, restated concretely:** show the operator, while still on site, a map with every
surveyed line drawn on it, each stretch coloured by how settled its findings are, and a short
list of *specific chainage ranges* worth a second pass — not a blanket "do it all again".

**Per-stretch state, derived only from what this system can already measure:**

| State | Condition | Colour cue |
|---|---|---|
| **Clear** | no candidate box in this stretch on any channel | green |
| **Resolved** | a candidate is credible (`fit_rejection_reason` is None) *and* corroborated across 2+ channels with permittivity agreement — the current, strict `corroborated` definition | green, marked |
| **Point, single-line** | a credible point-shaped detection, seen on only one channel or without permittivity agreement | amber — depth is real, identity is not confirmable from this line alone |
| **Runs-parallel, undetermined direction** | a linear/area-shaped detection running alongside the line (see (a)) | amber, flagged "needs a crossing pass to resolve" — this is the single most useful flag the overlay can raise, because it names exactly the case single-pass sensing structurally cannot resolve |
| **Ungraded, low credibility** | a candidate box that failed the credibility check (`fit_rejection_reason` fired) | grey — a real detector output, explicitly not trusted, shown so the operator can eyeball it rather than have it silently vanish |

**What the overlay flags for a crossing pass — and, as important, what it does not:**

- **Flags:** every "runs-parallel, undetermined direction" stretch (that's what a crossing pass
  resolves — a second line at a different angle turns "parallel to line" into an actual bearing);
  any "point, single-line" stretch where the risk class is escalation-worthy (the existing
  `elongated_linear_target` / `intersecting_linear_and_point_reflector` HIGH-risk rule in
  `risk/score.py`, reused rather than reinvented).
- **Does not flag:** "resolved" stretches (repeating a pass there is exactly the waste the
  product exists to cut), or "clear" stretches (nothing to chase).

**Design is deliberately conservative about depth-based prioritisation** — **[A]**: a shallower
finding is not automatically higher priority than a deeper one; risk class and continuity
uncertainty drive the flag, not depth alone, because a deep unresolved linear feature can matter
more than a shallow resolved point.

**Not designed here: the rendering.** This section specifies *what state each stretch is in and
why*, which is the part existing code (`risk/score.py`, `studio/corroborate.py`,
`evidence/quality.py`) already computes or can compute without new detection logic. The map
rendering itself is Part 4 territory (a genuine new UI surface) and depends on (d) existing first.

---

## d) Georeferencing without reliable GPS

**The constraint, already measured, not re-litigated here:** the onboard GPS reports a healthy
fix while recording near-zero movement on 3 of 4 delivered lines, and on the 4th disagrees with
the wheel encoder by 12%, growing to over a metre of drift (`docs/pilot/GPS_DIAGNOSTIC.md`).
Chainage from the wheel encoder is accurate on all four lines regardless.

**Proposed method: chainage plus surveyed reference points, not GPS.**

1. **Before the GPR pass**, a topography survey (however the company already produces one —
   total station, RTK when it works, or a simple tape-and-stake layout) places two or more
   reference points *on the ground* at known map coordinates, at or near the start and end of
   each intended line. **[A]** this survey step exists and precedes the GPR pass, per the
   product framing — it is the one hard prerequisite the whole plan depends on.
2. **The GPR line's start and end chainage (0 m and line-length m) are pinned to those two
   reference points.** Every chainage in between is placed by linear interpolation along the
   straight segment connecting them.
3. **Accuracy this can honestly claim:** bounded by two independent error sources, both
   measured, neither assumed —
   - **Chainage itself**: the wheel encoder against the recorded line length, consistent across
     all four lines to within measurement noise — **[M]** 9.47–9.80 m recorded lengths, no
     independent length reference exists in the delivered data to check against, so this is
     *self-consistency*, not absolute accuracy. Absolute wheel-encoder accuracy (tyre wear,
     calibration drift) is **[A]** unmeasured and worth asking the company about.
   - **Line straightness**: linear interpolation between two endpoints is exact only if the
     physical path was a straight line. Any real deviation (the operator's hand not tracking
     perfectly straight) is an error this method cannot detect or correct without a third
     reference point partway along the line. **Recommendation, not yet validated:** a third
     reference point near the line's midpoint on any line longer than ~15 m, so a bow in the
     path shows up as a chainage/coordinate mismatch instead of silently biasing every position
     past the bow.
4. **What this method cannot do:** place a finding on the map with genuine survey-grade
   accuracy — that needs the reference points themselves to be survey-grade, which is a company
   process question, not a software one. It can do something the current chainage-only export
   cannot: put a finding within a stated, bounded error of a real map location, honestly labelled
   as such (an `"estimated"` position-confidence tier, by direct analogy with the existing
   depth-confidence discipline — never `"calibrated"` unless the reference points themselves are
   survey-grade and the path-straightness assumption is checked).

**Explicitly not proposed:** trying to rehabilitate the onboard GPS. `GPS_DIAGNOSTIC.md`'s own
recommendation — check the receiver's operating mode — is a company action, not a software fix,
and this plan does not depend on that being resolved.

---

## e) Object-hypothesis method: measurements first, LLM narrates second

**The discipline, stated as a rule for Part 4's implementation to follow:** every hypothesis
field is populated from a measurement or a named rule *before* the LLM is called. The model's
job is to phrase the reasoning in plain language and name credible alternatives — never to
originate a classification the measurements don't support. This is the same seam the project
already enforces (`reason/prompt.py` builds text from `Evidence`, never from pixels); it extends
unchanged to hypothesis generation.

**Measurable inputs, all already computed or directly computable from existing code:**

| Signal | Source | What it constrains |
|---|---|---|
| Hyperbola shape (point vs. linear vs. area) | `detect/refine.py` | point reflector vs. extended feature vs. disturbed zone |
| Echo polarity vs. direct wave | `detect/measure.py::echo_matches_direct_wave_polarity` | void/air vs. soil/water/metal interface |
| Implied permittivity, and agreement across channels | `studio/velocity.py`, `studio/corroborate.py` | plausible material family (the four/five corroborated targets below show eps 8–60, a genuinely wide spread) |
| Per-frequency amplitude pattern (RAD vs. RA1 vs. RA2) | **not yet extracted** — see (a) | rough size/depth-class hint — flagged speculative until measured |
| Continuity along the line, and — once (b)/(d) exist — across lines | `studio/corroborate.py`, the proposed `TracedFeature` | single object vs. a run; orientation once cross-line data exists |
| Risk class from spacing/co-occurrence rules | `risk/score.py` | "utility-like" escalation, reused not reinvented |

**What the LLM is told, and what it is explicitly forbidden from inventing** — unchanged from
the existing prompt discipline (`reason/prompt.py`, `docs/pilot/CAPABILITY_STATEMENT.md`'s
"cannot tell a stone from a cable" honesty rule): it receives the measured class, the polarity
call, the permittivity, the corroboration count and now the traced-feature continuity state; it
is instructed to name the class's genuine physical alternatives (a point reflector is "pipe,
duct, cable, or isolated stone — indistinguishable from one line") rather than commit to one.

**What the 5 corroborated targets look like under this method — [M], measured this session:**

| Job / position / depth | Channels | Implied permittivity range | What the method would output |
|---|---|---|---|
| Job_0703, 8.54 m, 1.26 m deep | RA1+RA2 | 8.3–11.3 (tight agreement) | Point reflector, calibrated depth, corroborated. Eps consistent with ordinary soil (~9 recorded in the header) — no anomaly there. Hypothesis: buried point object (pipe/duct/cable/large stone), alternatives not excludable from one line. |
| Job_0730, 0.72 m, 0.89 m deep | RA1+RAD | 8.0–8.2 (very tight) | Same read: point reflector in ordinary-permittivity ground, well-constrained. Strongest of the five on eps agreement alone. |
| Job_0730, 6.72 m, 0.55 m deep | RA1+RAD | 16.5–16.7 (tight, but elevated) | Elevated permittivity relative to the header's ~9 — consistent with wetter ground or proximity to a water-bearing feature at this specific spot, stated as a measured deviation, not a material claim. |
| Job_0730, 3.48 m, 0.44 m deep | RA1+RA2 | 17.1–30.3 (wide) | Passes the corroboration ratio test (≤2×) but the range itself is wide enough that the method should flag lower confidence in the permittivity read specifically, distinct from the position/depth confidence, which is unaffected. |
| Job_0696, 0.15 m, 0.38 m deep | RA1+RA2 | 32.3–60.7 (very high, near the water-permittivity ceiling) | Near-surface, very high implied permittivity — consistent with shallow saturated ground or a genuinely different material response at this position. This is exactly the kind of target the method should hand to the LLM with an explicit "the permittivity here is unusual, treat the depth/material read with more caution than the other four" framing, not silently average it in. |

No object identity is claimed for any of the five beyond what the table states — that restraint
is the method working as intended, not a gap in it.

---

## f) Dependencies on `docs/COMPANY_QUESTIONS.md`

| # | Question | Blocks |
|---|---|---|
| **#1** | Ground truth (utility drawings / as-builts / follow-up walk) | Validating *any* hypothesis in (e) against reality; without it every hypothesis stays a hypothesis, permanently. |
| **#3** | Live device protocol | Whether "while still on site" (the product framing) is achievable at all, independent of every processing question in `docs/INCREMENTAL_PROCESSING.md`. |
| **#4** | Deployment OS/hardware | Whether a coverage-overlay UI (c) needs to run on a field tablet, a laptop, or is post-processed back at an office — changes the whole interaction design, not just packaging. |
| **#6** | Deliverable-sheet CAD labels | The single fastest route to checking (e)'s hypotheses against something concrete, and to validating the echo-polarity rule beyond its current 2–3 simulated objects. |
| **new #7** | Encoder- vs. time-triggered capture | Sets whether (d)'s chainage-interpolation error model needs a speed term at all — irrelevant if encoder-triggered (chainage is exact regardless of pace), material if time-triggered. |
| **new #8** | Survey walking speed | Same live-processing dependency as `INCREMENTAL_PROCESSING.md` — the overlay in (c) has to render *during* the pass, and "during" only has a number once this is answered. |
| *(new, this doc)* | Survey process: does a topography reference-point survey genuinely precede every GPR pass, and to what accuracy? | The entire premise of (d). Not currently on the list; the honest answer to "how accurate is this position" is unavailable without it. |

**Recommend adding the last item to `docs/COMPANY_QUESTIONS.md`** — not done in this pass, since
it wasn't there to correct, only to propose.
