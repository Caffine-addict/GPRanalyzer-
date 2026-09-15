/* The radargram canvas: rulers, image, overlays, and the data<->screen transform.
 *
 * The image is always fetched at the channel's *native* size (386 x 256 or so —
 * a few tens of KB) and scaled on the canvas with smoothing switched off. Two
 * consequences, both wanted: zooming never refetches, and every pixel on screen
 * is one real sample drawn larger rather than a blend of neighbouring samples.
 * Bilinear scaling would invent intermediate values and make a noisy line look
 * better resolved than it is — the same reason studio/render.py resamples
 * nearest-neighbour server-side.
 */

import { activeChannel, activeVelocity, getState } from "./state.js";

export const GUTTER_LEFT = 62;
export const GUTTER_TOP = 24;

const COLOURS = {
  gutter: "#0b0e11",
  gutterLine: "#2a313b",
  tick: "#5c6673",
  label: "#8a94a3",
  crosshair: "#4ea3d8",
  pick: "#f0a830",
  fit: "#48d18a",
  manual: "#4ea3d8",
  measure: "#e0603c",
  marquee: "#4ea3d8",
};

/* Candidate boxes are tinted by the shape the diagnoser measured, matching the
 * legend the Phase-1 tooling already used. Grey means "no class suggested" —
 * an honest absence, not a category. */
const CANDIDATE_COLOURS = {
  clear_point_reflector: "#00ff88",
  low_snr_point_reflector: "#ffee00",
  elongated_linear_target: "#00aaff",
  disturbed_zone: "#ff5533",
};
const CANDIDATE_UNDIAGNOSED = "#7d8794";

const canvas = document.getElementById("radargram");
const ctx = canvas.getContext("2d");

let image = null;      // HTMLImageElement of the current radargram
let imageKey = null;   // the URL it was loaded from, so a repeat render is a no-op

export function plotRect() {
  return {
    x: GUTTER_LEFT,
    y: GUTTER_TOP,
    w: Math.max(0, canvas.clientWidth - GUTTER_LEFT),
    h: Math.max(0, canvas.clientHeight - GUTTER_TOP),
  };
}

export function dataToScreen(view, trace, sample) {
  const plot = plotRect();
  return {
    x: plot.x + (trace - view.originTrace) * view.scaleX,
    y: plot.y + (sample - view.originSample) * view.scaleY,
  };
}

export function screenToData(view, x, y) {
  const plot = plotRect();
  return {
    trace: view.originTrace + (x - plot.x) / view.scaleX,
    sample: view.originSample + (y - plot.y) / view.scaleY,
  };
}

/** A viewport showing the whole line, with a little breathing room. */
export function fitView(channel) {
  const plot = plotRect();
  if (!channel || plot.w <= 0 || plot.h <= 0) {
    return { originTrace: 0, originSample: 0, scaleX: 1, scaleY: 1 };
  }
  return {
    originTrace: 0,
    originSample: 0,
    scaleX: plot.w / channel.n_traces,
    scaleY: plot.h / channel.n_samples,
  };
}

/** Zoom by `factor` about a fixed screen point, so what is under the cursor stays put. */
export function zoomAbout(view, factor, screenX, screenY) {
  const anchor = screenToData(view, screenX, screenY);
  const scaleX = clamp(view.scaleX * factor, 0.02, 400);
  const scaleY = clamp(view.scaleY * factor, 0.02, 400);
  const plot = plotRect();
  return {
    scaleX,
    scaleY,
    originTrace: anchor.trace - (screenX - plot.x) / scaleX,
    originSample: anchor.sample - (screenY - plot.y) / scaleY,
  };
}

const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));

/** Tick spacing that lands on 1/2/5 x 10^n, so labels read as round numbers. */
function niceStep(span, targetTicks) {
  const rough = span / Math.max(targetTicks, 1);
  const magnitude = 10 ** Math.floor(Math.log10(Math.max(rough, 1e-9)));
  for (const multiple of [1, 2, 5, 10]) {
    if (magnitude * multiple >= rough) return magnitude * multiple;
  }
  return magnitude * 10;
}

