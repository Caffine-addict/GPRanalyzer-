/* Left dock: the project tree and the confirmed-hyperbola signature library.
 *
 * The library is not decoration. Interpreting a GPR line is a comparison task —
 * you decide whether what is on screen looks like a hyperbola off a pipe by
 * having seen hyperbolas off pipes. These 16 crops came off signed-off survey
 * deliverables for the same Bangalore corridor this dataset was recorded on
 * (reference/hyperbolas/README.md), so they are the closest thing to a
 * confirmed answer key this repo has. Keeping them one click from the canvas
 * is the point.
 *
 * They are a visual benchmark, not labels. Nothing here claims a class for a
 * crop, and nothing feeds them to a model.
 */

import { el, replace } from "./dom.js";
import { open as openModal } from "./modal.js";
import { getState, setState } from "./state.js";

const jobTree = document.getElementById("jobTree");
const signatureGrid = document.getElementById("signatureGrid");

let callbacks = {};

export function init(handlers) {
  callbacks = handlers;
}

export function renderTree() {
  const state = getState();
  if (state.jobs.length === 0) {
    replace(jobTree, el("p", { class: "empty", text: "No job folders found under the dataset directory." }));
    return;
  }

  const nodes = [];
  for (const job of state.jobs) {
    const expanded = state.expandedJob === job;
    nodes.push(
      el("button", {
        class: `tree-job${state.job === job ? " active" : ""}`,
        onclick: () => callbacks.onOpenJob?.(job),
      }, [
        el("span", { class: "caret", text: expanded ? "▼" : "▶" }),
        job,
      ]),
    );

    if (!expanded || !state.jobDetail || state.jobDetail.job !== job) continue;
    for (const channel of state.jobDetail.channels) {
      nodes.push(
        el("button", {
          class: `tree-channel${state.channel === channel.extension ? " active" : ""}`,
          onclick: () => callbacks.onSelectChannel?.(channel.extension),
        }, [
          el("span", { text: channel.label }),
          el("span", {
            class: "depth",
            text: channel.max_depth_m ? `${channel.max_depth_m.toFixed(1)}m` : "—",
            title: channel.max_depth_m
              ? "Depth range assuming the header dielectric and a picosecond sampling interval"
              : "No dielectric recorded — depth range unavailable",
          }),
        ]),
      );
    }
  }
  replace(jobTree, ...nodes);
}

export function renderLibrary() {
  const { reference } = getState();
  if (reference.length === 0) {
    replace(signatureGrid, el("p", { class: "empty", text: "Signature library unavailable." }));
    return;
  }

  replace(
    signatureGrid,
    ...reference.map((crop) =>
      el("figure", { onclick: () => showCrop(crop) }, [
        el("img", {
          src: `/api/reference/crops/${crop.id}.png`,
          alt: `Confirmed hyperbola ${crop.id}`,
          loading: "lazy",
        }),
        el("figcaption", { text: `${crop.sheet.includes("1") ? "S1" : "S2"}·${crop.callout}` }),
      ]),
    ),
  );
}

function showCrop(crop) {
  openModal(
    el("h3", { text: `Confirmed hyperbola — sheet ${crop.sheet}, call-out ${crop.callout}` }),
    el("img", { src: `/api/reference/crops/${crop.id}.png`, alt: `Confirmed hyperbola ${crop.id}`, style: "image-rendering:pixelated;max-height:52vh" }),
    el("p", { text: `Confirmed by: ${crop.confirmed_by}.` }),
    el("p", { text:
      crop.label_class
        ? `Recorded class: ${crop.label_class}.`
        : "No class or depth label: the deliverable's CAD call-outs carry both, but the crop-to-call-out mapping has not been confirmed, so nothing is asserted here." }),
    el("div", { class: "btn-row" }, [
      el("button", {
        class: "btn ghost",
        text: "View full deliverable sheet",
        onclick: () => showSheet(crop.sheet),
      }),
    ]),
  );
}

function showSheet(sheetName) {
  openModal(
    el("h3", { text: `Deliverable sheet — ${sheetName}` }),
    el("img", { src: `/api/reference/sheets/${sheetName}`, alt: `Survey deliverable sheet ${sheetName}`, style: "max-height:74vh" }),
    el("p", { text: "Utility plan with numbered call-outs, alongside the GPR crop that justified each one — the deliverable this project is ultimately meant to produce." }),
  );
}

/** Accordion headers in the right dock. */
export function initAccordions() {
  for (const panel of document.querySelectorAll(".panel.accordion")) {
    panel.querySelector(".accordion-head").addEventListener("click", () => {
      panel.dataset.open = panel.dataset.open === "true" ? "false" : "true";
      setState({}); // re-render so canvas sizing keeps up with the layout change
    });
  }
}
