/* GPR Studio — boot and wiring.
 *
 * One render pass, driven off the store, batched into an animation frame. Every
 * panel reads the same state, so the number in the status bar, the depth on the
 * ruler and the velocity in the target list cannot disagree with each other.
 */

import * as api from "./api.js";
import * as ascan from "./ascan.js";
import * as docks from "./docks.js";
import * as info from "./panel_info.js";
import * as interact from "./interact.js";
import * as interpretPanel from "./panel_interpret.js";
import * as modal from "./modal.js";
import * as processing from "./panel_processing.js";
import * as targets from "./panel_targets.js";
import * as velocityPanel from "./panel_velocity.js";
import * as view from "./view.js";
import { activeChannel, activeVelocity, getState, setState, subscribe } from "./state.js";
import { el, replace } from "./dom.js";

const ASCAN_DEBOUNCE_MS = 90;

const dom = {
  jobBadge: document.getElementById("jobBadge"),
  chainPill: document.getElementById("chainPill"),
  toolButtons: document.getElementById("toolButtons"),
  channelButtons: document.getElementById("channelButtons"),
  paletteSelect: document.getElementById("paletteSelect"),
  contrastRange: document.getElementById("contrastRange"),
  contrastOut: document.getElementById("contrastOut"),
  canvasEmpty: document.getElementById("canvasEmpty"),
  statX: document.getElementById("statX"),
  statT: document.getElementById("statT"),
  statZ: document.getElementById("statZ"),
  statAmp: document.getElementById("statAmp"),
  statTrace: document.getElementById("statTrace"),
  statV: document.getElementById("statV"),
  statProvenance: document.getElementById("statProvenance"),
};

let frameQueued = false;
let ascanTimer = null;
/* Identifies the A-scan currently on screen: line, channel, trace *and* the
 * processing chain. Keying on the trace index alone let the wiggle survive a
 * processing change, leaving it showing a different signal from the radargram
 * directly above it — and the amplitude readout in the status bar with it. */
let lastAscanKey = null;

function scheduleRender() {
  if (frameQueued) return;
  frameQueued = true;
  requestAnimationFrame(() => {
    frameQueued = false;
    render();
  });
}

/* ---------- data loading ---------- */

async function openJob(job) {
  const state = getState();
  if (state.expandedJob === job && state.job === job) {
    setState({ expandedJob: null });
    return;
  }
  try {
    const detail = await api.getJob(job);
    const channel = detail.channels[0]?.extension ?? null;
    setState({
      job,
      jobDetail: detail,
      expandedJob: job,
      channel,
      hyperbola: null,
      fit: null,
      measure: null,
      ascan: null,
      picks: await api.listPicks(job),
      // Candidates are optional context: a job whose detector has never been
      // run is perfectly openable, just without an overlay to toggle.
      candidates: await api.listCandidates(job).catch(() => []),
    });
    resetView();
    syncLocation();
  } catch (error) {
    setState({ status: `Could not open ${job}: ${error.message}` });
  }
}

/* The open line lives in the URL, so a view can be bookmarked or pasted to a
 * colleague. replaceState, not pushState: switching channel is not a
 * navigation, and filling someone's Back button with it would be hostile. */
function syncLocation() {
  const { job, channel } = getState();
  if (!job) return;
  const query = new URLSearchParams({ job, ...(channel ? { channel } : {}) });
  window.history.replaceState(null, "", `?${query}`);
}

function selectChannel(extension) {
  // A fit measured on one channel does not carry to another — different
  // sampling interval, different time axis, different data entirely.
  setState({ channel: extension, hyperbola: null, fit: null, ascan: null, measure: null });
  resetView();
  syncLocation();
}

function resetView() {
  const channel = activeChannel(getState());
  if (channel) setState({ view: view.fitView(channel), viewFitted: true });
}

function ascanKey(state, index) {
  return `${state.job}/${state.channel}/${index}/${JSON.stringify(state.processing)}`;
}