export function setImage(url, onReady) {
  if (url === imageKey) return;
  imageKey = url;
  const next = new Image();
  next.onload = () => {
    if (imageKey !== url) return; // a newer request landed first; discard this one
    image = next;
    onReady?.();
  };
  next.onerror = () => {
    if (imageKey === url) { image = null; onReady?.(); }
  };
  next.src = url;
}

export function clearImage() {
  image = null;
  imageKey = null;
}

export function resize() {
  const wrap = canvas.parentElement;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(wrap.clientWidth * ratio);
  canvas.height = Math.round(wrap.clientHeight * ratio);
  canvas.style.width = `${wrap.clientWidth}px`;
  canvas.style.height = `${wrap.clientHeight}px`;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
}

export function draw() {
  const state = getState();
  const channel = activeChannel(state);
  const ratio = window.devicePixelRatio || 1;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  if (!channel) return;

  const plot = plotRect();
  const { view } = state;

  ctx.save();
  ctx.beginPath();
  ctx.rect(plot.x, plot.y, plot.w, plot.h);
  ctx.clip();

  ctx.fillStyle = "#000";
  ctx.fillRect(plot.x, plot.y, plot.w, plot.h);

  if (image) {
    ctx.imageSmoothingEnabled = false;
    const topLeft = dataToScreen(view, 0, 0);
    ctx.drawImage(
      image,
      topLeft.x,
      topLeft.y,
      channel.n_traces * view.scaleX,
      channel.n_samples * view.scaleY,
    );
  }

  drawCandidates(state, channel);
  drawHyperbola(state, channel);
  drawPicks(state, channel);
  drawMeasure(state, channel);
  drawMarquee(state);
  ctx.restore();

  drawRulers(state, channel);
  drawCrosshair(state, plot);
}

