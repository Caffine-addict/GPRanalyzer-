# GPR Analyzer

Software that turns ground-penetrating-radar (GPR) scans and vendor survey drawings into checkable
data, so engineers can locate buried utilities before they dig.

Built during an AI engineering internship with Puravankara Ltd. under Dayananda Sagar University's
"Lab to Land: AI for Sustainable Construction" programme.

## What it does

| Part | What it does |
|---|---|
| **GPR Studio** (`studio/`) | Interpretation workstation for recorded radar lines: processing chain (time-zero, dewow, background removal, band-pass, Kirchhoff migration, gain, stacking), hyperbola picking with a RANSAC velocity fit, and depth read-outs. |
| **Drawing extraction** (`reference/`) | Reads utility call-outs (type, depth, chainage, geo points) from vendor survey PDFs, with OCR for scanned sheets that have no text layer. |
| **AI assistant** (`reason/`, `studio/assistant.py`) | An LLM (Groq, `gpt-oss`, strict JSON output) explains a measured target in plain language. Every claim stays *proposed* until a named supervisor confirms or rejects it, and a review report PDF records each decision. |
| **Maps** (`studio/georef.py`, `studio/geo_export.py`) | Places lines on a map from chainage plus surveyed reference points, and exports findings as GeoJSON, KML and DXF for GIS and CAD users. |
| **Live pipeline** (`sources/`, `pipeline/`, `api/`, `dashboard/`) | Source → preprocess → detect → evidence → risk → store, streamed over FastAPI + WebSocket to operator, manager and PM views, with an end-of-survey PDF report. |
| **Synthetic data** (`simulate/`) | Random subsurface scenes simulated with gprMax (in Docker) and rendered into a labelled detector dataset, because no labelled real radar data exists. |

## Measured results

Every figure below was measured on real data, none estimated:

- **Drawing extraction:** 1,743 utility call-outs read from vendor survey drawings, 360 of them from
  scanned sheets via OCR (94% precision, 69% recall, scored against sheets with a text layer).
- **Velocity check:** hyperbola fits on the vendor's own radargrams gave a median soil dielectric of
  7.2, against the 7.3 the vendor reported.
- **Placement:** a synthetic end-to-end test put targets on the map within 5.5 mm of their true
  positions.
- **Public benchmark** ([University of Twente](https://doi.org/10.4121/96303227-5886-41c9-8607-70fdd2cfe7c1.v1),
  125 surveys with trench ground truth): picks placed on the GNSS map and traced across 3+ lines
  found 17.4% of utilities against 10.3% for the same picks with scrambled positions (sign test
  p = 0.0005). Permittivity estimates are still only slightly better than guessing the median
  (21.9% vs 23.4% median error). Full numbers, including what failed: [`docs/EXTERNAL_DATA.md`](docs/EXTERNAL_DATA.md).

## Design rules

- **Never fabricate a value.** Every depth, position and amplitude is labelled `calibrated`,
  `estimated` or `unavailable`. A missing number stays missing.
- **The source seam.** Everything downstream is written against one `ScanSource` interface, so a
  replayed file, a streaming gateway or a direct device read are interchangeable by config.
- **The fast path never waits on the LLM.** Detections are stored and broadcast first; reasoning
  arrives later and is optional.
- **A human signs off.** The assistant can only refer to targets the software measured, and nothing
  it says is final until a supervisor decides.

## Getting started

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/). Storage and sign-in use Postgres,
started from `compose.yaml`.

```bash
uv sync --all-extras          # or: pip install -e ".[dev,sim,build]"
cp .env.example .env          # GROQ_API_KEY is optional; set the Postgres password and URLs
docker compose up -d          # Postgres on 127.0.0.1
python -m studio.users        # create a sign-in account
python -m studio              # GPR Studio at http://127.0.0.1:8500
pytest                        # ~1,300 tests; the storage and sign-in tests need Postgres running
```

The live API runs with `python -m uvicorn api.server:app`, and the dashboard with
`npm install && npm run dev` inside `dashboard/`. Without a Groq key the assistant answers 503 and
says why; without trained weights detection is skipped per frame. Both are designed paths, not
failures.

## Limits, stated plainly

- **No trained detector on real lines.** No labelled radar data exists, so detection on real
  data is classical signal processing (hyperbola fitting) mapped to a 9-class rule-based taxonomy.
  The synthetic-data detector has not been validated on real ground.
- **Utility type can't be read from radar alone.** Output gives depth plus a hypothesis of what the
  object could be, never a confirmed identity.
- **Onboard GPS was unreliable** on three of four survey lines, so lines are placed by chainage and
  surveyed reference points instead.
- **Live hardware isn't wired yet.** The streaming and direct-device sources are structurally in
  place but wait on the device protocol.

## Stack

Python 3.12 · FastAPI · WebSocket · Postgres (psycopg 3) · OpenCV · NumPy · Groq (`gpt-oss`) ·
pdfplumber + OCR · pyproj · ezdxf · reportlab · gprMax (Docker) · YOLOv8 · React + TypeScript + Vite
· Leaflet
