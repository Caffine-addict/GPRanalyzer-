# GPR B-scan pattern reference

What patterns actually look like in a radargram, the physics behind why each one forms, and
how each maps to this project's 9-class detection taxonomy (see `CLAUDE.md`). Written with
an industrial-site survey as the primary case, but covers the general pattern catalog first
since industrial sites produce *combinations* of ordinary patterns, not exotic new ones.

## 1. The physics — why any reflection happens at all

GPR sends a short EM pulse into the ground and records what bounces back. A reflection occurs
at any boundary where the **dielectric permittivity** (ε, how much a material slows/stores an
EM field) changes. The strength of the reflection is set by the reflection coefficient:

```
R = (√ε1 − √ε2) / (√ε1 + √ε2)
```

Bigger contrast → stronger, brighter reflection. This one formula explains almost every
pattern below:

- **Metal** (pipes, rebar, tanks): effectively infinite conductivity → R ≈ 1 → the strongest
  possible reflection, and near-total energy loss means **nothing below it is visible** (a
  "shadow zone").
- **Air voids** (cavities, collapsed pipe, sinkhole throat): soil (ε≈4-20) next to air (ε=1) is
  also a huge contrast → strong, sharp reflection at the void's top surface.
- **Water**: ε≈80, far higher than any dry soil/rock. A saturated zone or water table is a
  strong, continuous reflector, and wet/conductive soil also **attenuates** the signal heavily
  (energy is lost to conduction, not just reflected) — this is why signal quality degrades in
  wet or contaminated ground.
- **Compacted vs. loose soil of the same material**: small ε contrast → a weak, easy-to-miss
  reflector. This is the physical reason a `low_snr_point_reflector` class needs to exist at
  all — some real features are just inherently faint, not a detector failure.

Depth is inferred from two-way travel time: `depth = (time × velocity) / 2`, where
`velocity = c / √ε`. This project's 3 channels (RAD 100ps / RA1 200ps / RA2 400ps — see
`parsers/spr.py`) sample progressively deeper, later-arriving time windows, so the **same
physical object can appear in one channel and not another** if it sits outside that channel's
depth range — a real absence, not a missed detection.

## 2. The pattern catalog

### Hyperbola (point/pipe-like reflector)
**Why**: A GPR antenna doesn't emit a single ray straight down — it illuminates a cone. As the
antenna passes near (not just directly over) a small object, it detects it from an angle, so
the *measured* two-way time is shortest directly overhead and increases symmetrically on either
side — plotting that time-vs-position curve produces a hyperbola, even though the object itself
is a single point (or a pipe seen end-on/perpendicular to the survey line).
**Causes**: pipes/cables crossing the survey line, rebar, boulders, tree roots, isolated debris.
**Taxonomy**: `clear_point_reflector` (sharp, high-contrast) or `low_snr_point_reflector` (faint,
low dielectric contrast — e.g. a plastic pipe vs. a metal one).

### Multiple/repeated hyperbolas, evenly spaced
**Why**: same mechanism as above, repeated at regular intervals.
**Causes**: rebar mesh in a slab (classic "picket fence" pattern, very regular spacing), a row
of closely-spaced parallel utility lines, fence posts, pilings.
**Taxonomy**: `multiple_point_reflectors`.

### Elongated/continuous reflector running along the survey direction
**Why**: if a linear object (pipe, cable, wall footing) runs *parallel* to the survey line
rather than crossing it, every trace sees roughly the same depth to it — no hyperbola forms,
instead a flat or gently-varying continuous band appears.
**Causes**: a pipe or duct the survey line happens to walk alongside, a foundation wall, a
curb/edge, a slab joint.
**Taxonomy**: `elongated_linear_target`.

### Intersecting linear + point pattern
**Why**: exactly what it sounds like geometrically — one utility crossing another at depth
produces a linear reflector (the one running parallel) crossed by a hyperbola (the one crossing
perpendicular), at their real intersection depth.
**Causes**: a utility crossing junction — extremely common at industrial sites where multiple
services (power, water, gas, comms) converge near a building.
**Taxonomy**: `intersecting_linear_and_point_reflector`.

### Strong/bright high-contrast reflector, not obviously hyperbolic
**Why**: very high R (metal, or a metal/air boundary) saturating the receiver, sometimes
producing a broad bright band or plate-like reflection rather than a clean point.
**Causes**: a large flat metal object (tank lid, steel plate, manhole cover), a dense rebar mat,
the top of a buried tank.
**Taxonomy**: `strong_high_contrast_reflector`.

### Cavity / void signature
**Why**: the top of an air- or fluid-filled void is a strong reflector (large ε contrast at the
top surface); the *bottom* of the void is often much weaker or invisible because the signal
loses coherence crossing the void, and there can be a subtle velocity "pull-down" (the void's
low ε means faster propagation through it, so features below appear shallower than they are).
**Causes**: collapsed/broken pipe leaving a void, sinkhole formation, an old unfilled excavation,
erosion beneath a slab — worth flagging seriously at an industrial site since these are often
safety-relevant (subsidence risk under equipment/foundations).
**Taxonomy**: `cavities`.