function drawRulers(state, channel) {
  const plot = plotRect();
  const { view } = state;
  const velocity = activeVelocity(state);

  ctx.fillStyle = COLOURS.gutter;
  ctx.fillRect(0, 0, canvas.clientWidth, GUTTER_TOP);
  ctx.fillRect(0, 0, GUTTER_LEFT, canvas.clientHeight);
  ctx.strokeStyle = COLOURS.gutterLine;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(plot.x + 0.5, 0);
  ctx.lineTo(plot.x + 0.5, canvas.clientHeight);
  ctx.moveTo(0, plot.y + 0.5);
  ctx.lineTo(canvas.clientWidth, plot.y + 0.5);
  ctx.stroke();

  ctx.font = "10px ui-monospace, Menlo, monospace";
  ctx.fillStyle = COLOURS.label;

  // Distance ruler — metres along the line, from the wheel encoder.
  const metresLo = view.originTrace * channel.trace_spacing_m;
  const metresHi = (view.originTrace + plot.w / view.scaleX) * channel.trace_spacing_m;
  const stepM = niceStep(metresHi - metresLo, Math.max(3, Math.floor(plot.w / 90)));
  const decimals = stepM < 0.1 ? 2 : stepM < 1 ? 1 : 0;
  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
  for (let m = Math.ceil(metresLo / stepM) * stepM; m <= metresHi; m += stepM) {
    const { x } = dataToScreen(view, m / channel.trace_spacing_m, 0);
    // Keep clear of the gutters: a centred label straddling the axis corner
    // gets half-clipped and reads as a different number.
    if (x < plot.x + 16 || x > plot.x + plot.w - 16) continue;
    ctx.strokeStyle = COLOURS.tick;
    ctx.beginPath();
    ctx.moveTo(Math.round(x) + 0.5, GUTTER_TOP - 5);
    ctx.lineTo(Math.round(x) + 0.5, GUTTER_TOP);
    ctx.stroke();
    ctx.fillText(`${m.toFixed(decimals)} m`, x, GUTTER_TOP - 6);
  }

  // Depth ruler — two-way time is what was measured; depth is that time times a
  // velocity. Both are drawn, time in the dimmer secondary position, so the
  // derived quantity is never the only thing on the axis.
  const nsLo = view.originSample * channel.sample_interval_ns;
  const nsHi = (view.originSample + plot.h / view.scaleY) * channel.sample_interval_ns;
  // Each depth tick carries two stacked labels (depth over time), so it needs
  // roughly twice the room a single-line ruler would.
  const stepNs = niceStep(nsHi - nsLo, Math.max(2, Math.floor(plot.h / 78)));
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let ns = Math.ceil(nsLo / stepNs) * stepNs; ns <= nsHi; ns += stepNs) {
    const { y } = dataToScreen(view, 0, ns / channel.sample_interval_ns);
    if (y < plot.y + 10 || y > plot.y + plot.h - 4) continue;
    ctx.strokeStyle = COLOURS.tick;
    ctx.beginPath();
    ctx.moveTo(GUTTER_LEFT - 5, Math.round(y) + 0.5);
    ctx.lineTo(GUTTER_LEFT, Math.round(y) + 0.5);
    ctx.stroke();
    if (velocity.value) {
      ctx.fillStyle = COLOURS.label;
      ctx.fillText(`${((velocity.value * ns) / 2).toFixed(2)}m`, GUTTER_LEFT - 8, y - 5);
      ctx.fillStyle = COLOURS.tick;
      ctx.fillText(`${ns.toFixed(1)}ns`, GUTTER_LEFT - 8, y + 5);
    } else {
      ctx.fillStyle = COLOURS.label;
      ctx.fillText(`${ns.toFixed(1)}ns`, GUTTER_LEFT - 8, y);
    }
  }

  // Cover the axis corner last so no ruler label can bleed into it, then name
  // the units. Depth is listed first because it is the primary label, but the
  // time it derives from is always shown beneath it — see the loop above.
  ctx.fillStyle = COLOURS.gutter;
  ctx.fillRect(0, 0, GUTTER_LEFT, GUTTER_TOP);
  ctx.strokeStyle = COLOURS.gutterLine;
  ctx.beginPath();
  ctx.moveTo(GUTTER_LEFT - 0.5, 0);
  ctx.lineTo(GUTTER_LEFT - 0.5, GUTTER_TOP);
  ctx.moveTo(0, GUTTER_TOP - 0.5);
  ctx.lineTo(GUTTER_LEFT, GUTTER_TOP - 0.5);
  ctx.stroke();
  ctx.fillStyle = COLOURS.tick;
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  ctx.fillText(velocity.value ? "m / ns" : "ns", GUTTER_LEFT - 8, GUTTER_TOP / 2);
}

function drawCrosshair(state, plot) {
  if (!state.cursor) return;
  const { x, y } = dataToScreen(state.view, state.cursor.trace, state.cursor.sample);
  if (x < plot.x || x > plot.x + plot.w || y < plot.y || y > plot.y + plot.h) return;
  ctx.save();
  ctx.strokeStyle = COLOURS.crosshair;
  ctx.globalAlpha = 0.55;
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  ctx.moveTo(plot.x, Math.round(y) + 0.5);
  ctx.lineTo(plot.x + plot.w, Math.round(y) + 0.5);
  ctx.moveTo(Math.round(x) + 0.5, plot.y);
  ctx.lineTo(Math.round(x) + 0.5, plot.y + plot.h);
  ctx.stroke();
  ctx.restore();
}

