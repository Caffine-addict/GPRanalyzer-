/* GPR Studio — boot and wiring.
 *
 * One render pass, driven off the store, batched into an animation frame. Every
 * panel reads the same state, so the number in the status bar, the depth on the
 * ruler and the velocity in the target list cannot disagree with each other.
 */

import * as api from "./api.js";
import * as theme from "./theme.js";
import * as ascan from "./ascan.js";
import * as docks from "./docks.js";
import * as documents from "./documents.js";
import * as fieldMode from "./field_mode.js";
import * as importDialog from "./import_dialog.js";
import * as info from "./panel_info.js";
import * as interact from "./interact.js";
import * as interpretPanel from "./panel_interpret.js";
import * as assistantPanel from "./panel_assistant.js";
import * as reviewsPanel from "./panel_reviews.js";
import * as assistantActions from "./assistant_ui.js";
import * as mapPanel from "./panel_map.js";
import * as mapView from "./map_view.js";
import * as menus from "./menus.js";
import * as modal from "./modal.js";
import * as processing from "./panel_processing.js";
import * as targets from "./panel_targets.js";
import * as velocityPanel from "./panel_velocity.js";
import * as view from "./view.js";
import * as view3d from "./view3d.js";
import * as drawingView from "./drawing_view.js";
import { DEFAULT_PROCESSING, activeChannel, activeVelocity, getState, setState, subscribe } from "./state.js";
import { el, replace } from "./dom.js";

/* Zooming and resizing change the size the radargram is drawn at many times a second; the image
 * is re-requested at the new size only once that settles, and is stretched in the meantime. */
const IMAGE_DEBOUNCE_MS = 150;
const MAX_IMAGE_PX = 4096; // studio/render.py MAX_RENDER_PX

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
let wantedImage = null;
let imageTimer = null;
/* The open line's processed samples, fetched once per line, channel and chain. The A-scan and the
 * Amp readout read the trace under the cursor out of it on the same frame as X/T/Z, so neither lags
 * the cursor, and neither can show a different signal from the radargram above it. */
let lineSamples = { key: null, line: null };

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
      picks: await api.listPicks(job),
      // Candidates are optional context: a job whose detector has never been
      // run is perfectly openable, just without an overlay to toggle.
      candidates: await api.listCandidates(job).catch(() => []),
    });
    resetView();
    syncLocation();
    await assistantActions.loadReviews(job);
    assistantActions.onLineChanged();
    if (mapView.isShown()) mapView.focusLine(job);
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
  setState({ channel: extension, hyperbola: null, fit: null, measure: null });
  resetView();
  syncLocation();
  assistantActions.onLineChanged();
}

function resetView() {
  const channel = activeChannel(getState());
  if (channel) setState({ view: view.fitView(channel), viewFitted: true });
}

const samplesKey = (state) => `${state.job}/${state.channel}/${JSON.stringify(state.processing)}`;

function loadLineSamples(state) {
  if (!state.job || !state.channel) return;
  const key = samplesKey(state);
  if (key === lineSamples.key) return; // loaded, loading, or failed for this chain: no retry until it changes
  lineSamples = { key, line: null };
  api.lineSamples(state.job, state.channel, state.processing)
    .then((line) => {
      if (lineSamples.key !== key) return; // the line or chain changed while this was loading
      lineSamples = { key, line };
      scheduleRender();
    })
    .catch((error) => setState({ status: `Could not read the traces: ${error.message}` }));
}

/** The trace under the cursor, as the A-scan draws it, or null. */
function traceUnderCursor(state) {
  const channel = activeChannel(state);
  const line = lineSamples.key === samplesKey(state) ? lineSamples.line : null;
  if (!line || !channel || !state.cursor) return null;
  const trace = Math.round(state.cursor.trace);
  if (trace < 0 || trace >= line.nTraces) return null;
  return {
    trace,
    distance_m: trace * channel.trace_spacing_m,
    sample_interval_ns: channel.sample_interval_ns,
    samples: line.data.subarray(trace * line.nSamples, (trace + 1) * line.nSamples),
  };
}

/** The radargram at the size it is drawn on screen, in device pixels, so it is not stretched. */
function requestImage(state, channel) {
  const ratio = window.devicePixelRatio || 1;
  const fit = (px) => Math.max(1, Math.min(MAX_IMAGE_PX, Math.round(px * ratio)));
  const url = api.imageUrl(state.job, state.channel, state.processing, state.display, {
    width: fit(channel.n_traces * state.view.scaleX),
    height: fit((channel.n_samples - view.timeZeroSample(state)) * state.view.scaleY),
  });
  if (url === wantedImage) return;
  wantedImage = url;
  clearTimeout(imageTimer);
  imageTimer = setTimeout(() => view.setImage(url, scheduleRender), IMAGE_DEBOUNCE_MS);
}

