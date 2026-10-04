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

Held-out check for velocity: settings were chosen on half the sites and scored on the other half
(two-fold, by site). Per-arch fits scored 69.6%, gated fits 46.5%, and stacked semblance with flat
events rejected **21.7%**, against 23.4% for guessing.

### What changed, and what each change was worth

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
- **Detection shows no evidence of finding utilities.** Picks score the same as the same picks with
  their positions scrambled. The test itself is weak, because line offsets are unknown and the
  offset search matches anything, but there is no signal for it to find. Scoring picks by focus
  sharpness, semblance, fit agreement with the site velocity, cross-line depth persistence, and
  combinations of these did not change that: every score stayed within about 1 point of random picks.
- A first version of the chance control (other surveys' utility patterns) was biased: random picks
  scored 3 points *below* it. It was replaced by the scrambled-position control.

### First run, kept for the record

Before these changes, 71 surveys: detector 29.4% against a control of 32.4%; migration picker
62.6% against 64.9%; velocity median error 38.8% against 23.4% for guessing (11 of 37 within 25%).
Those controls are the biased version described above.

## Mendeley utilities and voids (Morocco): evaluated, parked

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