function refreshAscan() {
  const state = getState();
  const channel = activeChannel(state);
  if (!channel || !state.cursor) return;
  const index = Math.round(state.cursor.trace);
  const key = ascanKey(state, index);
  if (key === lastAscanKey) return;
  lastAscanKey = key;

  clearTimeout(ascanTimer);
  ascanTimer = setTimeout(async () => {
    const current = getState();
    if (!current.job || !current.channel) return;
    try {
      setState({ ascan: await api.getTrace(current.job, current.channel, index, current.processing) });
    } catch {
      // Let the next render re-ask rather than leaving a stale wiggle pinned
      // under a radargram it no longer matches.
      lastAscanKey = null;
    }
  }, ASCAN_DEBOUNCE_MS);
}

/* ---------- tool actions ---------- */

async function fitRegion(region) {
  const state = getState();
  if (!state.job || !state.channel) return;
  setState({ status: "Fitting hyperbola…" });
  try {
    const result = await api.fitHyperbola(state.job, state.channel, region);
    if (!result.fit) {
      setState({ fit: null, hyperbola: null, status: result.reason });
      return;
    }
    setState({
      fit: result.fit,
      hyperbola: {
        apexTrace: result.fit.apex_trace,
        apexTimeNs: result.fit.apex_time_ns,
        velocity: result.fit.velocity_m_per_ns,
        curve: result.curve,
        source: "fitted",
      },
      status: null,
    });
  } catch (error) {
    setState({ status: `Fit failed: ${error.message}` });
  }
}

async function setManualVelocity(value) {
  const state = getState();
  if (!state.hyperbola || !state.job || !state.channel) return;
  // Optimistic: move the number immediately, swap in the server's curve when it lands.
  setState({ hyperbola: { ...state.hyperbola, velocity: value, source: "manual" }, fit: null });
  try {
    const result = await api.previewCurve(
      state.job, state.channel, state.hyperbola.apexTrace, state.hyperbola.apexTimeNs, value,
    );
    const current = getState();
    if (current.hyperbola?.velocity !== value) return; // superseded by a newer drag
    setState({ hyperbola: { ...current.hyperbola, curve: result.curve } });
  } catch (error) {
    setState({ status: `Curve failed: ${error.message}` });
  }
}

async function savePick(point) {
  const state = getState();
  const channel = activeChannel(state);
  if (!channel || !state.job) return;

  const overlay = point ? null : state.hyperbola;
  const velocity = overlay
    ? { value: overlay.velocity, source: overlay.source }
    : activeVelocity(state);
  if (!velocity.value) {
    setState({ status: "No velocity available — cannot put a depth on this target." });
    return;
  }

  const trace = overlay ? overlay.apexTrace : point.trace;
  const timeNs = overlay ? overlay.apexTimeNs : point.sample * channel.sample_interval_ns;
  const depthM = (velocity.value * timeNs) / 2;

  const label = await modal.promptText({
    title: "Save target",
    note:
      `${depthM.toFixed(3)} m deep, from a velocity that is ${{ fitted: "measured from this target's own hyperbola",
        manual: "set by hand", assumed: "taken from the file header" }[velocity.source]}. ` +
      "Label is free text — it is not one of the taxonomy classes, which only the company's ground truth decides.",
    placeholder: "e.g. suspected service duct",
    confirmLabel: "Save target",
  });
  if (label === null) return; // dismissed — do not record a target nobody confirmed

  try {
    await api.createPick(state.job, {
      channel: channel.extension,
      trace,
      sample: timeNs / channel.sample_interval_ns,
      time_ns: timeNs,
      depth_m: depthM,
      velocity_m_per_ns: velocity.value,
      velocity_source: velocity.source,
      dielectric: (0.2998 / velocity.value) ** 2,
      label,
      fit_r2: velocity.source === "fitted" ? state.fit?.r2 ?? null : null,
    });
    setState({ picks: await api.listPicks(state.job), status: null });
  } catch (error) {
    setState({ status: `Could not save target: ${error.message}` });
  }
}

