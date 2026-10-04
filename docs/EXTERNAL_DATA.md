# External GPR data

Public, real-world GPR datasets used to test the analysis. Downloads live under
`Dataset/external/` (git-ignored). Written 2026-10-04.

## University of Twente utility surveys: used

R. ter Huurne, *Ground Penetrating Radar dataset with ground-truth data of utility surveying
activities*, 4TU.ResearchData, 2023. DOI [10.4121/96303227-5886-41c9-8607-70fdd2cfe7c1.v1](https://doi.org/10.4121/96303227-5886-41c9-8607-70fdd2cfe7c1.v1).
Licence **CC0** (no conditions; credit given anyway).

- 125 surveys at 13 Dutch construction sites; 759 radargrams in SEG-Y (`parsers/segy.py`).
- 500 MHz air-launched antenna, 0.02 m trace spacing, 512 samples over 50 ns. The measured spectral
  peak is about 264 MHz.
- Every survey has a trial-trench drawing giving each utility's distance along the trench and its
  depth. `Metadata.csv` adds material, diameter and the soil permittivity the surveyors used.
- The survey lines run parallel to the trench, about 1 m apart. Their start offsets and walking
  directions are not recorded.

Scored by `scripts/validate_on_twente.py`. Results are in the next section.

## Results on Twente

### Current: after the physics improvements (2026-10-04)

`scripts/validate_on_twente.py`, all 125 surveys: 117 with a reported permittivity (737 lines),
70 of them with a trench table that OCRs cleanly. Tables that don't OCR are left out, never guessed.

| Test | Before | Now | Chance / baseline |
|---|---|---|---|
| Survey permittivity, median error | 38.8% (fit) | **21.9%** (semblance, stacked) | 23.4% (guess the median) |
| Surveys within 25% of the reported value | 11 of 37 | 64 of 116 | 85 of 117 |
| Utilities found per line, Studio detector | 29.4% | 29.7% | 29.4% (same picks, positions scrambled) |
| Utilities found per line, migration picker | 62.6% | 62.6% | 61.9% (same picks, positions scrambled) |
| **Utilities found, picks on the GNSS map, traced on 3+ lines** | n/a | **17.4%** | 10.3% (same picks, positions scrambled) |
| Surveys where mapped picks beat the scrambled control | n/a | **22 better, 4 worse of 39** | sign test p = 0.0005 |

Held-out check for velocity: settings were chosen on half the sites and scored on the other half
(two-fold, by site). Per-arch fits scored 69.6%, gated fits 46.5%, and stacked semblance with flat
events rejected **21.7%**, against 23.4% for guessing.

### Layer 1, step 1: lines on the map (2026-10-04)

The SEG-Y traces carry GNSS positions. RadarMap writes the Dutch RD grid (metres) as float32 at
bytes 171-178, about 3 cm resolution, plus NMEA latitude/longitude where the standard keeps source
X/Y. Over 750 lines the GNSS chord matches the measuring wheel (median ratio 0.98), and site 01.1's
twelve lines come out parallel (heading 75°), 0.9-1.1 m apart, as its survey map shows. About 10%
of lines have a frozen track (one fix) and are left unplaced. Short lines lag the wheel, so a line
is placed by GNSS start and heading, with the wheel giving distance along it (`survey/mapping.py`).

With every line on one map, a survey has one unknown trench position, not one per line, so chance
matches fall; and picks can be traced the way an interpreter follows a pipe from line to line.
This is the first evidence that the automatic analysis finds real utilities:

| Picks kept | Found | Scrambled | Surveys better / worse |
|---|---|---|---|
| top 5 per line, traced on 3+ lines (the script's setting) | 17.4% | 10.3% | 22 / 4 of 39 (p = 0.0005) |
| top 15 per line, traced on 3+ lines (experiment) | 35.5% | 27.5% | 32 / 8 of 44; site-bootstrap 95% CI +5.0 to +12.1 pts |
| top 15 per line, not traced (experiment) | 39.4% | 32.5% | 35 / 8 of 44; CI +4.3 to +8.8 pts |

The gain is real but modest: most utilities are still missed. The picker was never trained on real
data, which is Layer 1's next step.

### Layer 1, step 2: velocity from trench depths: not yet possible

Two attempts, both failing for the same reason, which is useful to know:
- Matching each utility to the nearest traced pick by position alone implied permittivities of
  0.4-1.7: the nearest pick is usually shallow clutter, not the pipe.
- Scanning permittivity for the value that explains the most utilities gave nearly flat curves
  (e.g. 41-49 matches at every value on survey 06.5): chance matches dominate. The "calibrated"
  values agreed with the surveyors on 11 of 39 surveys.

Trench calibration needs stronger detection first.

### Layer 1, step 3: independent labels from the survey maps: not precise enough

Training labels must say where each excavated utility crosses each line, without using our own
picks (a detector trained on its own picks learns its own mistakes). Each survey's
`survey_map.png` is drawn from the same GNSS tracks and marks the trench as a red dashed arrow, so
the maps were registered to the tracks (scale and shift, north up): 110 of 122 register, median
misfit 0.85 px (a few cm). Then the arrow was tested as the trench's position:

- **The arrow's tail is not the trench start.** With the alignment fixed at the tail, picks found
  16.6% against 15.0% scrambled (better on 9, worse on 13 of 29 surveys).
- **The arrow is a rough guide.** The best-matching trench start lies within 2.4 m of the tail on
  17 of 29 surveys, and the arrow's direction agrees with it on 22 of 29.
- Using it as a constraint (direction fixed, start within ±2.5 m of the tail) left the lift where
  it was, about 7 points, on the 21 surveys with a usable map, truth and GNSS.

So this dataset does not locate the trench to the ±0.3 m that clean labels need. The data paper
confirms why: "Ground-truth data lacks georeferencing due to confidentiality constraints."

**Self-labelling was tried and stopped before training.** Taking each utility's position from the
best alignment on the 12 surveys where it is clearly above chance (empirical p ≤ 0.05; sites 01,
010, 04, 06) gives 123 images and 726 labels. Overlaid on the radargrams, they do not sit on the
hyperbolas. On 01.1, visible arches are about 0.2-0.3 m shallower than their labels, and several
labels fall below any visible signal. The stacked uncertainties are the trench start (about 2 m),
the drawing's depth reference (top, centre or bottom of pipe), and the soil velocity. A detector
trained on these labels would learn to fire on empty ground, so none was trained.


1. **Time zero in the hyperbola fit (a physics bug, fixed).** t² = t₀² + (2x/v)² holds for time
   measured from the ground surface. `fit_region` measured it from the start of the record, and on
   these lines the surface arrives 2.6-3.8 ns late. On a synthetic arch with 3 ns of delay that put
   velocity 11% out (~26% in permittivity). With flat events excluded, fixing it moved the fit's
   median error from 24.4% to 21.6%. The same bug affected the Studio whenever "First sample" is
   above 0, where it also misaligned the image, the fit box and picks. All three are fixed.
2. **Semblance velocity analysis (new, `studio/velocity.semblance_curve`).** The classic seismic
   method: for each trial permittivity, how coherently the signal lines up along that hyperbola.
   It uses the whole arch, not ridge points.
3. **Rejecting flat events (`is_peaked`).** The biggest single gain. Flat layers left after
   background removal look like infinitely fast hyperbolas, so semblance and the fitter both
   preferred them. Every wet-soil survey read at the bottom of the range until arches whose best
   permittivity sits at the range edge were rejected. Median error went from 27.9% to 21.7%.
4. **Stacking arches per survey (`stacked_permittivity`).** One ground, one velocity: the summed
   curves of every clear arch on every line, rather than a median of noisy per-arch answers.

### Detector trained on simulations, tested on Twente (2026-10-04): no transfer

`runs/detect/prod1`: YOLOv8n trained for 100 epochs on 24 gprMax scenes, with simulated instrument
settings matched to the company's SPR radar. On its own held-out simulated scenes it reaches
mAP50 0.40, recall 0.30. On the 44 GNSS-mapped Twente surveys, scored like the picker:

| Top 5 picks per line, traced on 3+ lines | Found | Scrambled | Surveys better / worse |
|---|---|---|---|
| Migration picker (current) | 18.4% | 11.1% | 28 / 2 (p < 0.0001) |
| Simulation-trained detector | 3.3% | 2.8% | 7 / 8 (p = 1.0) |

It is at chance on real ground, so it is **not installed** and the picker stays. Part of the gap
is the instrument: the simulations imitate a ground-coupled SPR radar, and Twente used a 500 MHz
air-launched one. So this does not prove the model is useless on the company's radar, but nothing
supports using it either. 24 scenes is also small; simulated clutter is cleaner than real soil.
The same batch did confirm the cavity rule on simulation: 2 of 2 voids were called cavities, and
0 of 5 trenches were called cavities wrongly.

### Tried and rejected, with numbers

- **Global focusing**: migrate the whole line at each velocity and keep the sharpest result.
  Flat layers and ringing dominate the sharpness measure; 4 test surveys scattered from 5 to 26.
- **Refraction at the antenna's air gap** (Snell's law; the antenna is air-launched). Real
  physics, and large on paper: a 5 cm gap makes ε 17 look like 11. But the two halves of the data
  chose different gaps (0 and 6 cm) and the held-out error rose to 26.4%, because the correction
  raised every soil about equally instead of separating wet from dry.
- **Fit and semblance must agree**: kept too few arches, 43.2%.

### What still limits accuracy

- **Velocity in wet soils.** Up to permittivity ~11 we agree with the surveyors within about 7%
  (8.16 → 8.7, 9.0 → 9.4, 11.11 → 10.7). For their 13-19.5 values we read 9-11. On synthetic
  arches the method recovers 12, 17 and 20, so it has no built-in ceiling. Either those sites'
  arches differ from the model or the reported values were set another way. Trench depths at
  matched utilities would settle it; that needs line positions.
- **Detection without line positions shows no evidence of finding utilities**: line by line, picks
  score the same as the same picks with their positions scrambled, and scoring picks by focus
  sharpness, semblance, fit agreement with the site velocity, depth persistence or combinations of
  these stayed within about 1 point of random picks. On the GNSS map (step 1 above) they do beat
  chance; the line-by-line test was too weak to see it.
- A first version of the chance control (other surveys' utility patterns) was biased: random picks
  scored 3 points *below* it. It was replaced by the scrambled-position control.

### First run, kept for the record

Before these changes, 71 surveys: detector 29.4% against a control of 32.4%; migration picker
62.6% against 64.9%; velocity median error 38.8% against 23.4% for guessing (11 of 37 within 25%).
Those controls are the biased version described above.

## IFSTTAR test site (TU1208, Nantes): downloaded; exact positions on request

X. Dérobert and L. Pajewski, *TU1208 Open Database of Radargrams: The Dataset of the IFSTTAR
Geophysical Test Site*, Remote Sensing 10(4):530, 2018; data DOI
[10.5281/zenodo.1211173](https://doi.org/10.5281/zenodo.1211173). Licence **CC BY 4.0**.

- A purpose-built 30 m pit with five fill materials (silt, limestone, gneiss gravels, a
  multilayer). Every buried object was surveyed by theodolite.
- In each pipe region, three layers of three 2.5 m pipes. The order outward from the site's
  centre line is an empty steel pipe, a water-filled PVC pipe, then an empty PVC pipe. In silt the
  layers lie at 0.80, 1.20 and 1.83 m (to the pipe top), spaced 0.5, 0.7 and 1.0 m (Figure 6).
  There are also polystyrene blocks (voids), a 500 mm concrete pipe, stones, masonry, a steel
  girder and cables.
- 67 profiles from GSSI (.dzt), MALÅ (.rd3/.rad) and IDS (.dt, plus ASCII) radars at 200-900 MHz.
  This makes it ideal test data for format readers (`parsers/dzt.py` reads the GSSI files).
- **Limit:** the paper's figures are schematics. In the 400 MHz silt line-2 profile, two shallow
  pipes drawn 0.5 m apart image about 0.38 m apart, so pipe-by-pipe labels can't be read off the
  figures. The paper offers "a complete topographical file describing ... the position of each
  buried object" on request from IFSTTAR. That file would turn this into an exact benchmark for
  depth, velocity and steel vs water-filled vs empty plastic pipe.



Abdelaziz et al., *Intelligent recognition of subsurface utilities and voids: A Ground Penetrating
Radar dataset for Deep Learning applications*, Mendeley Data, 2024. DOI
[10.17632/ww7fd9t325.1](https://doi.org/10.17632/ww7fd9t325.1). Licence **CC BY 4.0**: credit
the authors if any of it is used.

- 131 utility, 79 cavity and 75 intact profiles, plus about 2,200 augmented copies.
- Images are 224 × 224 JPEG with no time or distance scale, and no raw traces.
- Only the augmented copies are labelled (YOLO and VOC). The augmentations add heavy noise and
  rotation.
- Labels are incomplete. In `augmented_utilities/001_aug_1` only the deep hyperbola is boxed; a
  clearer one above it is not.

**Parked** because it would teach the detector another radar's appearance, at another scale, with
missing labels. It could still be useful as a cavity/intact image test, if split by original
profile (`<id>_aug_<n>` → `<id>`) so that augmented copies of one profile never sit on both sides
of the split.
