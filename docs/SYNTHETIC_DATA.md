# Synthetic training data

The detector needs labelled B-scans, and the project has none: `Dataset/DSU_GPR_Files/`
is raw, and the 16 confirmed crops in `reference/hyperbolas/` carry no class or depth
(company questions #1 and #6). Rather than wait, we simulate survey lines whose buried
objects we placed ourselves. The physics draws the B-scan, and the labels come free and
exact.

## The plan in one line

Train a detector on **3 shapes** from simulated data, then turn each shape into one of the
**9 taxonomy classes** from measurements.

| Detector shape (`detect/shapes.py`) | Becomes (`detect/refine.py`) |
|---|---|
| `point_reflector`: a hyperbola | `clear_point_reflector` / `low_snr_point_reflector` by amplitude; `multiple_point_reflectors` if 3+ are evenly spaced; `cluttered_multi_target` if crowded irregularly; `intersecting_linear_and_point_reflector` if it sits on a linear reflector |
| `linear_reflector`: flat or continuous | `elongated_linear_target` |
| `disturbed_or_void`: an area | `cavities` if the top echo keeps the direct wave's polarity (an air gap), else `disturbed_zone` |

`strong_high_contrast_reflector` is never produced: it needs calibrated amplitude, which
the system does not have yet. A detector trained directly on the taxonomy, once real labels
exist, passes straight through the mapping unchanged.

## Pipeline

```
simulate/scenes.py        random scene: soil, layers, pipes, slabs, trenches, voids, stones
simulate/gprmax_input.py  scene -> gprMax input text (scene B-scan + a one-trace background)
simulate/runner.py        runs gprMax in Docker (simulate/docker/), resumable
simulate/convert.py       onto the instrument's time grid; depth gain; measured noise
simulate/labels.py        boxes measured from the scattered field (scene minus background)
simulate/dataset.py       render exactly as the live pipeline does -> YOLO dataset
detect/train.py           train YOLOv8 on it
```

Commands:

```bash
docker build -t gpr-analyzer-gprmax:950d0e19 simulate/docker   # once, ~2 min
.venv/bin/python -m simulate generate --runs sim_runs/batch1 --count 30 --seed 1
.venv/bin/python -m simulate run      --runs sim_runs/batch1   # resumable; Ctrl-C is safe
.venv/bin/python -m simulate build    --runs sim_runs/batch1 --out datasets/synthetic/batch1
.venv/bin/python -m simulate validate --runs sim_runs/batch1     # cavity rule vs known voids/trenches
.venv/bin/python -m detect.train --data datasets/synthetic/batch1/data.yaml --epochs 100
```

`sim_runs/`, `datasets/` and `runs/` are git-ignored: they're large and can be regenerated.

## What the synthetic data is matched to

Every instrument constant in `simulate/instrument.py` was **measured from the four real
lines** (RAD channel), and `tests/test_simulate_instrument.py` re-measures them from the
data so they can't drift away from it unnoticed.

| Constant | Value | Source |
|---|---|---|
| Trace spacing | 0.025 m | `SPR_SHAFT_INTERVAL` header field |
| Sample interval | 0.1 ns | `SPR_SAMPLING_INTERVAL` 100, read as picoseconds |
| Frame | 384 traces × 256 samples | real lines are 381–393 traces |
| Centre frequency | 466 MHz | spectral peak, all 4 lines |
| Direct-wave peak | sample 20 | measured 19–22 |
| Noise / peak echo | 0.009 | measured 0.007–0.011 |
| Direct wave / peak echo | 0.64 | median; measured 0.40–1.18 |
| Depth fade | 9-point profile | median over the 4 lines |

The 466 MHz peak is also a third independent check on reading the sampling interval as
picoseconds. In nanoseconds, the same spectrum would peak at 0.47 MHz.

**Rendering parity.** Every synthetic frame goes through the same
`render.bscan.traces_to_image` and `preprocess.enhance.enhance` the live pipeline uses. A
picture rendered any other way would teach the detector about images it will never be
shown.

**Depth gain, and a bug worth remembering.** Simulated echoes fade with depth from
physics alone; the real instrument adds its own gain on top. So one exponential time gain
is fitted across the synthetic set, making the typical frame fade the way the real lines
do. It is fitted only below the direct wave and held at 1 above it.

An earlier version matched the depth profile row by row, including the direct-wave rows.
There the synthetic background-removed signal is empty by construction: the simulated
direct wave is identical in every trace, so subtracting the mean trace cancels it
exactly. Dividing by almost nothing boosted the direct wave 50×, and every rendered frame
came out as one bright band over featureless grey. The simulated direct-to-echo ratio
(0.63) already matched the real lines (0.40–1.18), and the fitted gain keeps it there
(0.65).

**No gain from a handful of scenes.** The gain is fitted only from 20 or more scenes with
echoes. On the smoke batch, adding one scene to two moved the fitted gain 15x at the bottom
of the record, and every label box moved with it. That's a small-sample effect, not an
instrument property. Smaller sets are left at unity: reproducible, and the raw simulation
already matches the real direct-to-echo ratio.

**Empty frames still get noise.** Noise is set against a frame's strongest echo; an empty
frame has none. So its echo level is inferred from the direct wave, using the measured
ratio. Otherwise "perfectly clean" would become the detector's cue for "nothing here".

**Labels are measured, not drawn.** Geometry says roughly where to look. The box is the
extent of the object's scattered energy inside that region. An object whose energy never
clears 4σ of the noise gets no box, so the detector is never trained on targets the
instrument could not have shown.

**Visible stones are labelled too.** A 7 cm stone draws the same hyperbola as a 7 cm cable,
and a single 2-D line cannot tell them apart; only repeat survey lines can. Leaving
visible stones unlabelled would teach the detector to ignore small hyperbolas, and it
would then miss small cables. So a stone that clears the visibility bar is a
`point_reflector` like any other. Stones too faint to see stay unlabelled, as clutter.

**Neighbouring hyperbolas.** Hyperbola limbs overlap, so three rules keep each box on its
own object. First, a box may only reach as far along the line as the object's diffraction
aperture. Second, a point reflector whose peak is under a third of a neighbour's gives up
that neighbour's whole band; otherwise the brighter limb sets the fainter object's
threshold and drags its box across. Third, a point reflector's box must contain its own
apex, or it gets no box. All three came from real simulator output: boxes 2.95 m wide and
218 of 256 samples tall, a faint stone near a metal pipe boxed on the pipe's limb, and
another stone boxed 0.8 m from where it lay on a fragment of a neighbour's echo. See
*What a label box covers* below.

## Assumptions, stated

- **Antenna offset is not measured.** 0.10 m is typical of shielded 400–500 MHz utility
  antennas, and the generator varies it from 0.08 to 0.14 m.
- **The model is 2-D.** A pipe is a cylinder crossing the line; a slab runs along it.
  Oblique crossings are not simulated.
- **Utility materials use textbook values**: plastic ε 3, water ε 80, concrete ε 6.
- **Water inside pipes is under-resolved at 6 mm.** gprMax estimates a 4% phase-velocity
  error inside the water. It affects the pipe's internal ringing, not its hyperbola.
- **Ground is layered but otherwise clean**, apart from stones and disturbed zones. Real
  ground has more clutter. That's the domain gap fine-tuning on real labels will close.

## Cost

Measured on this Mac (M4, gprMax's CPU solver in Docker, 10 OpenMP threads):

| Grid | Per trace | One 384-trace scene | Scenes per day |
|---|---|---|---|
| 6 mm (production) | ~8.5 s | **~55 min** | ~26 |
| 12 mm (plumbing checks only) | ~3.2 s | ~21 min | ~70 |

Each trace carries about 0.6 s of fixed gprMax setup on top of the solve. A smaller grid
helps less than its cell count suggests: at 12 mm the 10 threads get too little work each
and spend proportionally more time coordinating. So 4× fewer cells ran only about 2.6×
faster. The 12 mm grid is also too coarse to train on: it under-resolves the wavelength
in wet soil.

Levers, in order of size:

- **gprMax's CUDA solver on an NVIDIA GPU**, typically an order of magnitude faster. This
  is the path to thousands of scenes: the company PC if it has one, or a rented GPU box.
- **A moving model window.** Only about ±1.8 m of ground around the antenna can echo
  back inside the record, so a narrow model that slides with the antenna would do about
  a third of the work of modelling the whole 10 m line on every trace.
- **Two scenes side by side with 5 threads each**, which may beat one scene on 10
  threads for the same reason small grids scale poorly.

## Licence boundary

gprMax is GPL-3.0. It is built into its own Docker image from a pinned commit, and
gpr-analyzer only writes its input text and reads its HDF5 output. Nothing imports it.
One workaround ships with the image, `simulate/docker/lscpu`. On ARM Linux, gprMax's
host probe cannot parse `lscpu` and crashes before simulating; the wrapper supplies the
numbers it expects, and gprMax's source is untouched.

## What a label box covers (settled 2026-09-12)

A box is the extent of an object's echo above 15% of its own peak, inside a prior. The open
question was how far that prior should reach — and at first it reached everywhere. A
hyperbola's band followed both limbs to the edge of the frame and on down to the record
floor, so any energy along that tail, the object's own or a neighbour's, stretched the box
with it. On the four real simulated lines, point-reflector boxes ran to a median 1.58 m and
a maximum 2.95 m wide, up to 218 of 256 samples tall: a box covering most of the frame says
nothing about where the target is.

Two changes, both leaving the box measured from the physics rather than drawn from geometry:

1. **The prior stops at the diffraction aperture** — one target depth each side of the
   apex, the 45-degree aperture (`scenes.hyperbola_half_aperture_m`). Past that the limb
   has flattened towards the straight asymptote every deeper target shares, so it says
   little about *this* target and, boxed, only adds width.
2. **Scenes are sparser, and spaced by that same aperture.** One area feature at most (a
   void inside a trench under a slab gave three area boxes nobody could tell apart), 1-3
   pipes, 0-3 stones, and every pair of point reflectors far enough apart that their
   apertures do not touch. The previous rule was a flat 0.5 m and applied to pipes only:
   stones ignored it and each other, leaving a median nearest neighbour 0.26 m away and
   overlapping boxes in most scenes.

One definition serves both jobs, which is the point of it. The generator spaces objects by
exactly the distance the labeller will later measure over, so "one object, one box" holds
by construction rather than by luck.

Measured by re-labelling the same four real simulated lines. Those are the *old* crowded
scenes, so the table isolates the labeller change with the scene change held out:

| | before | after |
|---|---|---|
| Point box width, median | 1.58 m | **0.95 m** |
| Point box width, p90 | 2.16 m | **1.48 m** |
| Point box width, max | 2.95 m | **1.95 m** |
| Tallest point box | 218 of 256 samples | **97 of 256** |
| Boxes reaching the record floor | 1 | **0** |
| Objects labelled | 31 | **33** |
| Area box width, median / max | 2.53 m / 4.08 m | 2.53 m / 4.08 m (unchanged) |

Two *more* objects were labelled, not fewer. A bounded prior reads an object's own peak
instead of a brighter neighbour's, so the 4-sigma visibility test gets fairer, not stricter.

On the new generator, over 300 scenes: point reflectors per scene fell from a median 6
(max 12) to a median 3 (max 6); the nearest-neighbour distance between point reflectors
rose from a median 0.26 m (minimum 0.002 m) to a median 1.47 m (minimum 0.44 m); no scene
carries more than one area feature, against 41 of 300 before. Empty scenes stay at 9.7%,
and pipes still lie inside trenches, which is the commonest real layout there is.

**A trap worth recording.** Area priors subtract "where a point reflector's echo could be".
Bounding *that* mask by the aperture as well left fragments of a pipe's echo just outside
it and inside the trench's prior — and since a hyperbola outshines backfill scatter
tenfold, those fragments set the trench's 15% threshold and collapsed its box onto the
pipe, the exact failure the exclusion exists to prevent. Two tests caught it. The two masks
answer different questions, so `_point_bands` returns both: the aperture-limited band for
an object's own box, and the unbounded union for areas to subtract.

This also closed the open slab case. `test_a_point_reflectors_box_does_not_run_into_a_far_stronger_slab` was an expected
failure after two attempts at an area-dominance rule failed — one read strength
circularly, the other broke pipes lying in trenches. A pipe's band no
longer reaches a slab's echo far along its limb, so the test passes on its own and the
xfail marker is gone. There are no expected-failure tests left in the suite.

## Not yet validated

- **The cavity polarity rule.** It's standard interpretation practice but unproven on this
  instrument. `python -m simulate validate` runs the exact rule on every simulated void and
  trench, where the object really is, and counts voids called cavities against trenches
  wrongly called cavities. Check that on a full batch before anyone relies on a `cavities`
  call.
- **Performance on real lines.** There are no real labels to measure against until
  company question #1 or #6 is answered.
