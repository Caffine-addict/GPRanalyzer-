/* The Processing panel — the chain from studio/processing.py, in run order.
 *
 * Laid out in the order the steps actually execute rather than grouped by
 * category, because in GPR processing the order *is* the semantics: gain
 * before filtering amplifies out-of-band noise, migration after gain migrates
 * a display artefact instead of the signal. Showing them in any other order
 * would invite an operator to reason about a pipeline that isn't the one
 * running. The order is fixed server-side; this panel only says what it is.
 */

import { checkboxRow, el, field, numberInput, replace, selectInput } from "./dom.js";
import { DEFAULT_PROCESSING, getState, setState } from "./state.js";

const container = document.getElementById("processingPanel");

const BACKGROUND_MODES = [
  ["none", "Off"],
  ["mean", "Mean trace (whole line)"],
  ["moving", "Moving window"],
];

const GAIN_MODES = [
  ["none", "Off (true amplitude)"],
  ["agc", "AGC"],
  ["linear", "Linear"],
  ["exponential", "Exponential"],
];

function update(patch) {
  setState({ processing: { ...getState().processing, ...patch } });
}

/** A one-line summary of everything switched on, for the toolbar pill. */
export function describeChain(processing) {
  const parts = [];
  if (processing.time_zero_sample > 0) parts.push(`t0@${processing.time_zero_sample}`);
  if (processing.dewow) parts.push("dewow");
  if (processing.background_removal !== "none") parts.push(`bg:${processing.background_removal}`);
  if (processing.bandpass) parts.push(`bp ${processing.bandpass_low_mhz}-${processing.bandpass_high_mhz}MHz`);
  if (processing.migrate) parts.push(`migrated @${processing.migration_velocity_m_per_ns.toFixed(4)} m/ns`);
  if (processing.gain !== "none") parts.push(`gain:${processing.gain}`);
  if (processing.stack_traces > 1) parts.push(`stack×${processing.stack_traces}`);
  return parts.length ? parts.join("  ·  ") : "raw — no processing applied";
}

export function render() {
  const p = getState().processing;

  replace(
    container,
    el("div", { class: "group-title", text: "1 · Time zero" }),
    field("First sample", numberInput(p.time_zero_sample, (v) => update({ time_zero_sample: Math.max(0, v) }), { min: 0 }), { unit: "smp" }),

    el("div", { class: "group-title", text: "2 · Dewow" }),
    checkboxRow("Remove low-frequency drift", p.dewow, (dewow) => update({ dewow })),
    p.dewow ? field("Window", numberInput(p.dewow_window_samples, (v) => update({ dewow_window_samples: v }), { min: 3 }), { sub: true, unit: "smp" }) : null,

    el("div", { class: "group-title", text: "3 · Background removal" }),
    field("Mode", selectInput(p.background_removal, BACKGROUND_MODES, (background_removal) => update({ background_removal }))),
    p.background_removal === "moving" ? field("Window", numberInput(p.background_window_traces, (v) => update({ background_window_traces: v }), { min: 3 }), { sub: true, unit: "tr" }) : null,

    el("div", { class: "group-title", text: "4 · Band-pass" }),
    checkboxRow("Frequency filter", p.bandpass, (bandpass) => update({ bandpass })),
    p.bandpass ? field("Low cut", numberInput(p.bandpass_low_mhz, (v) => update({ bandpass_low_mhz: v }), { min: 1 }), { sub: true, unit: "MHz" }) : null,
    p.bandpass ? field("High cut", numberInput(p.bandpass_high_mhz, (v) => update({ bandpass_high_mhz: v }), { min: 2 }), { sub: true, unit: "MHz" }) : null,

    el("div", { class: "group-title", text: "5 · Migration" }),
    checkboxRow("Kirchhoff migration", p.migrate, (migrate) => update({ migrate })),
    p.migrate ? field("Velocity", numberInput(p.migration_velocity_m_per_ns, (v) => update({ migration_velocity_m_per_ns: v }), { min: 0.01, max: 0.3, step: 0.001 }), { sub: true, unit: "m/ns" }) : null,
    p.migrate ? field("Aperture", numberInput(p.migration_aperture_traces, (v) => update({ migration_aperture_traces: v }), { min: 1 }), { sub: true, unit: "tr" }) : null,
    p.migrate ? el("p", { class: "panel-note", text: "Hyperbolas collapse to points only at the right velocity — over-collapsed 'smiles' mean it is set too high." }) : null,

    el("div", { class: "group-title", text: "6 · Gain" }),
    field("Mode", selectInput(p.gain, GAIN_MODES, (gain) => update({ gain }))),
    p.gain === "agc" ? field("Window", numberInput(p.agc_window_samples, (v) => update({ agc_window_samples: v }), { min: 3 }), { sub: true, unit: "smp" }) : null,
    p.gain === "linear" || p.gain === "exponential" ? field("Strength", numberInput(p.gain_exponent, (v) => update({ gain_exponent: v }), { min: 0, step: 0.1 }), { sub: true }) : null,
    p.gain !== "none" ? el("p", { class: "panel-note", text: "Gain makes deep reflectors visible and destroys relative amplitude. Evidence extraction reads raw traces, not this." }) : null,

    el("div", { class: "group-title", text: "7 · Stacking" }),
    field("Fold", numberInput(p.stack_traces, (v) => update({ stack_traces: Math.max(1, v) }), { min: 1 }), { unit: "tr" }),

    el("div", { class: "btn-row" }, [
      el("button", {
        class: "btn ghost",
        text: "Reset to raw",
        onclick: () => setState({ processing: { ...DEFAULT_PROCESSING } }),
      }),
    ]),
  );
}
