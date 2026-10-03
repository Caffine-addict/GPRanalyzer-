# Interpreting like an experienced GPR analyst

Goal: make the Studio's analysis as close as possible to an interpreter with 20+ years of
experience. This page records where such people learn the job, which of their routine checks the
Studio now performs, how each was validated, and what still needs real data. Written 2026-10-04.

## Where GPR interpreters learn

| Source | What it teaches |
|---|---|
| [GSSI, *Utility Locating with GPR* handbook (MN72-615)](https://www.geophysical.com/wp-content/uploads/2021/07/MN72615B-Utility-Locating-Handbook.pdf) | The manufacturer's field rulebook: polarity, brightness, hyperbola shape, noise, penetration depth, depth calibration, survey layout. Read in full for this page. |
| NULCA-accredited courses: [Radiodetection 9900](https://www.radiodetection.com/en-us/training-courses/utility-locating-gpr-nulca-accredited-9900-online-demand-training), [Sensors & Software](https://www.sensoft.ca/training-events/courses/subsurface-imaging-with-gpr-01/) | Reflection physics, velocity, data interpretation, how GPR complements EM locating |
| [Sensors & Software, *Interpreting GPR data*](https://www.sensoft.ca/training-events/webinars/interpreting-gpr-data-part1/) | Metal vs non-metal, hyperbola width, attenuation, tracing across lines, noise, absent signal |
| [ASTM D6432-19](https://webstore.ansi.org/standards/astm/astmd643219) | Standard guide for surface GPR: equipment, field procedure, interpretation |
| PAS 128 ([client guide](https://cices.org/media/rbydmvnu/pas128-client-specification-guide-sep-22-final.pdf)) and ASCE 38-22 | Survey quality levels; QL-B needs GPR **and** EM locator, interpreted by a qualified person |
| A. P. Annan, *GPR: Principles, Procedures & Applications* (2003); D. J. Daniels, *Ground Penetrating Radar* (2004) | The theory under the rules; standard processing sequence |
| Mentoring and ground truth | Experience comes from seeing what was dug up where they marked. The Studio's equivalent is the supervisor's confirm/reject record (below). |

## The expert's routine, and where the Studio stands

| Expert check (source) | Studio | Evidence |
|---|---|---|
| Target is at the apex; reflection is from the object's top (GSSI) | Measured: candidates, picks and circles sit on the apex | — |
| Velocity from hyperbola shape, valid only on perpendicular crossings (GSSI) | Measured: RANSAC fit per target; vendor check 7.2 vs 7.3 reported | `studio/velocity.py`, vendor radargrams; Twente 01.1 hand-picked 9.0 vs 9.0. Automatically chosen hyperbolas: worse than guessing (`docs/EXTERNAL_DATA.md`) |
| Polarity: reversed = rise in permittivity (metal, water); kept = drop (air, and anything less dielectric than the soil) (GSSI, physics) | **Read at the apex** (new) | 8 unique simulated scenes, 40 objects, scored against physics (`scripts/score_polarity_on_simulation.py`): apex right on 27 of 32 it could read, unreadable on 8; the old box-averaged reading was right on 11 of 18, unreadable on 22. Strong-contrast objects (metal, water, voids, slabs, wet ground): 8 of 8 readable right. A small sample, and simulation only. |
| Brightness: metal bright, empty PVC weak; compare only at equal depth (GSSI) | Relative amplitude per target, in context | — |
| Usable depth / noise floor (GSSI, Sensors & Software) | **Measured per channel** (new) | Real lines: deep channel reaches noise at ~43% of its record (2.2 m on Job_0703); targets below are not identified |
| Wide hyperbola usually = oblique crossing (GSSI) | Explanations now say so | — |
| Pipe size not measurable below the antenna spacing (GSSI) | In the Assistant's checklist | — |
| Lateral resolution ~λ/2: close targets merge (GSSI) | In the checklist (~0.1 m at 466 MHz) | Centre frequency measured: `simulate/instrument.py` |
| Multi-frequency confirmation | Cross-channel corroboration | `studio/corroborate.py` |
| Service type needs outside information (GSSI, PAS 128) | Assistant says what to check; vendor drawings as context | — |

A correction worth keeping: a first tally reported "metal 6/6, concrete 3/3, voids 5/5". Several
simulation folders contain the same scenes, so those counts repeated 2 metal pipes three times, and
"concrete reversed" was scored as right when the simulator's concrete (permittivity 6) is less
dielectric than its soil (~9), so "kept" is the physically correct answer. The table above is the
recount on unique scenes, scored by the script rather than by eye.

## Not yet possible, and why

| Expert skill | What it needs |
|---|---|
| Ringing as a metal cue | The simulator's 2D metal pipes do not ring (tested: metal median 0.5 tail lobes vs 0 for voids), so the rule cannot be validated there. Needs real metal examples. |
| Top-and-bottom echo of water-filled plastic pipe (inner diameter, contents) | Water-filled pipes in the production simulation batch, now running; then validate. |
| Tracing a utility across parallel lines; 3D depth slices | Lines with known relative positions (a grid or surveyed reference points). |
| Depth calibration from ground truth (GSSI's best method) | One excavated target per site with its measured depth. |
| Surface-feature context (valves, manholes) | Site photographs or a walkover record per line. |
| EM locator fusion (PAS 128 QL-B) | EM locator readings over the same lines. |
| Accuracy figures on real lines | First real test done on 71 excavated Twente surveys (`docs/EXTERNAL_DATA.md`). Automatic detection and automatic velocity do not beat chance; a hand-picked hyperbola's velocity matches the surveyors' value. More labelled lines will come from the supervisor review record. |

## How the Studio keeps learning

An interpreter improves by comparing marks with what excavation finds. The Studio records the
same loop: every AI claim is confirmed or rejected by a named supervisor with a note
(`annotations/<job>/reviews.json`, CSV and PDF exports). Once enough reviews carry excavation
notes, they become labelled training and validation data for the detector and the material
rules. Until then, every material call stays capped at low confidence.