### Disturbed zone — chaotic, discontinuous reflectivity
**Why**: undisturbed native soil tends to show reasonably continuous, gently-varying horizontal
layering (natural soil horizons). Backfilled/re-compacted ground loses that continuity — you get
a jumbled mix of small diffractions and broken layering instead, because the fill material is
heterogeneous (mixed clasts, air pockets, compaction variation).
**Causes**: a previously excavated and backfilled utility trench, remediated/regraded ground,
old foundation removal and infill — again very common at industrial sites with a long history of
construction and utility work.
**Taxonomy**: `disturbed_zone`.

### Cluttered multi-target zone
**Why**: many small, irregularly-spaced diffractors close together, none individually dominant —
statistically different from the "regular spacing" of `multiple_point_reflectors`.
**Causes**: construction debris fields, scrap metal, a congested/undocumented utility corridor,
riprap/rubble fill.
**Taxonomy**: `cluttered_multi_target`.

### Attenuation / signal-loss zone (no discrete target, but a real anomaly)
**Why**: a zone of higher conductivity (wet clay, saline or contaminated soil, dense clay lens)
absorbs EM energy faster than surrounding material — the radargram just goes dark/quiet below a
certain point in that zone, with no discrete reflector to box.
**Causes**: soil moisture variation, a contamination plume (industrial sites specifically — fuel/
chemical leaks alter soil conductivity), a clay lens.
**Taxonomy note**: this doesn't map cleanly onto the current 9-class taxonomy at all — it's
absence-of-signal, not a detectable object. Worth flagging to the company as a possible gap:
if attenuation zones matter for their use case (e.g. contamination screening), the taxonomy may
need a 10th class, or this needs to be handled as a separate derived signal (e.g. per-region
signal energy) rather than a bounding box at all.

### Ringing / multiples (an artifact, not a real second object)
**Why**: energy bounces back and forth between two strong reflectors (commonly the ground
surface and a shallow strong target) before finally returning to the antenna, producing a fake
"echo" of a real feature at roughly 2x, 3x its true depth.
**Causes**: any strong shallow reflector, but especially metal.
**Practical implication**: don't let a repeated hyperbola-like pattern at a suspicious *exact*
depth multiple of a real shallow feature get boxed as a second independent target — check
whether it's just ringing before labeling in phase 2.

## 3. Industrial-site-specific patterns (your stated main case)

Industrial sites don't introduce new physics — they introduce a specific *mix* of the above,
worth watching for specifically:

- **Utility congestion near buildings**: expect `intersecting_linear_and_point_reflector` and
  `cluttered_multi_target` far more often near structures than in open ground — multiple
  services converge there.
- **Underground storage tanks (UST)**: `strong_high_contrast_reflector` (the tank top), often
  with a shadow zone beneath, and check surrounding soil for attenuation anomalies (possible
  leak history) even though that's outside the current taxonomy.
- **Old backfilled trenches from past construction phases**: `disturbed_zone`, frequently running
  parallel to current utility corridors — easy to mistake for a `elongated_linear_target` if you
  only look at one channel; check whether the reflectivity inside the band is continuous
  (real pipe) or chaotic (an old trench with nothing left in it).
- **Scrap/debris fields**: industrial sites accumulate buried construction debris over decades —
  `cluttered_multi_target`, usually shallow, irregular.
- **Rebar-heavy foundations/slabs**: dense `multiple_point_reflectors` right at the surface,
  which can mask (shadow) anything directly beneath — a real detection gap, not a modeling
  failure, worth noting in any report.
- **Subsidence/void risk beneath equipment pads**: `cavities` near foundations are the
  highest-consequence finding category at an industrial site — worth a lower confidence bar for
  flagging these specifically (false positive here costs a look; false negative could matter).

## 4. How this should shape phase-2 labeling

When ground truth eventually arrives (see `docs/COMPANY_QUESTIONS.md` #1), classifying each
`annotations/<job>/boxes.json` candidate should mean asking, in order:

1. **Shape**: hyperbola (point), continuous band (linear), or chaotic (disturbed/cluttered)?
2. **Regularity**: one isolated feature, evenly-spaced repeats (rebar/multi-point), or irregular
   cluster (cluttered)?
3. **Contrast**: does it saturate/dominate (`strong_high_contrast_reflector`) or is it barely
   above background (`low_snr_point_reflector`)?
4. **Context**: does it intersect another feature at the same depth (intersecting), sit inside a
   zone that looks disturbed relative to its surroundings (disturbed_zone), or have a
   void-like top-strong/bottom-weak signature (cavity)?

None of this replaces the company's ground truth — it's a framework for making that
classification pass fast and consistent once the ground truth exists, and for having an
informed conversation with the company about *why* a given box looks the way it does.

## 5. Confirmed examples from this corridor

`reference/hyperbolas/` holds 16 hyperbola crops cut from two signed-off
utility-survey deliverables for the same Bangalore ORR / Devarabisanahalli
corridor as `Dataset/DSU_GPR_Files/`. They are the only confirmed imagery in
the repo — use them as the visual benchmark for the Hyperbola pattern in
section 2 (GPR Studio shows them beside the radargram). They carry no class or
depth labels yet; see `docs/COMPANY_QUESTIONS.md` #6.
