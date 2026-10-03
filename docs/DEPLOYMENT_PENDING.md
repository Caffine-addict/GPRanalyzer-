# Pending for deployment

What must happen before GPR Studio and the pipeline run outside this development machine.
Kept current as items close. Written 2026-10-04.

## Must have before anyone else uses it

| Item | Why | Notes |
|---|---|---|
| Authentication and user identity | The Studio is loopback-only with no login. A supervisor's sign-off is a typed name, not a verified identity. | Real accounts; record the signed-in user on every review decision. |
| HTTPS and network exposure | Today it listens on 127.0.0.1 only. | Reverse proxy with TLS; keep the cross-site write check. |
| Secrets | `GROQ_API_KEY` sits in `.env`. | A secret manager, or a local model (the reasoning layer is already behind `LLMClient`). |
| Data backup | Picks, reviews and placements are JSON files under `annotations/`. | Scheduled backup; reviews are the audit trail and future training labels. |
| Packaging | Runs from a developer virtualenv. | Container image or installer; confirm Windows if the company PC is Windows. |
| Model weights | No detector checkpoint is installed. | Install only after validation; set `ULTRALYTICS_SAFE_LOAD` (unrestricted `torch.load` today). |
| Map tiles | OpenStreetMap tiles are fetched live. | OSM's tile policy forbids heavy use; use a licensed provider or offline tiles. |

## Needed from the company or the field

| Item | Unblocks |
|---|---|
| Device streaming protocol | Live survey (Session 10): `sources/edge_gateway.py`, `sources/direct_device.py` |
| Surveyed start/end (and mid) points per line, or a fixed GPS receiver mode | Survey-grade map positions; 3 of 4 lines are location-only today |
| Labelled field data: 50-100 lines with excavated or verified utilities | Real accuracy figures; training on real data |
| One ground-truth depth per site | Depth calibration (the handbook's most accurate method) |
| EM locator readings over the same lines | Service type; PAS 128 QL-B |
| Site photographs or walkover notes | Surface-feature context for interpretation |
| Industry mentor and PIC/HOD names | Report and slide placeholders |

## Before claiming results

| Item | Status |
|---|---|
| Synthetic training batch (26 scenes) | Running; builds the dataset, checks the cavity rule and trains automatically |
| Validate apex polarity on real data | Validated on simulation only; Twente cannot test it until detection beats chance |
| Automatic detection on real data | **At chance** on 71 excavated Twente surveys (docs/EXTERNAL_DATA.md). Needs a detector trained or tuned on real lines; Twente's trench depths plus surveyed line positions would make a proper training/validation set |
| Automatic velocity on real data | **Worse than guessing** (38.8% vs 23.4% median error). Fitting is sound on a hyperbola an interpreter picks; automatic selection of hyperbolas is not. Keep velocity interpreter-picked until it is fixed |
| Validate ringing and top/bottom echo rules | Need real metal examples / water-filled pipes |
| PAS 128 grade on deliverables | Not claimable without ground truth and survey-grade positions |
| Monitoring and logging for a deployed service | Not built |
| CI: run tests, ruff and mypy on every push | Not set up |
