/* The radargram canvas: rulers, image, overlays, and the data<->screen transform.
 *
 * The image is requested at the size it is drawn, in device pixels (app.js requestImage), and
 * re-requested once a zoom or resize settles; until then the last one is stretched to fit.
 * Enlarging happens server-side by linear blending of neighbouring samples (studio/render.py),
 * which never invents a value outside their range.
 */

import { activeChannel, activeVelocity, getState } from "./state.js";
import { token } from "./theme.js";

export const GUTTER_LEFT = 62;
export const GUTTER_TOP = 24;

const COLOURS = {
  crosshair: "#3b82f6",
  pick: "#f59e0b",
  fit: "#22c55e",
  manual: "#3b82f6",
  measure: "#f97316",
  marquee: "#3b82f6",
};

/** The rulers' frame follows the light/dark theme; everything drawn over the data keeps its colour. */
const frame = () => ({
  gutter: token("--bg-sink"), gutterLine: token("--line"), tick: token("--text-faint"), label: token("--text-dim"),
});

/* Candidate boxes are tinted by the shape the diagnoser measured, matching the
 * legend the Phase-1 tooling already used. Grey means "no class suggested" —
 * an honest absence, not a category. */
export const CANDIDATE_COLOURS = {
  clear_point_reflector: "#00ff88",
  low_snr_point_reflector: "#ffee00",
  elongated_linear_target: "#00aaff",
  disturbed_zone: "#ff5533",
};
export const CANDIDATE_UNDIAGNOSED = "#71717a";

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

/* Raw sample index of the ground surface: the "First sample" processing setting. */
export function timeZeroSample(state) {
  // A whole sample: the field accepts any number, and the server takes only whole samples.
  return Math.max(0, Math.round(state.processing?.time_zero_sample ?? 0));
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
    // The server crops the rows above time zero, so the image starts there. Every coordinate
    // on screen stays a raw sample index; only times and depths are measured from time zero.
    ctx.imageSmoothingEnabled = true; // the image is near screen size; this only covers a settling zoom
    ctx.imageSmoothingQuality = "high";
    const t0 = timeZeroSample(state);
    const topLeft = dataToScreen(view, 0, t0);
    ctx.drawImage(
      image,
      topLeft.x,
      topLeft.y,
      channel.n_traces * view.scaleX,
      (channel.n_samples - t0) * view.scaleY,
    );
  }

  drawCandidates(state, channel);
  drawHyperbola(state, channel);
  drawPicks(state, channel);
  drawReviews(state, channel);
  drawMeasure(state, channel);
  drawMarquee(state);
  ctx.restore();

  drawRulers(state, channel);
  drawCrosshair(state, plot);
}

