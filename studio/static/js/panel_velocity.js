/* The Velocity panel — where an assumed depth axis becomes a measured one.
 *
 * Every depth this software reports is velocity x two-way time / 2. Right now
 * that velocity comes from the header's SPR_MEDIUM_DIELECTRIC — a number the
 * operator typed into the instrument on site — resting on top of the further
 * assumption that SPR_SAMPLING_INTERVAL is in picoseconds (docs/
 * COMPANY_QUESTIONS.md #2). Neither has been confirmed by the vendor.
 *
 * A hyperbola fit measures the velocity from the geometry of the data itself,
 * with no header value involved. So this panel does one thing beyond showing
 * the fit: it puts the measured permittivity next to the assumed one and says
 * whether they agree. Agreement across several targets is real evidence the
 * assumption stack holds; disagreement is the finding, not a bug to hide.
 */

import { el, rangeInput, readoutRows, replace } from "./dom.js";
import { activeChannel, activeVelocity, getState, setState } from "./state.js";

const container = document.getElementById("velocityPanel");
let callbacks = {};

const SPEED_OF_LIGHT_M_PER_NS = 0.2998;

/* Within this fraction, a measured permittivity and the header's assumed one
 * are the same answer to the precision either deserves — the fit's own scatter
 * across targets is comfortably wider than 15%. */
const AGREEMENT_TOLERANCE = 0.15;

export function init(handlers) {
  callbacks = handlers;
}

function sourceTag(source) {
  const labels = { fitted: "measured", manual: "set by hand", assumed: "from header", unavailable: "none" };
  return el("span", { class: `src-tag src-${source}`, text: labels[source] ?? source });
}

function agreementVerdict(measuredEps, assumedEps) {
  if (!assumedEps) {
    return el("div", { class: "verdict", text: "No dielectric in this file's header — nothing to compare against." });
  }
  const ratio = Math.abs(measuredEps - assumedEps) / assumedEps;
  if (ratio <= AGREEMENT_TOLERANCE) {
    return el("div", { class: "verdict ok" }, [
      `Measured ε ${measuredEps.toFixed(1)} vs header ε ${assumedEps.toFixed(1)} — consistent ` +
      `(${(ratio * 100).toFixed(0)}% apart). Independent support for the depth axis, including the ` +
      `picosecond sampling-interval assumption it rests on.`,
    ]);
  }
  return el("div", { class: "verdict flag" }, [
    `Measured ε ${measuredEps.toFixed(1)} vs header ε ${assumedEps.toFixed(1)} — ` +
    `${(ratio * 100).toFixed(0)}% apart. Either the site dielectric is wrong for this ground, or the ` +
    `sampling-interval unit is not picoseconds. Worth fitting more targets before concluding either.`,
  ]);
}

function fitReadout(fit, channel) {
  if (!fit.physically_plausible) {
    return el("div", { class: "readout bad" }, [
      ...readoutRows([
        ["velocity", `${fit.velocity_m_per_ns.toFixed(4)} m/ns`, "flag"],
        ["fit R²", fit.r2.toFixed(3)],
        ["ridge points", `${fit.n_inliers}/${fit.n_total}`],
      ]),
      el("div", { class: "verdict flag" }, [
        "Faster than light — this is not a real velocity. The fit has latched onto noise or onto two " +
        "unrelated targets. Try a tighter box around a single hyperbola.",
      ]),
    ]);
  }

  const quality = fit.r2 >= 0.85 ? "strong" : fit.r2 >= 0.6 ? "moderate" : "weak";
  return el("div", { class: `readout ${fit.r2 >= 0.85 ? "good" : ""}` }, [
    ...readoutRows([
      ["velocity", `${fit.velocity_m_per_ns.toFixed(4)} m/ns`, "ok"],
      ["permittivity", `ε ${fit.dielectric.toFixed(1)}`],
      ["apex depth", `${fit.depth_m.toFixed(3)} m`],
      ["apex time", `${fit.apex_time_ns.toFixed(2)} ns`],
      ["fit R²", `${fit.r2.toFixed(3)} (${quality})`],
      ["ridge points", `${fit.n_inliers}/${fit.n_total}`],
    ]),
    fit.r2 < 0.6
      ? el("div", { class: "verdict flag" }, [
          "Weak fit — these ridge points barely describe a hyperbola, so treat the velocity as indicative only.",
        ])
      : agreementVerdict(fit.dielectric, channel?.dielectric_assumed),
  ]);
}

export function render() {
  const state = getState();
  const channel = activeChannel(state);
  const inUse = activeVelocity(state);
  const overlay = state.hyperbola;

  const header = el("div", { class: "readout" }, [
    el("div", { class: "row" }, [
      el("span", { text: "driving depth axis" }),
      el("span", {}, [sourceTag(inUse.source)]),
    ]),
    ...readoutRows([
      ["velocity", inUse.value ? `${inUse.value.toFixed(4)} m/ns` : "unavailable", inUse.value ? null : "flag"],
      ["permittivity", inUse.value ? `ε ${((SPEED_OF_LIGHT_M_PER_NS / inUse.value) ** 2).toFixed(1)}` : "—"],
    ]),
  ]);

  if (!channel) {
    replace(container, el("p", { class: "empty", text: "Open a line to measure velocity." }));
    return;
  }

  if (!overlay) {
    replace(
      container,
      header,
      el("p", { class: "panel-note", text:
        "Pick the Hyperbola tool (V) and drag a box around a single hyperbola. The fit measures " +
        "velocity from its curvature — no header value involved." }),
    );
    return;
  }

  const slider = rangeInput(overlay.velocity, (value) => callbacks.onVelocityChange?.(value), {
    min: 0.03, max: 0.3, step: 0.0005,
  });

  replace(
    container,
    header,
    state.fit ? fitReadout(state.fit, channel) : null,
    el("div", { class: "group-title", text: "Adjust by eye" }),
    el("div", { class: "field" }, [
      slider,
      el("span", { class: "unit", text: `${overlay.velocity.toFixed(4)}` }),
    ]),
    el("p", { class: "panel-note", text:
      "Drag until the overlay sits on the hyperbola's limbs. Adjusting by hand replaces a measured " +
      "velocity with a judged one — the target list records which you used." }),
    el("div", { class: "btn-row" }, [
      el("button", {
        class: "btn primary",
        text: "Save as target",
        onclick: () => callbacks.onSaveTarget?.(),
      }),
      el("button", {
        class: "btn ghost",
        text: "Clear",
        onclick: () => setState({ hyperbola: null, fit: null }),
      }),
    ]),
  );
}
