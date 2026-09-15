/* The Targets panel — the interpreted output of a line.
 *
 * Every row carries the provenance of its depth (measured / by hand / from
 * header) next to the depth itself, and so does every row of the CSV export.
 * A target list that leaves site without saying which depths were measured and
 * which were assumed is exactly the failure this project exists to prevent —
 * see the confidence labelling on Evidence in core/contracts.py.
 */

import { el, replace } from "./dom.js";
import { picksCsvUrl } from "./api.js";
import { getState } from "./state.js";

const container = document.getElementById("targetsPanel");
let callbacks = {};

export function init(handlers) {
  callbacks = handlers;
}

const SOURCE_LABEL = { fitted: "measured", manual: "by hand", assumed: "assumed" };

function targetRow(pick) {
  return el("div", { class: "target-row", onclick: () => callbacks.onSelect?.(pick) }, [
    el("div", { class: `swatch${pick.velocity_source === "fitted" ? " fitted" : ""}` }),
    el("div", { class: "main" }, [
      el("div", { class: "name", text: pick.label || `${pick.channel} target` }),
      el("div", { class: "meta", text:
        `${pick.depth_m.toFixed(2)} m · ${(pick.trace * 0 + pick.time_ns).toFixed(1)} ns · ` +
        `ε${pick.dielectric.toFixed(1)} · ${SOURCE_LABEL[pick.velocity_source]}` +
        (pick.fit_r2 != null ? ` · R²${pick.fit_r2.toFixed(2)}` : "") }),
    ]),
    el("button", {
      class: "del",
      text: "×",
      title: "Delete target",
      onclick: (event) => { event.stopPropagation(); callbacks.onDelete?.(pick); },
    }),
  ]);
}

export function render() {
  const state = getState();
  if (!state.job) {
    replace(container, el("p", { class: "empty", text: "Open a line to mark targets." }));
    return;
  }

  const picks = state.picks;
  const measured = picks.filter((p) => p.velocity_source === "fitted").length;

  replace(
    container,
    picks.length === 0
      ? el("p", { class: "empty", text: "No targets yet. Pick tool (P) marks one; the Hyperbola tool (V) measures one first." })
      : el("div", {}, picks.map(targetRow)),
    picks.length > 0
      ? el("p", { class: "panel-note", text:
          `${picks.length} target${picks.length === 1 ? "" : "s"} · ${measured} with a measured velocity` })
      : null,
    el("div", { class: "btn-row" }, [
      el("a", {
        class: "btn ghost",
        text: "Export CSV",
        href: picksCsvUrl(state.job),
        download: "",
        style: picks.length ? "line-height:23px;text-align:center;text-decoration:none" : "pointer-events:none;opacity:.45;line-height:23px;text-align:center;text-decoration:none",
      }),
    ]),
  );
}