async function deletePick(pick) {
  const state = getState();
  if (!state.job) return;
  try {
    await api.deletePick(state.job, pick.id);
    setState({ picks: await api.listPicks(state.job) });
  } catch (error) {
    setState({ status: `Could not delete target: ${error.message}` });
  }
}

/* Selecting a target is what arms the Interpretation panel. The previous answer is
 * dropped rather than left on screen under a different target's name. */
function selectPick(pick) {
  centreOn(pick);
  setState({ selectedPickId: pick.id, interpretation: null, interpretError: null });
}

async function interpretSelected(pick) {
  const state = getState();
  if (!state.job || state.interpreting) return;
  setState({ interpreting: true, interpretError: null });
  try {
    const interpretation = await api.interpretPick(state.job, pick.id);
    // Discard a reply the operator has already moved on from.
    if (getState().selectedPickId !== pick.id) return;
    setState({ interpretation, interpreting: false });
  } catch (error) {
    setState({ interpreting: false, interpretError: error.message });
  }
}

function centreOn(pick) {
  const state = getState();
  const channel = activeChannel(state);
  if (!channel || pick.channel !== channel.extension) return;
  const plot = view.plotRect();
  setState({
    view: {
      ...state.view,
      originTrace: pick.trace - plot.w / (2 * state.view.scaleX),
      originSample: pick.sample - plot.h / (2 * state.view.scaleY),
    },
  });
}

/* ---------- rendering ---------- */

function renderToolbar(state) {
  for (const button of dom.toolButtons.querySelectorAll(".tool")) {
    button.classList.toggle("active", button.dataset.tool === state.tool);
  }
  view.canvas.style.cursor = interact.cursorFor(state.tool);

  const channels = state.jobDetail?.channels ?? [];
  replace(
    dom.channelButtons,
    ...channels.map((channel) =>
      el("button", {
        class: `tool${state.channel === channel.extension ? " active" : ""}`,
        text: channel.extension,
        title: `${channel.label} · ${channel.sample_interval_ns} ns sampling`,
        onclick: () => selectChannel(channel.extension),
      }),
    ),
  );

  const candidateButton = document.getElementById("candidatesToggle");
  candidateButton.classList.toggle("active", state.showCandidates);
  candidateButton.disabled = state.candidates.length === 0;
  candidateButton.title = state.candidates.length
    ? `${state.candidates.length} auto-detected candidate regions — suggestions to look at, not labels`
    : "No candidate regions stored for this line (scripts/detect_candidates.py has not been run)";

  dom.jobBadge.textContent = state.job
    ? `${state.job} · ${state.channel ?? "—"}`
    : "no line open";
  dom.chainPill.textContent = state.status ?? processing.describeChain(state.processing);
  dom.chainPill.classList.toggle("active", Boolean(state.status));
}

function renderStatusBar(state) {
  const channel = activeChannel(state);
  const velocity = activeVelocity(state);
  const cursor = state.cursor;

  const timeNs = cursor && channel ? cursor.sample * channel.sample_interval_ns : null;
  dom.statX.textContent = cursor && channel ? `${(cursor.trace * channel.trace_spacing_m).toFixed(3)} m` : "—";
  dom.statT.textContent = timeNs != null ? `${timeNs.toFixed(2)} ns` : "—";
  dom.statZ.textContent = timeNs != null && velocity.value ? `${((velocity.value * timeNs) / 2).toFixed(3)} m` : "—";
  dom.statTrace.textContent = cursor ? String(Math.round(cursor.trace)) : "—";
  dom.statV.textContent = velocity.value ? `${velocity.value.toFixed(4)} m/ns` : "unavailable";

  const sampleIndex = cursor ? Math.round(cursor.sample) : null;
  const amplitude = state.ascan?.samples?.[sampleIndex];
  dom.statAmp.textContent = amplitude == null ? "—" : amplitude.toFixed(1);

  const measured = velocity.source === "fitted";
  dom.statProvenance.textContent =
    velocity.source === "unavailable" ? "depth: unavailable" : `depth: ${measured ? "measured" : velocity.source}`;
  dom.statProvenance.classList.toggle("measured", measured);
  dom.statProvenance.title = measured
    ? "Depth uses a velocity measured from a hyperbola in this line."
    : state.jobDetail?.axis_provenance?.depth ?? "";
}

