#!/usr/bin/env python3
"""Local browser UI to pick a job folder under Dataset/ and view its scan.

Standalone dev tool, deliberately separate from api/server.py — this
browses raw survey files sitting in a folder, not live orchestrator
surveys, so it has no business in the gated pipeline/dashboard. Re-scans
the dataset directory on every request, so a folder dropped in while the
server is running just shows up next time the page (or the job list) is
refreshed — no restart needed.

Superseded for *viewing* by GPR Studio (`python -m studio`), which adds the
processing chain, zoom, velocity fitting, and shows these same boxes as its
Candidates overlay. Still the only tool that *creates* candidate boxes.

Usage:
    .venv/bin/python scripts/spr_viewer_server.py [dataset_dir]
    # then open http://127.0.0.1:8420
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

from core import boxes as box_store
from scripts.view_spr_scan import panel_geometry, render_job_image
from studio.diagnose import diagnose_job

_CHANNEL_EXTENSIONS = ("RAD", "RA1", "RA2")

app = FastAPI(title="SPR scan viewer")
_dataset_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("Dataset/DSU_GPR_Files")


def _list_jobs() -> list[str]:
    if not _dataset_dir.exists():
        return []
    jobs = [
        d.name
        for d in sorted(_dataset_dir.iterdir())
        if d.is_dir() and any((d / f"Single-01.{ext}").exists() for ext in _CHANNEL_EXTENSIONS)
    ]
    return jobs


_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>SPR scan viewer</title>
<style>
  body {{ font-family: -apple-system, sans-serif; background: #1a1a1a; color: #eee; margin: 0; display: flex; height: 100vh; }}
  #sidebar {{ width: 200px; background: #111; padding: 12px; overflow-y: auto; box-sizing: border-box; flex-shrink: 0; }}
  #sidebar h2 {{ font-size: 14px; color: #999; margin: 0 0 10px; }}
  #jobs button {{ display: block; width: 100%; text-align: left; padding: 8px 10px; margin-bottom: 4px;
    background: #222; color: #eee; border: 1px solid #333; border-radius: 4px; cursor: pointer; font-size: 13px; }}
  #jobs button:hover {{ background: #333; }}
  #jobs button.active {{ background: #2d5f8a; border-color: #4a90d9; }}
  #main {{ flex: 1; display: flex; flex-direction: column; align-items: flex-start; padding: 16px; overflow: auto; }}
  #hint {{ color: #999; font-size: 12px; margin-bottom: 8px; }}
  #imgWrap {{ position: relative; display: inline-block; }}
  #imgWrap img {{ max-width: 1400px; display: block; border: 1px solid #333; }}
  #overlay {{ position: absolute; top: 0; left: 0; cursor: crosshair; }}
  #empty {{ color: #777; margin-top: 40px; }}
  #refresh {{ margin-bottom: 10px; width: 100%; padding: 6px; background: #333; color: #eee; border: none; border-radius: 4px; cursor: pointer; }}
  #boxPanel {{ width: 300px; background: #111; padding: 12px; overflow-y: auto; box-sizing: border-box; flex-shrink: 0; }}
  #boxPanel h2 {{ font-size: 14px; color: #999; margin: 0 0 10px; }}
  #legend {{ font-size: 11px; color: #aaa; margin-bottom: 10px; line-height: 1.8; }}
  #legend span {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: middle; }}
  .boxRow {{ background: #222; border: 1px solid #333; border-radius: 4px; padding: 6px 8px; margin-bottom: 6px; font-size: 12px; }}
  .boxRow .top {{ display: flex; justify-content: space-between; align-items: center; }}
  .boxRow button {{ background: #522; color: #f88; border: none; border-radius: 3px; cursor: pointer; padding: 2px 7px; }}
  .boxRow .diag {{ color: #999; margin-top: 4px; font-size: 11px; line-height: 1.4; }}
  .boxRow .diag .class {{ font-weight: bold; }}
</style>
</head>
<body>
  <div id="sidebar">
    <h2>Job folders ({dataset_dir})</h2>
    <button id="refresh" onclick="loadJobs()">Refresh list</button>
    <div id="jobs"></div>
  </div>
  <div id="main"><div id="empty">Select a job folder to view its scan.</div></div>
  <div id="boxPanel" style="display:none;">
    <h2 id="boxCount">Boxes (0)</h2>
    <div id="legend">
      <span style="background:#00ff88;"></span>clear_point_reflector<br>
      <span style="background:#ffee00;"></span>low_snr_point_reflector<br>
      <span style="background:#00aaff;"></span>elongated_linear_target<br>
      <span style="background:#ff5533;"></span>disturbed_zone<br>
      <span style="background:#888888;"></span>ambiguous / not yet diagnosed
    </div>
    <div id="boxList"></div>
  </div>

<script>
let current = null;
let geometry = null;
let boxes = [];
let diagnoses = {{}};  // box_id -> diagnosis

const CLASS_COLORS = {{
  clear_point_reflector: '#00ff88',
  low_snr_point_reflector: '#ffee00',
  elongated_linear_target: '#00aaff',
  disturbed_zone: '#ff5533',
}};
function colorForBox(boxId) {{
  const d = diagnoses[boxId];
  if (!d || !d.suggested_class) return '#888888';
  return CLASS_COLORS[d.suggested_class] || '#888888';
}}

async function loadJobs() {{
  const res = await fetch('/api/jobs');
  const jobs = await res.json();
  const container = document.getElementById('jobs');
  container.innerHTML = '';
  if (jobs.length === 0) {{
    container.innerHTML = '<div style="color:#777;font-size:13px;">No job folders found.</div>';
    return;
  }}
  for (const job of jobs) {{
    const btn = document.createElement('button');
    btn.textContent = job;
    if (job === current) btn.className = 'active';
    btn.onclick = () => selectJob(job);
    container.appendChild(btn);
  }}
}}

async function selectJob(job) {{
  current = job;
  document.getElementById('boxPanel').style.display = 'block';
  document.getElementById('main').innerHTML =
    '<div id="hint">Phase 1: click-drag to box a pattern (no class yet — company ground truth ' +
    'still decides that). Colors below are an automatic shape-fit SUGGESTION, not a label.</div>' +
    '<div id="imgWrap"><img id="scanImg"><canvas id="overlay"></canvas></div>';
  const img = document.getElementById('scanImg');

  const [geoRes, boxRes, diagRes] = await Promise.all([
    fetch('/api/jobs/' + encodeURIComponent(job) + '/geometry'),
    fetch('/api/jobs/' + encodeURIComponent(job) + '/boxes'),
    fetch('/api/jobs/' + encodeURIComponent(job) + '/diagnoses'),
  ]);
  geometry = await geoRes.json();
  boxes = await boxRes.json();
  diagnoses = {{}};
  for (const d of await diagRes.json()) diagnoses[d.box_id] = d;

  img.onload = () => setupOverlay(img);
  img.src = '/api/jobs/' + encodeURIComponent(job) + '/view.png?t=' + Date.now();

  loadJobs();
  renderBoxList();
}}

function canvasToNative(cx, cy) {{
  for (const ch of geometry.channels) {{
    if (cy >= ch.plot_y0 && cy < ch.plot_y0 + ch.plot_h && cx >= ch.plot_x0 && cx < ch.plot_x0 + ch.plot_w) {{
      return {{
        channel: ch.extension,
        trace: (cx - ch.plot_x0) / ch.plot_w * ch.n_traces,
        sample: (cy - ch.plot_y0) / ch.plot_h * ch.n_samples,
      }};
    }}
  }}
  return null;
}}

function nativeToScreen(channelExt, traceX, sampleY, img) {{
  const ch = geometry.channels.find(c => c.extension === channelExt);
  const cx = ch.plot_x0 + (traceX / ch.n_traces) * ch.plot_w;
  const cy = ch.plot_y0 + (sampleY / ch.n_samples) * ch.plot_h;
  return {{ x: cx * (img.clientWidth / img.naturalWidth), y: cy * (img.clientHeight / img.naturalHeight) }};
}}

function drawExisting(ctx, img) {{
  ctx.lineWidth = 1.5;
  for (const b of boxes) {{
    const p0 = nativeToScreen(b.channel, b.x, b.y, img);
    const p1 = nativeToScreen(b.channel, b.x + b.w, b.y + b.h, img);
    ctx.strokeStyle = colorForBox(b.id);
    ctx.strokeRect(p0.x, p0.y, p1.x - p0.x, p1.y - p0.y);
  }}
}}

function setupOverlay(img) {{
  const canvas = document.getElementById('overlay');
  canvas.width = img.clientWidth;
  canvas.height = img.clientHeight;
  canvas.style.width = img.clientWidth + 'px';
  canvas.style.height = img.clientHeight + 'px';
  const ctx = canvas.getContext('2d');
  drawExisting(ctx, img);

  let start = null;
  canvas.onmousedown = (e) => {{
    const rect = canvas.getBoundingClientRect();
    start = {{ x: e.clientX - rect.left, y: e.clientY - rect.top }};
  }};
  canvas.onmousemove = (e) => {{
    if (!start) return;
    const rect = canvas.getBoundingClientRect();
    const cur = {{ x: e.clientX - rect.left, y: e.clientY - rect.top }};
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawExisting(ctx, img);
    ctx.strokeStyle = '#ffcc00';
    ctx.lineWidth = 1;
    ctx.strokeRect(Math.min(start.x, cur.x), Math.min(start.y, cur.y), Math.abs(cur.x - start.x), Math.abs(cur.y - start.y));
  }};
  canvas.onmouseup = async (e) => {{
    if (!start) return;
    const rect = canvas.getBoundingClientRect();
    const end = {{ x: e.clientX - rect.left, y: e.clientY - rect.top }};
    const startPt = start;
    start = null;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawExisting(ctx, img);

    const sx = img.naturalWidth / img.clientWidth;
    const sy = img.naturalHeight / img.clientHeight;
    const n0 = canvasToNative(startPt.x * sx, startPt.y * sy);
    const n1 = canvasToNative(end.x * sx, end.y * sy);
    if (!n0 || !n1 || n0.channel !== n1.channel) return;

    const x = Math.min(n0.trace, n1.trace);
    const y = Math.min(n0.sample, n1.sample);
    const w = Math.abs(n1.trace - n0.trace);
    const h = Math.abs(n1.sample - n0.sample);
    if (w < 1 || h < 1) return;

    const note = prompt('Optional note (phase 1: no class yet):', '') || '';
    const res = await fetch('/api/jobs/' + encodeURIComponent(current) + '/boxes', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ channel: n0.channel, x, y, w, h, note }}),
    }});
    if (res.ok) {{
      boxes.push(await res.json());
      drawExisting(ctx, img);
      renderBoxList();
    }}
  }};
}}

function renderBoxList() {{
  document.getElementById('boxCount').textContent = 'Boxes (' + boxes.length + ')';
  const el = document.getElementById('boxList');
  el.innerHTML = '';
  for (const b of boxes) {{
    const d = diagnoses[b.id];
    const row = document.createElement('div');
    row.className = 'boxRow';

    const top = document.createElement('div');
    top.className = 'top';
    const span = document.createElement('span');
    span.textContent = b.channel + ' (' + b.x.toFixed(0) + ',' + b.y.toFixed(0) + ')';
    span.style.color = colorForBox(b.id);
    const del = document.createElement('button');
    del.textContent = 'x';
    del.onclick = async () => {{
      await fetch('/api/jobs/' + encodeURIComponent(current) + '/boxes/' + b.id, {{ method: 'DELETE' }});
      boxes = boxes.filter(x => x.id !== b.id);
      const img = document.getElementById('scanImg');
      const canvas = document.getElementById('overlay');
      if (img && canvas) {{
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        drawExisting(ctx, img);
      }}
      renderBoxList();
    }};
    top.appendChild(span);
    top.appendChild(del);
    row.appendChild(top);

    if (b.note) {{
      const noteEl = document.createElement('div');
      noteEl.className = 'diag';
      noteEl.textContent = b.note;
      row.appendChild(noteEl);
    }}

    if (d) {{
      const diagEl = document.createElement('div');
      diagEl.className = 'diag';
      let html = '<span class="class">' + (d.suggested_class || 'ambiguous') + '</span>';
      if (d.fit_r2 !== null) html += ' — hyperbola fit R²=' + d.fit_r2.toFixed(2) + ' (' + d.n_ridge_inliers + '/' + d.n_ridge_total + ' pts)';
      html += '<br>coherence=' + d.local_coherence.toFixed(2) + ' aspect=' + d.aspect_ratio.toFixed(1) + ' amp=' + d.peak_amplitude.toFixed(1);
      if (d.implied_dielectric !== null) html += '<br>implied ε=' + d.implied_dielectric.toFixed(1) + ' (v=' + d.velocity_m_per_ns.toFixed(3) + ' m/ns)';
      html += '<br><em>' + d.rationale + '</em>';
      diagEl.innerHTML = html;
      row.appendChild(diagEl);
    }}

    el.appendChild(row);
  }}
}}

loadJobs();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _PAGE.format(dataset_dir=_dataset_dir)


@app.get("/api/jobs")
def list_jobs() -> list[str]:
    return _list_jobs()


def _require_known_job(job_name: str) -> Path:
    if job_name not in _list_jobs():
        raise HTTPException(status_code=404, detail=f"unknown job folder: {job_name!r}")
    return _dataset_dir / job_name


@app.get("/api/jobs/{job_name}/view.png")
def view_job(job_name: str) -> Response:
    job_dir = _require_known_job(job_name)
    try:
        image = render_job_image(job_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise HTTPException(status_code=500, detail="failed to encode image")
    return Response(content=encoded.tobytes(), media_type="image/png")


@app.get("/api/jobs/{job_name}/geometry")
def get_geometry(job_name: str) -> dict:
    job_dir = _require_known_job(job_name)
    try:
        return panel_geometry(job_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/jobs/{job_name}/boxes")
def list_boxes(job_name: str) -> list[box_store.Box]:
    _require_known_job(job_name)
    return box_store.load_boxes(job_name)


@app.post("/api/jobs/{job_name}/boxes")
def create_box(job_name: str, payload: dict = Body(...)) -> box_store.Box:  # noqa: B008 - FastAPI DI pattern
    _require_known_job(job_name)
    try:
        return box_store.add_box(
            job_name,
            channel=payload["channel"],
            x=float(payload["x"]),
            y=float(payload["y"]),
            w=float(payload["w"]),
            h=float(payload["h"]),
            note=payload.get("note", ""),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid box payload: {exc}") from exc


@app.get("/api/jobs/{job_name}/diagnoses")
def get_diagnoses(job_name: str) -> list[dict]:
    job_dir = _require_known_job(job_name)
    from dataclasses import asdict

    return [asdict(d) for d in diagnose_job(job_dir)]


@app.delete("/api/jobs/{job_name}/boxes/{box_id}")
def remove_box(job_name: str, box_id: str) -> dict:
    _require_known_job(job_name)
    if not box_store.delete_box(job_name, box_id):
        raise HTTPException(status_code=404, detail=f"unknown box id: {box_id!r}")
    return {"deleted": box_id}


if __name__ == "__main__":
    import uvicorn

    print(f"watching dataset dir: {_dataset_dir.resolve()}")
    # reload=True: this is a dev-iteration tool being actively edited — restart
    # automatically on save instead of silently serving stale code until the
    # process is killed by hand and rerun.
    uvicorn.run("scripts.spr_viewer_server:app", host="127.0.0.1", port=8420, reload=True)
