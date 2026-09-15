/* The Line info panel — what the file says about itself, and what that is worth.
 *
 * The acquisition header is shown verbatim because it is the raw provenance,
 * but two of its fields silently underpin every depth on screen, so the caveat
 * sits directly beneath them rather than in documentation nobody opens mid-survey.
 */

import { el, replace } from "./dom.js";
import { activeChannel, getState } from "./state.js";

const container = document.getElementById("infoPanel");

/* Worth surfacing above the raw dump: identity, geometry, and the two fields
 * the depth axis depends on. */
const HEADER_HIGHLIGHTS = [
  ["INSTRUMENT", "Instrument"],
  ["ACQUISITION_DATE", "Date"],
  ["ACQUISITION_TIME", "Time"],
  ["SPR_MEDIUM_DIELECTRIC", "Dielectric (site setting)"],
  ["SPR_SAMPLING_INTERVAL", "Sampling interval"],
  ["SPR_SHAFT_INTERVAL", "Shaft interval"],
];

function definitionList(pairs) {
  return el(
    "dl",
    { class: "kv" },
    pairs.flatMap(([key, value]) => [el("dt", { text: key }), el("dd", { text: value, title: value })]),
  );
}

export function render() {
  const state = getState();
  const detail = state.jobDetail;
  const channel = activeChannel(state);

  if (!detail || !channel) {
    replace(container, el("p", { class: "empty", text: "No line open." }));
    return;
  }

  const header = detail.header ?? {};
  const geometry = [
    ["Channel", `${channel.extension} — ${channel.label}`],
    ["Traces", `${channel.n_traces}`],
    ["Samples", `${channel.n_samples}`],
    ["Line length", `${channel.line_length_m.toFixed(2)} m`],
    ["Trace spacing", `${channel.trace_spacing_m} m`],
    ["Time window", `${channel.time_window_ns.toFixed(1)} ns`],
    ["Depth range", channel.max_depth_m ? `0 – ${channel.max_depth_m.toFixed(2)} m` : "unavailable"],
    ["GPS fixes", `${detail.gps.length}`],
  ];

  replace(
    container,
    el("div", { class: "group-title", text: "Geometry" }),
    definitionList(geometry),
    el("div", { class: "caveat", text: `Distance axis: ${detail.axis_provenance.distance}.` }),
    el("div", { class: "caveat", text: `Depth axis: ${detail.axis_provenance.depth}.` }),
    el("div", { class: "group-title", text: "Acquisition header" }),
    definitionList(
      HEADER_HIGHLIGHTS.filter(([key]) => key in header).map(([key, label]) => [label, header[key]]),
    ),
    el("details", {}, [
      el("summary", { class: "group-title", style: "cursor:pointer", text: "All header fields" }),
      definitionList(Object.entries(header).map(([key, value]) => [key, value || "—"])),
    ]),
  );
}