function drawCandidates(state, channel) {
  if (!state.showCandidates) return;
  ctx.save();
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 2]);
  for (const candidate of state.candidates) {
    if (candidate.channel !== channel.extension) continue;
    const topLeft = dataToScreen(state.view, candidate.x, candidate.y);
    const bottomRight = dataToScreen(state.view, candidate.x + candidate.w, candidate.y + candidate.h);
    ctx.strokeStyle = candidate.suggested_class
      ? CANDIDATE_COLOURS[candidate.suggested_class] ?? CANDIDATE_UNDIAGNOSED
      : CANDIDATE_UNDIAGNOSED;
    ctx.globalAlpha = 0.8;
    ctx.strokeRect(topLeft.x, topLeft.y, bottomRight.x - topLeft.x, bottomRight.y - topLeft.y);
  }
  ctx.restore();
}

function drawHyperbola(state, channel) {
  const overlay = state.hyperbola;
  if (!overlay?.curve?.length) return;
  ctx.save();
  ctx.strokeStyle = overlay.source === "fitted" ? COLOURS.fit : COLOURS.manual;
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  overlay.curve.forEach((point, index) => {
    const { x, y } = dataToScreen(state.view, point.trace, point.time_ns / channel.sample_interval_ns);
    index === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();

  const apex = dataToScreen(
    state.view,
    overlay.apexTrace,
    overlay.apexTimeNs / channel.sample_interval_ns,
  );
  ctx.fillStyle = ctx.strokeStyle;
  ctx.beginPath();
  ctx.arc(apex.x, apex.y, 3.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawPicks(state, channel) {
  ctx.save();
  ctx.font = "10px ui-monospace, Menlo, monospace";
  for (const pick of state.picks) {
    if (pick.channel !== channel.extension) continue;
    const { x, y } = dataToScreen(state.view, pick.trace, pick.sample);
    ctx.strokeStyle = pick.velocity_source === "fitted" ? COLOURS.fit : COLOURS.pick;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x - 6, y); ctx.lineTo(x + 6, y);
    ctx.moveTo(x, y - 6); ctx.lineTo(x, y + 6);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(x, y, 8, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = ctx.strokeStyle;
    ctx.fillText(`${pick.depth_m.toFixed(2)}m`, x + 11, y - 3);
  }
  ctx.restore();
}

function drawMeasure(state, channel) {
  if (!state.measure) return;
  const { from, to } = state.measure;
  const a = dataToScreen(state.view, from.trace, from.sample);
  const b = dataToScreen(state.view, to.trace, to.sample);
  ctx.save();
  ctx.strokeStyle = COLOURS.measure;
  ctx.lineWidth = 1.4;
  ctx.setLineDash([5, 3]);
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.lineTo(b.x, b.y);
  ctx.stroke();
  ctx.setLineDash([]);
  for (const point of [a, b]) {
    ctx.beginPath();
    ctx.arc(point.x, point.y, 3, 0, Math.PI * 2);
    ctx.stroke();
  }

  const velocity = activeVelocity(state);
  const dx = Math.abs(to.trace - from.trace) * channel.trace_spacing_m;
  const dNs = Math.abs(to.sample - from.sample) * channel.sample_interval_ns;
  const parts = [`Δx ${dx.toFixed(3)} m`, `Δt ${dNs.toFixed(2)} ns`];
  if (velocity.value) parts.push(`Δz ${((velocity.value * dNs) / 2).toFixed(3)} m`);
  ctx.fillStyle = COLOURS.measure;
  ctx.font = "10px ui-monospace, Menlo, monospace";
  ctx.fillText(parts.join("   "), (a.x + b.x) / 2 + 8, (a.y + b.y) / 2 - 6);
  ctx.restore();
}

function drawMarquee(state) {
  const drag = state.drag;
  if (drag?.kind !== "marquee") return;
  const a = dataToScreen(state.view, drag.from.trace, drag.from.sample);
  const b = dataToScreen(state.view, drag.to.trace, drag.to.sample);
  ctx.save();
  ctx.strokeStyle = COLOURS.marquee;
  ctx.setLineDash([4, 3]);
  ctx.lineWidth = 1;
  ctx.strokeRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y));
  ctx.restore();
}

export { canvas };