function render() {
  const state = getState();
  const channel = activeChannel(state);

  dom.canvasEmpty.hidden = Boolean(channel);
  if (channel && state.job) {
    view.setImage(
      api.imageUrl(state.job, state.channel, state.processing, state.display, {
        width: channel.n_traces,
        height: channel.n_samples,
      }),
      scheduleRender,
    );
  } else {
    view.clearImage();
  }

  view.draw();
  // Driven from render, not only from cursor motion: the A-scan is stale after
  // any processing change too, and refreshAscan() no-ops when it is current.
  refreshAscan();
  ascan.draw(state.ascan, state.cursor ? Math.round(state.cursor.sample) : null);
  renderToolbar(state);
  renderStatusBar(state);
  processing.render();
  velocityPanel.render();
  targets.render();
  interpretPanel.render();
  info.render();
  docks.renderTree();
}

function handleResize() {
  view.resize();
  ascan.resize();
  // Re-fit only while the viewport is still the automatic one. A deliberate
  // zoom survives a window resize; a fit computed before the panels settled
  // does not, which is what makes the line fill the canvas on first paint.
  if (getState().viewFitted) resetView();
  scheduleRender();
}

/* ---------- boot ---------- */

async function boot() {
  modal.init();
  docks.init({ onOpenJob: openJob, onSelectChannel: selectChannel });
  docks.initAccordions();
  velocityPanel.init({ onVelocityChange: setManualVelocity, onSaveTarget: () => savePick(null) });
  targets.init({ onDelete: deletePick, onSelect: selectPick });
  interpretPanel.init({ onInterpret: interpretSelected });
  interact.install({
    onPick: (point) => savePick(point),
    onFitRegion: fitRegion,
    onCursor: refreshAscan,
  });

  for (const button of dom.toolButtons.querySelectorAll(".tool")) {
    button.addEventListener("click", () => setState({ tool: button.dataset.tool }));
  }
  document.getElementById("zoomIn").addEventListener("click", () => interact.zoomBy(1.25));
  document.getElementById("zoomOut").addEventListener("click", () => interact.zoomBy(1 / 1.25));
  document.getElementById("zoomFit").addEventListener("click", resetView);
  document.getElementById("candidatesToggle").addEventListener("click", () =>
    setState({ showCandidates: !getState().showCandidates }));

  dom.paletteSelect.addEventListener("change", (event) =>
    setState({ display: { ...getState().display, palette: event.target.value } }));
  dom.contrastRange.addEventListener("input", (event) => {
    dom.contrastOut.textContent = event.target.value;
    setState({ display: { ...getState().display, contrast: Number(event.target.value) } });
  });

  subscribe(scheduleRender);
  window.addEventListener("resize", handleResize);
  // Panel content lands after the first paint and changes the canvas box;
  // observing the element catches that, where a one-off measurement at boot
  // silently leaves the line drawn at the wrong scale.
  new ResizeObserver(handleResize).observe(view.canvas.parentElement);
  handleResize();

  const [jobs, palettes, reference] = await Promise.all([
    api.listJobs(),
    api.listPalettes(),
    api.listReference().catch(() => []),
  ]);
  replace(
    dom.paletteSelect,
    ...palettes.map((palette) =>
      el("option", {
        value: palette.name,
        text: palette.label,
        selected: palette.name === getState().display.palette,
      }),
    ),
  );
  setState({ jobs, palettes, reference });
  docks.renderLibrary();

  const requested = new URLSearchParams(window.location.search);
  const deepLink = requested.get("job");
  if (deepLink && jobs.includes(deepLink)) {
    await openJob(deepLink);
    const channel = requested.get("channel");
    if (channel && getState().jobDetail?.channels.some((c) => c.extension === channel)) {
      selectChannel(channel);
    }
  } else if (jobs.length === 1) {
    openJob(jobs[0]);
  }
}

boot();
