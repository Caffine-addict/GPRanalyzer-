# Training a learned detector

Research done 2026-10-09 (three parallel literature reviews: how to label, what is what, and what
published results really show). Sources are cited in each section. Nothing here was taken on trust
from a paper's headline number: test conditions are given with every figure.

## What the evidence says

**Simulation alone does not transfer to real ground.** Our YOLOv8n trained on 24 gprMax scenes
(`runs/detect/prod1`) reached mAP50 0.40 on simulation and was at chance on real Twente surveys
(3.3% vs 2.8%). The one published held-out-site test agrees: YOLOv5s trained on 300 gprMax scenes
scored mAP50 **0.031** on an unseen real site; 125 real images gave 0.494; simulation pretraining
then real fine-tuning gave **0.591** (TE-S2R, arXiv 2506.06311, preprint, 11 test images).

**Label the apex, not the hyperbola's extent.** No paper ablates box extent; published practice is
apex-centred boxes or apex keypoints (Li et al., Sensors 2020; Sensors 25:6414, 2025, keypoint F1
0.80-0.82 on one road). An apex stays defined where hyperbolas overlap, where our old 15%-of-peak
box rule broke, and it is the same unit as our tags, the picker and the crews' dig points.

**Real labels: the picker proposes, a person verifies.** The smallest field-validated successes used
about 85-140 real labelled hyperbolas. Target 150-300 verified apexes from at least 4 of the 6
sites. Rejected picks are free hard negatives. The Studio's review already records both.

**Most published accuracies are inflated.** 0.83-0.90 mAP headlines come from one site with random or
parallel-line splits; none is checked against excavations. A PAS 128 client guide puts a whole
expert survey (GPR plus EM locator) at roughly 80-95% of utilities detected, and states that
geophysical data alone does not identify a utility.

**What GPR can tell apart (no deep classifier yet).** Continuity across parallel lines (linear
utility vs isolated object), echo polarity relative to the direct wave (denser than soil: metal,
water; lighter: air), depth, sometimes size (top-to-bottom echo of water-filled plastic only). It
cannot tell water from gas from sewer, or power from telecom cable. Polarity alone mislabels an
empty plastic duct as a cavity: continuity is what separates them (`detect/refine.py` to fix).

## The plan

1. **Pretrain on composites (done 2026-10-09, `simulate/composite.py`).** Real RAD stretches with no
   strong picker target, plus point-reflector echoes: analytic hyperbolas built with this radar's own
   direct-wave pulse (85%) and the few clean gprMax crops (15%). Apex labels from the geometry,
   fixed 0.6 m x 3.2 ns boxes. Sites Set3-5 (all 23 tags) never used; Set2 validates.
2. **Collect verified real apexes** through the Studio review (target 150-300, 4+ sites).
3. **Fine-tune** on them; evaluate leave-one-site-out, matching by apex distance (0.3 m, 1.5 ns),
   with the scrambled/random-position chance control, recall and false alarms per metre.
4. **Install only if** (under leave-one-site-out on real data): paired recall gain over the frozen
   picker with the 95% CI lower bound above 0 on 100+ verified targets, false alarms per metre no
   worse, no site worse by more than one target, and it beats the chance control on held-out
   Twente sites. Otherwise the picker stays and the model runs as a second opinion only.

## Result of step 1 (2026-10-09): does not beat the picker

YOLOv8n, 4,000 composite images (27 clean stretches of Job/Set1 ground), validated on Set2
composites; ran 35 epochs (early stop). Scored on the 23 held-out tags exactly like the picker
(RAD, 15 per line, 0.3 m and 1.5 ns, random-position chance):

| Model | Tags found | Chance |
|---|---|---|
| Final model, epoch 15 (chosen by validation) | **8/23 (35%)** | 9% |
| Epoch 2 checkpoint, looked at mid-training | 16/23 (70%) | 22% |
| Migration picker (RAD) | 10/23 (43%) | 14% |

The validation score kept rising (mAP50 0.93) while real recall fell: the model learned the pasted
echoes' look. The epoch-2 figure is not usable: choosing a checkpoint by the tags would make them a
tuning set. Conclusion: not installed; the picker stays. A real validation set (verified apexes from
training sites, step 2) is required before any checkpoint can be chosen honestly.

## Honest limits of step 1

- The 23 tags are a development set: about half show no clear echo, and n=23 gives a 95% CI of
  roughly ±17 points. Telling 74% from 85% needs about 70-135 paired verified targets.
- Background stretches still hold real arches weaker than the picker's strength 5: label noise.
- Analytic hyperbolas are geometry plus the radar's pulse, not full-wave physics.
