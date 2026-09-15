/* Pointer and keyboard interaction for the radargram canvas.
 *
 * The four tools mirror what any GPR interpretation package gives you: pan,
 * mark a target, fit a hyperbola, measure between two points.
 *
 * One deliberate omission: there is no client-side hyperbola maths. The manual
 * gesture is a marquee that the *server* fits (studio/velocity.py), and the
 * velocity slider asks the server for each curve. Reimplementing the physics in
 * JavaScript for smoother dragging would create a second source of truth for
 * the one number every depth in this system depends on, and the two would drift.
 * The round trip is a couple of milliseconds on a line this size.
 */

import { activeChannel, getState, setState } from "./state.js";
import { canvas, fitView, plotRect, screenToData, zoomAbout } from "./view.js";

const MIN_MARQUEE_TRACES = 3;
const MIN_MARQUEE_SAMPLES = 3;

let handlers = {};

function pointerData(event) {
  const rect = canvas.getBoundingClientRect();
  return screenToData(getState().view, event.clientX - rect.left, event.clientY - rect.top);
}

function insidePlot(event) {
  const rect = canvas.getBoundingClientRect();
  const plot = plotRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  return x >= plot.x && x <= plot.x + plot.w && y >= plot.y && y <= plot.y + plot.h;
}

function clampToChannel(point, channel) {
  return {
    trace: Math.min(Math.max(point.trace, 0), channel.n_traces - 1),
    sample: Math.min(Math.max(point.sample, 0), channel.n_samples - 1),
  };
}

/* Pointer capture keeps a drag alive when the cursor leaves the canvas —
 * important for a marquee started near an edge. It is a convenience, not a
 * requirement: capture can be refused (the pointer is already gone, or the
 * event was synthesised), and a refusal must not take the gesture down with
 * it, so both calls are advisory. */
function capturePointer(pointerId) {
  try { canvas.setPointerCapture(pointerId); } catch { /* drag still works uncaptured */ }
}

function releasePointer(pointerId) {
  try {
    if (canvas.hasPointerCapture(pointerId)) canvas.releasePointerCapture(pointerId);
  } catch { /* nothing was captured */ }
}

function onPointerDown(event) {
  const state = getState();
  const channel = activeChannel(state);
  if (!channel || !insidePlot(event)) return;
  capturePointer(event.pointerId);
  const point = clampToChannel(pointerData(event), channel);

  if (state.tool === "pan") {
    setState({
      viewFitted: false,
      drag: { kind: "pan", startClient: { x: event.clientX, y: event.clientY }, startView: state.view },
    });
    return;
  }
  if (state.tool === "pick") {
    handlers.onPick?.(point);
    return;
  }
  if (state.tool === "hyperbola") {
    setState({ drag: { kind: "marquee", from: point, to: point } });
    return;
  }
  if (state.tool === "measure") {
    setState({ drag: { kind: "measure", from: point }, measure: { from: point, to: point } });
  }
}

function onPointerMove(event) {
  const state = getState();
  const channel = activeChannel(state);
  if (!channel) return;
  const point = clampToChannel(pointerData(event), channel);
  const drag = state.drag;

  if (drag?.kind === "pan") {
    const dx = event.clientX - drag.startClient.x;
    const dy = event.clientY - drag.startClient.y;
    setState({
      cursor: point,
      view: {
        ...drag.startView,
        originTrace: drag.startView.originTrace - dx / drag.startView.scaleX,
        originSample: drag.startView.originSample - dy / drag.startView.scaleY,
      },
    });
  } else if (drag?.kind === "marquee") {
    setState({ cursor: point, drag: { ...drag, to: point } });
  } else if (drag?.kind === "measure") {
    setState({ cursor: point, measure: { from: drag.from, to: point } });
  } else {
    setState({ cursor: point });
  }
  handlers.onCursor?.(point);
}

function onPointerUp(event) {
  const state = getState();
  const drag = state.drag;
  releasePointer(event.pointerId);
  if (!drag) return;

  if (drag.kind === "marquee") {
    const traceSpan = Math.abs(drag.to.trace - drag.from.trace);
    const sampleSpan = Math.abs(drag.to.sample - drag.from.sample);
    setState({ drag: null });
    if (traceSpan >= MIN_MARQUEE_TRACES && sampleSpan >= MIN_MARQUEE_SAMPLES) {
      handlers.onFitRegion?.({
        trace_start: Math.round(Math.min(drag.from.trace, drag.to.trace)),
        sample_start: Math.round(Math.min(drag.from.sample, drag.to.sample)),
        trace_span: Math.round(traceSpan),
        sample_span: Math.round(sampleSpan),
      });
    }
    return;
  }
  setState({ drag: null });
}

function onWheel(event) {
  if (!activeChannel(getState()) || !insidePlot(event)) return;
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
  setState({
    viewFitted: false,
    view: zoomAbout(getState().view, factor, event.clientX - rect.left, event.clientY - rect.top),
  });
}

function onKeyDown(event) {
  if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return;
  const shortcuts = { h: "pan", p: "pick", v: "hyperbola", m: "measure" };
  const tool = shortcuts[event.key.toLowerCase()];
  if (tool) {
    setState({ tool });
    return;
  }
  if (event.key.toLowerCase() === "f") {
    const channel = activeChannel(getState());
    if (channel) setState({ view: fitView(channel), viewFitted: true });
  }
  if (event.key === "Escape") {
    setState({ drag: null, measure: null, hyperbola: null, fit: null });
  }
}

export function zoomBy(factor) {
  const plot = plotRect();
  setState({
    viewFitted: false,
    view: zoomAbout(getState().view, factor, plot.x + plot.w / 2, plot.y + plot.h / 2),
  });
}

export function install(callbacks) {
  handlers = callbacks;
  canvas.addEventListener("pointerdown", onPointerDown);
  canvas.addEventListener("pointermove", onPointerMove);
  canvas.addEventListener("pointerup", onPointerUp);
  canvas.addEventListener("pointercancel", onPointerUp);
  canvas.addEventListener("pointerleave", () => setState({ cursor: null }));
  canvas.addEventListener("wheel", onWheel, { passive: false });
  window.addEventListener("keydown", onKeyDown);
}

/** Cursor shape communicates the active tool even before the operator clicks. */
export function cursorFor(tool) {
  return { pan: "grab", pick: "crosshair", hyperbola: "crosshair", measure: "crosshair" }[tool] ?? "default";
}
