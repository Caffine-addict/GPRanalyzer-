/* The Interpretation panel — what a picked target most likely is.
 *
 * Two halves, kept visibly apart. The measured half (class and the rule behind it, depth
 * and its provenance, risk) is computed from the radargram and is the same every time.
 * The written half comes from a language model and is labelled as such, with the exact
 * evidence it was given available to read underneath. An operator must always be able to
 * tell which half is measurement and which is a model's words — that separation is the
 * whole reason this panel exists rather than a single block of prose.
 */

import { el, replace } from "./dom.js";
import { getState } from "./state.js";

const container = document.getElementById("interpretPanel");
let callbacks = {};

export function init(handlers) {
  callbacks = handlers;
}

const DEPTH_NOTE = {
  calibrated: "depth measured — velocity came from this target's own hyperbola",
  estimated: "depth estimated — no velocity was measured for this target",
  unavailable: "depth unavailable",
};

const ANSWERS = [
  ["What", "what"],
  ["Where", "where"],
  ["Why", "why"],
  ["Confidence", "how"],
  ["Recommended action", "recommended_action"],
];

function row(key, value) {
  return el("div", { class: "row" }, [el("span", { text: key }), el("span", { class: "mono", text: value })]);
}

export function render() {
  const state = getState();
  if (!state.job) {
    replace(container, el("p", { class: "empty", text: "Open a line to interpret a target." }));
    return;
  }

  const pick = state.picks.find((p) => p.id === state.selectedPickId) ?? null;
  if (!pick) {
    replace(container, el("p", { class: "empty", text: "Select a target in the Targets panel to interpret it." }));
    return;
  }

  const result = state.interpretation?.pick_id === pick.id ? state.interpretation : null;
  const nodes = [
    el("div", { class: "interp-head" }, [
      el("div", { class: "name", text: pick.label || `${pick.channel} target` }),
      el("div", { class: "meta", text: `${pick.depth_m.toFixed(2)} m · trace ${Math.round(pick.trace)}` }),
    ]),
    el("div", { class: "btn-row" }, [
      el("button", {
        class: "btn primary",
        text: state.interpreting ? "Reading…" : result ? "Interpret again" : "Interpret this target",
        disabled: state.interpreting,
        onclick: () => callbacks.onInterpret?.(pick),
      }),
    ]),
  ];

  if (state.interpretError) nodes.push(el("p", { class: "caveat", text: state.interpretError }));

  if (result) {
    nodes.push(
      el("div", { class: "interp-measured" }, [
        row("class", result.class),
        row("depth", `${result.depth_m.toFixed(3)} m`),
        row("along line", `${result.position_m.toFixed(2)} m`),
        row("risk", `${result.risk_level} (${result.risk_score.toFixed(2)})`),
      ]),
      el("p", { class: "panel-note", text: `measured: ${result.class_rule}` }),
      el("p", { class: "panel-note", text: DEPTH_NOTE[result.depth_confidence] ?? result.depth_confidence }),
    );

    if (result.reasoning) {
      for (const [label, key] of ANSWERS) {
        nodes.push(
          el("div", { class: "interp-block" }, [
            el("div", { class: "interp-label", text: label }),
            el("p", { class: "interp-text", text: result.reasoning[key] }),
          ]),
        );
      }
      nodes.push(
        el("p", { class: "panel-note", text: `${result.model} · ${result.latency_ms} ms · written by a model, not measured` }),
      );
    } else {
      nodes.push(el("p", { class: "caveat", text: result.reasoning_error ?? "no answer from the model" }));
    }

    nodes.push(
      el("details", { class: "interp-evidence" }, [
        el("summary", { text: "Evidence the model was given" }),
        el("pre", { class: "mono", text: result.evidence }),
      ]),
    );
  }

  replace(container, ...nodes);
}
