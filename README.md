# gpr-analyzer

An end-to-end pipeline for Ground Penetrating Radar (GPR) B-scan analysis:
YOLOv8 detection over a 9-class subsurface-feature taxonomy, evidence
extraction with honest confidence labelling, weighted risk scoring, and
LLM-generated findings (what/where/why/how + recommended action), streamed
live to operator/manager/PM dashboards and rolled up into an end-of-survey
PDF report.

Everything upstream of `sources/` and `parsers/` is written against a small
set of contracts (`ScanFrame`, `SourceCapabilities`, the `ScanSource` ABC) and
does not know or care where frames come from. Three sources share that
interface: `replay` (plays back a folder of files at survey speed — this is
what the whole system develops and tests against, no hardware required),
`edge_gateway` (a device that pushes/streams to a local gateway), and
`direct_device` (reading straight off the device). The latter two are wired
into the source factory but not yet implemented — see `CLAUDE.md` for why.

## Running it

This is developed on macOS but is meant to run on whatever machine the
company deploys it on (Windows or Linux, not yet confirmed) — everything is
kept OS-agnostic on purpose: `pathlib` throughout (no hardcoded path
separators or absolute paths), no forced compute device (torch/ultralytics
auto-detect CUDA/CPU; no macOS-only MPS assumption anywhere), and only
dependencies with cross-platform wheels.

```bash
uv sync --extra dev          # or: pip install -e ".[dev]"
cp .env.example .env         # fill in GROQ_API_KEY

# activate the venv directly if not using `uv run`:
source .venv/bin/activate    # macOS/Linux
.venv\Scripts\activate       # Windows

pytest                       # should collect (and, once sessions land, pass)
```

Source, thresholds, and the reasoning model are all config-driven — see
`config.yaml`. Switching from replay to a real source later is a config
change, not a code change.

### Running the API server

```bash
.venv/bin/python -m uvicorn api.server:app --reload   # default: http://127.0.0.1:8000
.venv\Scripts\python -m uvicorn api.server:app --reload   # Windows
```

REST endpoints under `/surveys` and `/lines`, plus a `/ws/live` WebSocket
feed of `finding.created`/`finding.reasoned` events. No auth today — see
`CLAUDE.md` for the accepted internal-tool trust model this assumes.

### Running the dashboard

```bash
cd dashboard
npm install
npm run dev                  # http://localhost:5173, proxies to the API at
                              # VITE_API_BASE_URL (defaults to http://127.0.0.1:8000)
npm test                     # Vitest unit tests
npm run build                # production build (tsc + vite build)
```

Three role views (`/operator`, `/manager`, `/pm`) — see `dashboard/src/views/`.
Set `VITE_API_BASE_URL` in `dashboard/.env` if the API isn't on the default
host/port. This is a separate npm project, not part of the Python package —
run the API server first so the dashboard has something to talk to.
