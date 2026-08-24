# gpr-analyzer

**In one sentence:** this software looks at Ground Penetrating Radar scans
(a way of "seeing" what's buried underground without digging), automatically
spots things like buried pipes, voids, or disturbed soil, explains what it
found in plain language, flags how urgent each one is, and shows all of that
on screen while the survey is still happening — plus a summary PDF at the
end.

## What is this, really?

**Ground Penetrating Radar (GPR)** is a tool surveyors drag or push across
the ground. It sends radar pulses into the soil and records what bounces
back, producing an image called a **B-scan** — think of it like an ultrasound
for the ground. Reading those images by eye and telling a buried pipe apart
from a rock or a patch of noise normally takes a trained specialist and a
lot of time.

This project automates that reading process, end to end:

1. **Detect** — an AI model trained on real GPR images scans each B-scan and
   marks anything that looks like one of 9 known feature types (see below).
2. **Assess** — each detection gets a plain-language explanation ("what is
   this, where is it, why do we think so, how confident are we") from an AI
   assistant, plus an automatic risk level: **LOW**, **MEDIUM**, or **HIGH**.
3. **Show** — everything above shows up live, on a web dashboard, while the
   survey is still in progress — no waiting until the end to find out
   something risky was just detected.
4. **Report** — at the end of a survey, a PDF summary is generated
   automatically, listing everything found, sorted by how urgent it is.

Three kinds of people can use the dashboard, each seeing a view suited to
their job:

- **Operator** — the person doing the survey on-site. Starts/stops a survey
  and watches results appear live as they walk the ground.
- **Manager** — reviews every survey that's been run, drills into any one
  of them to see the full list of what was found.
- **PM** (project manager) — checks how one specific patch of ground has
  looked across *every* time it's been surveyed, to spot changes over time.

One more thing that matters a lot here: **the system never guesses and
pretends it measured something.** If it can't be sure of a number (say, how
deep something is buried), it says so honestly — "unavailable" — instead of
showing a number that looks precise but isn't. Every figure shown is labelled
as either **calibrated** (a real measurement), **estimated** (a rough guess,
clearly marked as such), or **unavailable** (we genuinely don't know). That
same honesty applies to the project itself: as of today, there's no trained
detection model included yet and no real radar hardware connected — see
[Project status](#project-status) below for exactly what's built and running
versus what's still waiting on real-world data.

---

The rest of this document goes deeper for anyone setting the project up,
extending it, or reviewing the code.

**In more technical terms:** an end-to-end pipeline for GPR B-scan analysis —
YOLOv8 detection over a 9-class subsurface-feature taxonomy, evidence
extraction with honest confidence labelling, weighted risk scoring, and
LLM-generated findings (what/where/why/how + recommended action), streamed
live to operator/manager/PM dashboards and rolled up into an end-of-survey
PDF report.

This is a rebuild, not an extension, of an earlier prototype (see
[`docs/PRIOR_ART.md`](docs/PRIOR_ART.md) for why) — three copies of the same
script with no shared contracts is not something you extend, it's something
you replace.

## Contents

- [What is this, really?](#what-is-this-really)
- [Why it's built this way](#why-its-built-this-way)
- [Architecture](#architecture)
- [The 9-class taxonomy](#the-9-class-taxonomy)
- [Project layout](#project-layout)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Running the API server](#running-the-api-server)
- [Running the dashboard](#running-the-dashboard)
- [Generating a report](#generating-a-report)
- [Heartbeat scripts](#heartbeat-scripts)
- [Testing](#testing)
- [Project status](#project-status)
- [Known limitations](#known-limitations)

## Why it's built this way

Three design decisions shape everything else in this repo:

1. **The source seam.** Nothing is known yet about the real edge device —
   protocol, whether it streams live or exports files, whether depth/position
   are calibrated. So everything upstream of `sources/` and `parsers/` is
   written against a small, fixed contract (`ScanFrame`, `SourceCapabilities`,
   the `ScanSource` ABC) and genuinely does not know or care which concrete
   source produced a frame. When the real device spec eventually arrives,
   wiring it in should only ever touch `sources/` — if it doesn't, the seam
   has failed and needs fixing, not working around.

2. **Never fabricate a value.** Every field in `Evidence` that depends on
   calibration (depth, position, amplitude) carries its own confidence label:
   `"calibrated"`, `"estimated"`, or `"unavailable"`. A value is `None` if
   and only if its confidence is `"unavailable"` — enforced at the dataclass
   level, not just by convention. A plausible-looking number is worse than no
   number at all, because it looks like a measurement. This shows up
   everywhere downstream too: the LLM reasoning prompt never states an
   "estimated" figure as if it were fact, the dashboard renders `unavailable`
   in muted italics instead of a bare `0`, and the PDF report does the same.

3. **The fast path never waits on reasoning.** A detection becomes a
   `Finding` and is persisted and emitted (`finding.created`) immediately —
   risk scoring is fast and local. The LLM call is a *second*, asynchronous
   pass: it runs in the background, updates the same `Finding` when it
   completes, and emits again (`finding.reasoned`). A `Finding` must survive
   and remain useful even if reasoning never completes — no trained model, no
   API key, and no network are hard requirements for the core pipeline to
   produce real output.

## Architecture

`pipeline/orchestrator.py` drives a linear per-frame pipeline, then splits
into a fast path (persist + emit immediately) and a slow, asynchronous
reasoning path that updates the same finding later:

```
sources/ -> parsers/ -> preprocess/ -> detect/ -> evidence/ -> risk/
                                                                  |
                                                  emit "finding.created"
                                                                  v
                                                            store/ (DuckDB)
                                                                  ^
                                                                  |
                                                  emit "finding.reasoned"
                                                                  |
                                                       reason/ (Groq, async)
```

Everything else reads from the store rather than sitting in that pipeline
directly:

```
store/ -> api/ (FastAPI + WebSocket) -> dashboard/ (React, 3 role views)
store/ -> reports/ (end-of-survey PDF)
```

`pipeline/orchestrator.py` is where every package upstream of it meets for
the first time: it pulls frames from a `ScanSource`, runs them through
preprocess → detect → evidence → risk, persists and emits the fast-path
result, then dispatches reasoning as a background `asyncio.Task` that
updates the store and emits again when it lands.

## The 9-class taxonomy

The detection model looks for 9 specific patterns in a scan. In plain terms:
a buried void (**cavities**), something long and straight like a pipe or
cable (**elongated_linear_target**), a pipe crossing near another buried
object (**intersecting_linear_and_point_reflector**), a strong single
reflection typical of metal or a large solid object
(**strong_high_contrast_reflector**), several separate small objects close
together (**multiple_point_reflectors**), a faint, uncertain single-object
signal (**low_snr_point_reflector**, "SNR" = signal-to-noise ratio — how
much the real signal stands out from background noise), a messy area with
many overlapping signals (**cluttered_multi_target**), an area where the
soil itself looks disturbed rather than a distinct object
(**disturbed_zone**), and a clean, unambiguous single-object signal
(**clear_point_reflector**). These are the exact class names used in the
code and config:

```
cavities                                    elongated_linear_target
intersecting_linear_and_point_reflector     strong_high_contrast_reflector
multiple_point_reflectors                   low_snr_point_reflector
cluttered_multi_target                      disturbed_zone
clear_point_reflector
```

This supersedes the coarser class set used by the original prototype (see
`docs/PRIOR_ART.md`) — pseudo-labelled into finer-grained classes. One
consequence: `risk.escalation.utility_classes` in `config.yaml` (which
classes count as "utility-like buried infrastructure" for the
cavities-plus-utility escalation rule) is a judgement call under the new
taxonomy, since the original "utilities" class no longer exists as such —
worth a domain-expert sanity check before relying on it operationally.

## Project layout

```
core/        contracts.py, config.py — the types and config everything else depends on
sources/     ScanSource ABC + replay/edge_gateway/direct_device implementations
parsers/     file-format registry (extension -> parser), e.g. image.py
preprocess/  enhance.py — bilateral filter, CLAHE, optional NL-means
detect/      model.py — YOLOv8 inference wrapper
render/      bscan.py — traces -> normalised image (uncalibrated, logs as such)
evidence/    extract.py — Detection + ScanFrame + SourceCapabilities -> Evidence
risk/        score.py — weighted score, thresholds, escalation rules
reason/      schema.py, engine.py (Groq), prompts/v1_finding.txt
store/       Store interface + duckdb_store.py — no DuckDB calls outside this module
pipeline/    orchestrator.py — fast path emits immediately, reasoning is async
api/         server.py (FastAPI + WebSocket), survey_manager.py, connection_manager.py, schemas.py
reports/     generate.py — end-of-survey PDF with confidence-derived caveats
dashboard/   React + TS + Vite, three role views — separate npm project
scripts/     heartbeat scripts — run real (or replay) data through each layer end-to-end
tests/       mirrors the package layout, 349 tests
docs/        PRIOR_ART.md (why this was rebuilt), INTEGRATION.md (written once hardware answers arrive)
```

Every package above `sources/`/`parsers/` only ever imports `core.contracts`
types from below it — there's no back-reference from, say, `risk/` into
`detect/`.

## Getting started

Requires Python 3.12+. Managed with [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev          # or: pip install -e ".[dev]"
cp .env.example .env         # fill in GROQ_API_KEY (optional — see below)

# activate the venv directly if not using `uv run`:
source .venv/bin/activate    # macOS/Linux
.venv\Scripts\activate       # Windows

pytest                       # 349 tests, should all pass
```

Nothing above requires trained YOLO weights or a Groq API key — both are
optional. Without weights, `detect/model.py` raises a well-typed
`ModelNotFoundError` that the orchestrator catches per-frame (the frame's
metadata is still persisted, detection is just skipped for that frame).
Without `GROQ_API_KEY`, the reasoning engine is simply not constructed and
every `Finding` stays on the fast path only. Both are the *designed*
degraded path, not a bug — see the heartbeat scripts below for it in action.

This is developed on macOS but built to run on whatever machine the
deployment target ends up being (Windows or Linux, not yet confirmed):
`pathlib.Path` throughout (no hardcoded path separators or absolute paths),
no forced compute device (`torch`/`ultralytics` auto-detect CUDA/CPU — no
macOS-only MPS assumption anywhere), and only dependencies with
cross-platform wheels.

## Configuration

Everything tunable lives in [`config.yaml`](config.yaml) — detection
thresholds, enhancement parameters, risk weights and escalation rules, the
reasoning model and prompt version, source selection, latency targets, the
storage backend, and the API's CORS allowlist. `core/config.py` fails loudly
on anything missing — there are no silent defaults scattered through the
code, so a bad or incomplete config is caught at startup, not at some
downstream call site.

Switching the active data source is a one-line config change
(`source.type: replay | edge_gateway | direct_device`), not a code change —
that's the whole point of the source seam.

## Running the API server

```bash
.venv/bin/python -m uvicorn api.server:app --reload   # default: http://127.0.0.1:8000
.venv\Scripts\python -m uvicorn api.server:app --reload   # Windows
```

| Method | Path                          | Description                                      |
|--------|-------------------------------|---------------------------------------------------|
| GET    | `/surveys`                    | List every survey this server has run             |
| POST   | `/surveys/{survey_id}/start`  | Start a survey against the configured source       |
| POST   | `/surveys/{survey_id}/stop`   | Stop a running survey                              |
| GET    | `/surveys/{survey_id}/findings` | All findings for one survey                      |
| GET    | `/surveys/{survey_id}/summary`  | Aggregate stats (counts by risk level and class) |
| GET    | `/lines/{line_id}/findings`   | Findings for one physical location, across *every* survey that covered it |
| WS     | `/ws/live`                    | Live feed of `finding.created` / `finding.reasoned` events |

There's no authentication today — an accepted internal-tool trust model, not
an oversight. CORS is a real allowlist (`config.yaml`'s `api.cors_origins`),
not a wildcard, since with no auth the browser's same-origin policy is the
only thing preventing an unrelated webpage from reaching this API through an
operator's own browser.

## Running the dashboard

```bash
cd dashboard
npm install
npm run dev                  # http://localhost:5173
npm test                     # Vitest unit tests, 63 tests
npm run build                # production build (tsc + vite build)
```

A separate npm project (not part of the Python package), dependency-light by
design — native `fetch`/`WebSocket`, no state-management or data-fetching
library. Three routed views:

- **`/operator`** — start/stop a survey, watch a live append-only feed of
  findings as they're detected and reasoned.
- **`/manager`** — browse every survey (running or finished), drill into
  one's summary and full findings table.
- **`/pm`** — cross-survey history for one physical line (`line_id`): how has
  this location trended across every time it's been surveyed.

Set `VITE_API_BASE_URL` in `dashboard/.env` if the API isn't on the default
`http://127.0.0.1:8000`. Run the API server first — the dashboard has
nothing to show without it.

## Generating a report

```python
from reports.generate import generate_report
from store.duckdb_store import DuckDBStore

store = DuckDBStore("output/gpr.duckdb")
generate_report("survey-id", store, "output/survey-id.pdf")
```

Produces a landscape-orientation PDF: a summary table (total findings, by
risk level, by class), a confidence-caveats section (a per-field
calibrated/estimated/unavailable breakdown across every finding — derived
from the findings themselves rather than a separate stored capabilities
record), and the full findings table sorted highest-risk-first. A survey
with zero findings still produces a real, honest PDF stating that.

## Heartbeat scripts

Each layer has a script under [`scripts/`](scripts/) that runs it against
real (or replayed) data end-to-end — useful for confirming a layer actually
works, or as a template for wiring in the next one:

| Script | What it proves |
|---|---|
| `replay_heartbeat.py` | `ReplaySource` alone: plays back a fixture directory as `ScanFrame`s. |
| `pipeline_heartbeat.py` | preprocess → detect → evidence → risk, called directly (no orchestrator). |
| `orchestrator_heartbeat.py` | The full async orchestrator against replayed data, degrading gracefully with no trained weights and no API key. |
| `api_heartbeat.py` | The FastAPI server as a real subprocess with a real external WebSocket client — not the in-process TestClient. |
| `report_heartbeat.py` | Runs the orchestrator to populate a real store, then generates a real PDF from it. |

```bash
.venv/bin/python scripts/orchestrator_heartbeat.py
```

## Testing

```bash
pytest                                                     # 349 tests
pytest --cov=core --cov=sources --cov=parsers --cov=preprocess \
       --cov=detect --cov=render --cov=evidence --cov=risk \
       --cov=reason --cov=store --cov=pipeline --cov=api --cov=reports
cd dashboard && npm test                                   # 63 tests
```

100% line coverage on every implemented Python package and on the
dashboard — but the coverage number itself isn't the point. Every session
of this project's development ran mutation testing (deliberately break a
line, confirm the test suite actually fails, then revert) rather than
trusting coverage percentage alone, and it found real gaps almost every
time — the kind where a fixture varies multiple fields together and hides a
column-swap bug, or an assertion checks "not None" instead of an exact
value. `ruff` and `mypy` are clean; the dashboard's `tsc`/`oxlint` are clean.

## Project status

Sessions 0–9 of the original build sequence are complete, reviewed, and
gated — full detail (including every bug found and fixed along the way) in
[`CLAUDE.md`](CLAUDE.md). In short: core contracts and config, the source
abstraction, preprocessing, YOLOv8 detection, evidence extraction, risk
scoring, LLM reasoning, DuckDB storage, the async orchestrator, the
FastAPI/WebSocket API, the dashboard, and PDF reporting are all built and
tested.

**Session 10 (hardware integration) is blocked**, not next-in-queue: it
requires real device specs (protocol, streaming vs. file export, calibration
details) that haven't been provided yet, and guessing them is explicitly
against this project's own source-seam rule. `sources/edge_gateway.py` and
`sources/direct_device.py` are structurally wired into the source factory
already — `capabilities()` returns honest conservative values — but
`frames()` raises `NotImplementedError` until a real spec arrives.

## Known limitations

- **No authentication anywhere in the API.** Deliberate for now (internal
  tool, single trusted deployment) — would need real work before this is
  reachable from anywhere untrusted.
- **`detect/model.py`'s `YOLO(...)` does an unrestricted `torch.load()`** —
  the standard pickle deserialization surface. Low risk today (weights path
  only ever comes from trusted local config, and no real weights exist yet),
  but worth revisiting (`ULTRALYTICS_SAFE_LOAD`) before a real checkpoint is
  trained and deployed.
- **The dashboard's live feed can't merge `finding.created`/`finding.reasoned`
  events for the same finding** — the WebSocket payload doesn't carry a
  storage-layer ID (by design: `Finding` is a pure domain object). It shows
  both events as separate log entries instead. Fixable in a small, low-risk
  follow-up if a merged view is ever needed.
- **Reports aren't wired into the API yet** — `reports/generate.py` is built
  and fully tested standalone, but there's no `/surveys/{id}/report`
  endpoint calling it yet.
