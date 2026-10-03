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

Full run, 2026-10-04: 71 of the 125 surveys scored, 458 lines. The other 54 were skipped because
their trench table did not OCR into usable distances and depths; they are left out, never guessed.

| Test | Ours | Chance / baseline | Reading |
|---|---|---|---|
| Utilities found per line, Studio detector | 29.4% | 32.4% | No better than chance |
| Utilities found per line, migration picker | 62.6% | 64.9% | No better than chance |
| Survey permittivity, median error | 38.8% | 23.4% (guess the dataset median) | Worse than guessing |
| Surveys within 25% of the reported permittivity | 11 of 37 | 30 of 37 | Worse than guessing |
| Rank correlation, fitted vs reported permittivity | 0.26 | 0 | Weak |

**What this says, plainly:** on real, excavated sites, the Studio's automatic analysis does not yet
beat chance. The fitter itself is sound. On a clear hyperbola it returns the surveyors' value
(01.1 Path7: 9.0 against 9.0, R² 0.99), and other clear hyperbolas on that site give 7.6 to 11.1.
What fails is choosing *which* hyperbolas to trust: automatically proposed boxes include clutter,
ringing and partial arches.

**The detection test is weak by construction.** Line offsets are unknown, so the offset and
direction search also matches clutter. That is why the control is so high (32% to 65%). A positive
result would need line positions relative to the trench. Even so, neither method beats its
control, so there is no evidence yet that the automatic picks find utilities.

What follows from it:
- Material calls from polarity cannot be assessed here, since they need matched utilities. They stay
  at low confidence.
- An interpreter's chosen hyperbola is still the reliable velocity source. The Studio's manual pick
  plus fit is what was validated (9.0 vs 9.0); automatic velocity is not.
- Next steps are in `DEPLOYMENT_PENDING.md`.

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