/* ---------- tool actions ---------- */

async function fitRegion(region) {
  const state = getState();
  if (!state.job || !state.channel) return;
  setState({ status: "Fitting hyperbola…" });
  try {
    const timeZeroSample = view.timeZeroSample(state);
    const result = await api.fitHyperbola(state.job, state.channel, { ...region, time_zero_sample: timeZeroSample });
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
        timeZeroSample,
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
      state.hyperbola.timeZeroSample,
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
  // Time is measured from time zero (the surface); `sample` stays a raw index for drawing.
  const t0 = overlay ? overlay.timeZeroSample : view.timeZeroSample(state);
  const timeNs = overlay ? overlay.apexTimeNs : (point.sample - t0) * channel.sample_interval_ns;
  if (timeNs <= 0) {
    setState({ status: "That point is above time zero (the ground surface) — no depth to put on it." });
    return;
  }
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
      sample: t0 + timeNs / channel.sample_interval_ns,
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
  docks.showTab("targets");
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

/* ---------- modes ---------- */

const MODE_KEY = "gpr-mode";

/* Interpret is the radargram workstation; Field is the whole site on the map. The mode lives in the
 * URL and in local storage, so a link or a reload comes back to the same place. */
function setMode(mode) {
  const next = mode === "field" ? "field" : "interpret";
  document.body.dataset.mode = next;
  for (const button of document.querySelectorAll(".mode-switch .mode")) {
    const on = button.dataset.mode === next;
    button.classList.toggle("active", on);
    button.setAttribute("aria-selected", String(on));
  }
  try { window.localStorage.setItem(MODE_KEY, next); } catch { /* not remembered */ }
  const query = new URLSearchParams(window.location.search);
  if (next === "field") query.set("mode", "field");
  else query.delete("mode");
  window.history.replaceState(null, "", query.toString() ? `?${query}` : window.location.pathname);
  if (next === "field") fieldMode.show();
  else handleResize();
}

/** From the field map to the radargram: open the line and channel, centred on the target. */
async function openAt(job, channel, chainageM) {
  setMode("interpret");
  if (getState().job !== job) await openJob(job);
  if (channel && channel !== getState().channel && getState().jobDetail?.channels.some((c) => c.extension === channel)) {
    selectChannel(channel);
  }
  setView("radargram");
  const state = getState();
  const active = activeChannel(state);
  if (!active || chainageM == null) return;
  const plot = view.plotRect();
  setState({ view: { ...state.view, originTrace: chainageM / active.trace_spacing_m - plot.w / (2 * state.view.scaleX) } });
  syncLocation();
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
  dom.jobBadge.classList.toggle("live", Boolean(state.job));
  dom.chainPill.textContent = state.status ?? processing.describeChain(state.processing);
  dom.chainPill.classList.toggle("active", Boolean(state.status));
}

function renderStatusBar(state, trace) {
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
  const amplitude = trace?.samples[sampleIndex];
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
    requestImage(state, channel);
  } else {
    clearTimeout(imageTimer);
    wantedImage = null;
    view.clearImage();
  }

  view.draw();
  loadLineSamples(state); // a no-op once this line and chain are loaded
  const trace = traceUnderCursor(state);
  ascan.draw(trace, state.cursor ? Math.round(state.cursor.sample) : null);
  renderToolbar(state);
  renderStatusBar(state, trace);
  processing.render();
  velocityPanel.render();
  targets.render();
  interpretPanel.render();
  assistantPanel.render();
  reviewsPanel.render();
  mapPanel.render();
  view3d.update(sceneSettings(state)); // a no-op unless the 3D view is open and its processing changed
  renderReviewBadge(state);
  info.render();
  docks.renderTree();
}

/* Radargram or Map in the middle of the screen. The map hides the radargram and its A-scan
 * rather than shrinking them, so neither is ever drawn at a misleading scale. */
function setView(name) {
  const onMap = name === "map";
  const on3d = name === "3d";
  const onDrawing = name === "drawing";
  for (const button of document.querySelectorAll("#viewToggle .tool")) {
    button.classList.toggle("active", button.dataset.view === name);
  }
  document.getElementById("canvasWrap").hidden = onMap || on3d || onDrawing;
  document.querySelector(".ascan-strip").hidden = onMap || on3d || onDrawing;
  drawingView.show(onDrawing);
  mapView.show(onMap, getState().job);
  const { job, channel } = getState();
  view3d.show(on3d, { ...sceneSettings(getState()), site: job?.includes("_") ? job.split("_")[0] : null, channel: channel ?? "RAD" });
  if (onMap) docks.showTab("map");
  else if (!on3d && !onDrawing) handleResize();
}

/* What the 3D curtains share with the 2D view, so both show the same processed signal and depths. */
const sceneSettings = (state) => ({
  processing: state.processing, display: state.display, timeZeroSample: view.timeZeroSample(state),
});

/** From the 3D view: open a line on the radargram at a chainage, or a claim's Review card. */
async function openFrom3d(job, channel, chainageM, showCandidates = false) {
  await openAt(job, channel, chainageM);
  if (showCandidates) setState({ showCandidates: true });
}

async function openRunFrom3d(run) {
  const first = run.targets[0];
  await openAt(first.job, getState().channel ?? "RAD", first.chainage_m);
  setState({ highlightRunId: run.id });
  docks.showTab("review");
}

async function openReviewFrom3d(job, channel, chainageM, reviewId) {
  await openAt(job, channel, chainageM);
  await assistantActions.loadReviews(job); // the line may have been open already, with claims from before
  const review = getState().reviews.find((r) => r.id === reviewId);
  if (!review) return setState({ status: "That claim is no longer stored for this line" });
  assistantActions.focusReview(review);
  docks.showTab("review");
}

/* Claims waiting for a supervisor, counted on the Review tab so they are never out of sight. */
function renderReviewBadge(state) {
  const badge = document.getElementById("reviewBadge");
  const waiting = state.reviews.filter((r) => r.status === "proposed").length;
  badge.hidden = waiting === 0;
  badge.textContent = waiting > 99 ? "99+" : String(waiting);
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


/* ---------- menu bar ---------- */

async function afterLineImported(job) {
  reviewsPanel.forgetSites(); // which sites exist may have changed
  setState({ jobs: await api.listJobs() });
  await openJob(job);
}

/* ---------- removing a line (studio/line_trash.py): recoverable, never a delete ---------- */

function confirmRemoveLine(job) {
  modal.open(
    el("h3", { text: `Remove ${job}?` }),
    el("p", { text: "The line leaves the project, the Map, Field mode and the 3D view. Its radar files and everything " +
      "recorded on it (picks, claims, survey points) are kept together in the removed_lines folder." }),
    el("p", { text: "You can put it back from File › Removed lines…" }),
    el("div", { class: "btn-row", style: "padding:0" }, [
      el("button", { class: "btn ghost", text: "Cancel", onclick: () => modal.close() }),
      el("button", { class: "btn bad", text: "Remove line", onclick: () => removeLine(job) }),
    ]),
  );
}

async function removeLine(job) {
  modal.close();
  try {
    await api.removeLine(job);
  } catch (error) {
    setState({ status: `Could not remove ${job}: ${error.message}` });
    return;
  }
  if (getState().job === job) {
    setState({ job: null, jobDetail: null, expandedJob: null, channel: null, hyperbola: null, fit: null, measure: null,
      picks: [], candidates: [], reviews: [], siteRuns: null });
    window.history.replaceState(null, "", window.location.pathname);
  }
  reviewsPanel.forgetSites(); // which sites exist may have changed
  setState({ jobs: await api.listJobs(), status: `Removed ${job}. Put it back from File › Removed lines…` });
}

async function showRemovedLines() {
  const list = el("ul", { class: "export-list" }, [el("li", { class: "panel-note", text: "Loading…" })]);
  modal.open(el("h3", { text: "Removed lines" }),
    el("p", { text: "Lines taken out of the project, with everything recorded on them. Restore puts a line back as it was." }),
    list,
    el("div", { class: "btn-row", style: "padding:0" }, [el("button", { class: "btn ghost", text: "Close", onclick: () => modal.close() })]));
  try {
    const entries = await api.removedLines();
    replace(list, ...(entries.length ? entries.map((e) => el("li", { class: "row" }, [
      el("span", { text: `${e.job} · removed by ${e.removed_by} · ${e.removed_at.replace(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2}).*/, "$1-$2-$3 $4:$5")} UTC` }),
      el("button", { class: "btn", text: "Restore", onclick: () => restoreLine(e) }),
    ])) : [el("li", { class: "panel-note", text: "No removed lines." })]));
  } catch (error) {
    replace(list, el("li", { class: "panel-note warn", text: `Could not list removed lines: ${error.message}` }));
  }
}

async function restoreLine(entry) {
  try {
    await api.restoreLine(entry.id);
  } catch (error) {
    setState({ status: `Could not restore ${entry.job}: ${error.message}` });
    modal.close();
    return;
  }
  modal.close();
  reviewsPanel.forgetSites(); // which sites exist may have changed
  setState({ jobs: await api.listJobs(), status: `Restored ${entry.job}.` });
  await openJob(entry.job);
}

/* A readable starting view: dewow, band-pass around the antenna's centre frequency, a windowed
 * background removal (a whole-line mean leaves vertical striping where the ground changes along
 * the line), then gain. The band is half to twice the centre frequency the file header records;
 * with none recorded, band-pass stays off and the Processing panel says why. Never a guessed band. */
function standardView(channel) {
  const centre = channel?.antenna_freq_mhz;
  return {
    ...DEFAULT_PROCESSING,
    dewow: true,
    background_removal: "moving", // over DEFAULT_PROCESSING.background_window_traces (51)
    gain: "agc",
    ...(centre ? { bandpass: true, bandpass_low_mhz: Math.round(centre / 2), bandpass_high_mhz: Math.round(centre * 2) } : {}),
  };
}

function installMenus() {
  const noLine = () => !getState().job;
  const tool = (name, label, key) => ({ label, shortcut: key, action: () => setState({ tool: name }) });
  menus.install({
    file: () => [
      { label: "Import files…", action: () => importDialog.open({ onLineImported: afterLineImported }) },
      { label: "Reference drawings…", action: () => importDialog.showReferences() },
      "-",
      ...getState().jobs.map((job) => ({ label: `Open ${job}`, action: () => openJob(job) })),
      "-",
      {
        label: "Export targets (CSV)",
        disabled: noLine(),
        hint: noLine() ? "Open a line first" : "",
        action: () => window.open(api.picksCsvUrl(getState().job), "_blank"),
      },
      "-",
      {
        label: noLine() ? "Remove line…" : `Remove ${getState().job}…`,
        disabled: noLine(),
        hint: noLine() ? "Open a line first" : "",
        action: () => confirmRemoveLine(getState().job),
      },
      { label: "Removed lines…", action: showRemovedLines },
    ],
    process: () => [
      { label: "Raw — no processing", action: () => setState({ processing: { ...DEFAULT_PROCESSING } }) },
      { label: "Standard view (dewow, band-pass, windowed background, AGC)",
        action: () => setState({ processing: standardView(activeChannel(getState())) }) },
      { label: "Standard view + migration",
        action: () => setState({ processing: { ...standardView(activeChannel(getState())), migrate: true } }) },
    ],
    view: () => [
      { label: "Zoom in", action: () => interact.zoomBy(1.25) },
      { label: "Zoom out", action: () => interact.zoomBy(1 / 1.25) },
      { label: "Fit line to window", shortcut: "F", action: resetView },
      "-",
      { label: `${getState().showCandidates ? "Hide" : "Show"} candidate regions`,
        disabled: noLine(), action: () => setState({ showCandidates: !getState().showCandidates }) },
      "-",
      ...(getState().jobDetail?.channels ?? []).map((channel) => ({
        label: `Channel: ${channel.label}`, action: () => selectChannel(channel.extension),
      })),
    ],
    tools: () => [
      tool("pan", "Pan / select", "H"),
      tool("pick", "Mark a target", "P"),
      tool("hyperbola", "Fit a hyperbola (velocity)", "V"),
      tool("measure", "Measure distance and depth", "M"),
    ],
  });
}

/* ---------- boot ---------- */

async function boot() {
  modal.init();
  theme.initToggle(document.getElementById("themeToggle"));
  window.addEventListener("gpr-theme", () => setState({})); // repaint the canvases in the new colours
  api.whoAmI().then(({ username }) => {
    document.getElementById("accountName").textContent = username;
    setState({ reviewer: username }); // decisions are recorded under the signed-in account
  }).catch(() => {});
  fieldMode.init({ openAt, view: () => ({ processing: getState().processing, display: getState().display }) });
  for (const button of document.querySelectorAll(".mode-switch .mode")) {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  }
  installMenus();
  docks.init({ onOpenJob: openJob, onSelectChannel: selectChannel });
  docks.initAccordions();
  docks.initInspectorTabs();
  mapPanel.init({ onShowMap: () => setView("map") });
  view3d.init({ openAt: openFrom3d, openReview: openReviewFrom3d, openRun: openRunFrom3d });
  for (const button of document.querySelectorAll("#viewToggle .tool")) {
    button.addEventListener("click", () => setView(button.dataset.view));
  }
  document.getElementById("welcomeOpen").addEventListener("click", () => {
    const first = getState().jobs[0];
    if (first) openJob(first);
  });
  document.getElementById("welcomeImport").addEventListener("click", () =>
    importDialog.open({ onLineImported: afterLineImported }));
  velocityPanel.init({ onVelocityChange: setManualVelocity, onSaveTarget: () => savePick(null) });
  targets.init({ onDelete: deletePick, onSelect: selectPick });
  interpretPanel.init({ onInterpret: interpretSelected });
  interact.install({
    onPick: (point) => savePick(point),
    onFitRegion: fitRegion,
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
  documents.render();

  const requested = new URLSearchParams(window.location.search);
  let savedMode = null;
  try { savedMode = window.localStorage.getItem(MODE_KEY); } catch { /* none */ }
  setMode(requested.get("mode") ?? (requested.get("job") ? "interpret" : savedMode) ?? "interpret");
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