function drawRulers(state, channel) {
  const ruler = frame();
  const plot = plotRect();
  const { view } = state;
  const velocity = activeVelocity(state);

  ctx.fillStyle = ruler.gutter;
  ctx.fillRect(0, 0, canvas.clientWidth, GUTTER_TOP);
  ctx.fillRect(0, 0, GUTTER_LEFT, canvas.clientHeight);
  ctx.strokeStyle = ruler.gutterLine;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(plot.x + 0.5, 0);
  ctx.lineTo(plot.x + 0.5, canvas.clientHeight);
  ctx.moveTo(0, plot.y + 0.5);
  ctx.lineTo(canvas.clientWidth, plot.y + 0.5);
  ctx.stroke();

  ctx.font = "10px ui-monospace, Menlo, monospace";
  ctx.fillStyle = ruler.label;

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
    ctx.strokeStyle = ruler.tick;
    ctx.beginPath();
    ctx.moveTo(Math.round(x) + 0.5, GUTTER_TOP - 5);
    ctx.lineTo(Math.round(x) + 0.5, GUTTER_TOP);
    ctx.stroke();
    ctx.fillText(`${m.toFixed(decimals)} m`, x, GUTTER_TOP - 6);
  }

  // Depth ruler — two-way time is what was measured; depth is that time times a
  // velocity. Both are drawn, time in the dimmer secondary position, so the
  // derived quantity is never the only thing on the axis.
  const t0 = timeZeroSample(state);
  const nsLo = (view.originSample - t0) * channel.sample_interval_ns;
  const nsHi = (view.originSample - t0 + plot.h / view.scaleY) * channel.sample_interval_ns;
  // Each depth tick carries two stacked labels (depth over time), so it needs
  // roughly twice the room a single-line ruler would.
  const stepNs = niceStep(nsHi - nsLo, Math.max(2, Math.floor(plot.h / 78)));
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let ns = Math.ceil(nsLo / stepNs) * stepNs; ns <= nsHi; ns += stepNs) {
    const { y } = dataToScreen(view, 0, t0 + ns / channel.sample_interval_ns);
    if (y < plot.y + 10 || y > plot.y + plot.h - 4) continue;
    ctx.strokeStyle = ruler.tick;
    ctx.beginPath();
    ctx.moveTo(GUTTER_LEFT - 5, Math.round(y) + 0.5);
    ctx.lineTo(GUTTER_LEFT, Math.round(y) + 0.5);
    ctx.stroke();
    if (velocity.value) {
      ctx.fillStyle = ruler.label;
      ctx.fillText(`${((velocity.value * ns) / 2).toFixed(2)}m`, GUTTER_LEFT - 8, y - 5);
      ctx.fillStyle = ruler.tick;
      ctx.fillText(`${ns.toFixed(1)}ns`, GUTTER_LEFT - 8, y + 5);
    } else {
      ctx.fillStyle = ruler.label;
      ctx.fillText(`${ns.toFixed(1)}ns`, GUTTER_LEFT - 8, y);
    }
  }

  // Cover the axis corner last so no ruler label can bleed into it, then name
  // the units. Depth is listed first because it is the primary label, but the
  // time it derives from is always shown beneath it — see the loop above.
  ctx.fillStyle = ruler.gutter;
  ctx.fillRect(0, 0, GUTTER_LEFT, GUTTER_TOP);
  ctx.strokeStyle = ruler.gutterLine;
  ctx.beginPath();
  ctx.moveTo(GUTTER_LEFT - 0.5, 0);
  ctx.lineTo(GUTTER_LEFT - 0.5, GUTTER_TOP);
  ctx.moveTo(0, GUTTER_TOP - 0.5);
  ctx.lineTo(GUTTER_LEFT, GUTTER_TOP - 0.5);
  ctx.stroke();
  ctx.fillStyle = ruler.tick;
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
  for (const candidate of state.candidates) {
    if (candidate.channel !== channel.extension) continue;
    // Seen on two or more of the line's channels: likelier a real target, so drawn solid and
    // heavier. Single-channel candidates stay dashed.
    const corroborated = (candidate.corroboration ?? 1) >= 2;
    ctx.lineWidth = corroborated ? 2 : 1;
    ctx.setLineDash(corroborated ? [] : [3, 2]);
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
    // The curve's times are record times (the server adds time zero back); the apex time is
    // measured from time zero, so it is offset here.
    const { x, y } = dataToScreen(state.view, point.trace, point.time_ns / channel.sample_interval_ns);
    index === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();

  const apex = dataToScreen(
    state.view,
    overlay.apexTrace,
    overlay.timeZeroSample + overlay.apexTimeNs / channel.sample_interval_ns,
  );
  ctx.fillStyle = ctx.strokeStyle;
  ctx.beginPath();
  ctx.arc(apex.x, apex.y, 3.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

/* A circle on every target a reasoning layer made a claim about, coloured by what a
 * supervisor has decided: amber dashed while proposed, green once confirmed, red dotted if
 * rejected. The circle is drawn on the target's own measured box, never somewhere a model
 * chose, so it always sits on real data. */
export const REVIEW_STYLES = {
  proposed: { colour: "#f59e0b", dash: [6, 4], mark: "?" },
  confirmed: { colour: "#22c55e", dash: [], mark: "✓" },
  rejected: { colour: "#ef4444", dash: [2, 3], mark: "✗" },
};

const LABEL_H = 15;
const LABEL_GAP = 2;

/** A label's spot: above its circle, else stacked higher, else below; null when every spot is taken. */
function placeLabel(placed, plot, cx, top, bottom, width) {
  const x = Math.min(Math.max(cx - width / 2, plot.x + 2), plot.x + plot.w - width - 2); // inside the canvas
  const ys = [0, 1, 2].map((n) => top - 3 - LABEL_H - n * (LABEL_H + LABEL_GAP)).concat(bottom + 3);
  for (const y of ys) {
    if (y < plot.y + 2 || y + LABEL_H > plot.y + plot.h - 2) continue;
    const box = { x, y, w: width, h: LABEL_H };
    const clear = placed.every((o) => box.x + box.w + LABEL_GAP <= o.x || o.x + o.w + LABEL_GAP <= box.x
      || box.y + box.h + LABEL_GAP <= o.y || o.y + o.h + LABEL_GAP <= box.y);
    if (clear) return box;
  }
  return null;
}

function drawReviews(state, channel) {
  ctx.save();
  ctx.font = "11px ui-sans-serif, system-ui, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  // Every run adds its own claim, so one target can collect several pending readings; the
  // canvas shows only the newest pending one per target (the Review panel keeps them all).
  const newestPending = new Map();
  for (const review of state.reviews) {
    if (review.status !== "proposed" || review.channel !== channel.extension) continue;
    const held = newestPending.get(review.target_id);
    if (!held || review.created_at >= held.created_at) newestPending.set(review.target_id, review);
  }
  const plot = plotRect();
  const shapes = state.reviews
    .filter((r) => r.channel === channel.extension && (r.status !== "proposed" || newestPending.get(r.target_id) === r))
    .map((review) => {
      const a = dataToScreen(state.view, review.x, review.y);
      const b = dataToScreen(state.view, review.x + review.w, review.y + review.h);
      const rx = Math.max(10, Math.abs(b.x - a.x) / 2 + 6);
      const ry = Math.max(10, Math.abs(b.y - a.y) / 2 + 6);
      return { review, cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2, rx, ry };
    });
  const under = (s) => {
    if (!state.cursor) return false;
    const p = dataToScreen(state.view, state.cursor.trace, state.cursor.sample);
    return ((p.x - s.cx) / s.rx) ** 2 + ((p.y - s.cy) / s.ry) ** 2 <= 1;
  };
  for (const { review, cx, cy, rx, ry } of shapes) {
    const style = REVIEW_STYLES[review.status] ?? REVIEW_STYLES.proposed;
    ctx.strokeStyle = style.colour;
    ctx.lineWidth = state.highlightReviewId === review.id ? 3 : 1.8;
    ctx.setLineDash(style.dash);
    ctx.beginPath();
    ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
    ctx.stroke();
  }
  // Labels after every circle, so none is drawn over. Highlighted and hovered ones claim a spot
  // first; when the rest are too crowded to place, they show only while hovered.
  const first = (s) => (state.highlightReviewId === s.review.id || under(s) ? 0 : 1);
  const placed = [];
  ctx.setLineDash([]);
  for (const shape of [...shapes].sort((p, q) => first(p) - first(q) || p.cx - q.cx)) {
    const style = REVIEW_STYLES[shape.review.status] ?? REVIEW_STYLES.proposed;
    const label = `${style.mark} ${shape.review.identity}`;
    const width = ctx.measureText(label).width + 8;
    const box = placeLabel(placed, plot, shape.cx, shape.cy - shape.ry, shape.cy + shape.ry, width);
    if (!box) continue;
    placed.push(box);
    ctx.fillStyle = "rgba(10, 14, 20, 0.78)";
    ctx.fillRect(box.x, box.y, box.w, box.h);
    ctx.fillStyle = style.colour;
    ctx.fillText(label, box.x + 4, box.y + 11);
  }
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
