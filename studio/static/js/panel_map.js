/* The Map tab — put the open line on the map, and export GeoJSON.
 *
 * A line is placed from two or more reference points whose map position is known (the onboard
 * GPS is measured broken: docs/pilot/GPS_DIAGNOSTIC.md). Each point ties a chainage along the line
 * to a Lat/Long, with how well that point is known and where it came from. Points are typed in,
 * or a row's "Pick" arms the map so the next click fills its Lat/Long.
 *
 * The server validates and checks the points (studio/georef.py): wheel-vs-survey scale and, with
 * three or more points, how straight the line really was. Both results are shown, never hidden.
 */

import * as api from "./api.js";
import { el, replace } from "./dom.js";
import * as mapView from "./map_view.js";
import { getState } from "./state.js";

const container = document.getElementById("mapPanel");
let callbacks = {};
let loadedFor = null;
let server = null; // last GET/PUT /georef response
let draft = []; // rows being edited
let status = "";
let isError = false;
let picking = null; // index of the row waiting for a map click

export function init(handlers) {
  callbacks = handlers;
}

const blankRow = (chainage = "") => ({ chainage_m: chainage, lat: "", lon: "", accuracy_m: "0.05", source: "" });

async function load(job) {
  loadedFor = job;
  status = "";
  try {
    server = await api.getGeoref(job);
    draft = server.reference_points.length
      ? server.reference_points.map((p) => ({ ...p }))
      : [blankRow("0"), blankRow(server.line_length_m.toFixed(2))];
  } catch (error) {
    server = null;
    draft = [];
    status = error.message;
    isError = true;
  }
  paint();
}

function setPicking(index) {
  picking = index;
  if (index == null) {
    mapView.onMapClick(null);
  } else {
    callbacks.onShowMap?.();
    mapView.onMapClick((latlng) => {
      draft[index] = { ...draft[index], lat: latlng.lat.toFixed(7), lon: latlng.lng.toFixed(7) };
      setPicking(null);
    });
  }
  paint();
}

async function save() {
  const job = getState().job;
  try {
    server = await api.saveGeoref(job, draft.map((r) => ({
      chainage_m: Number(r.chainage_m), lat: Number(r.lat), lon: Number(r.lon),
      accuracy_m: Number(r.accuracy_m), source: r.source,
    })));
    draft = server.reference_points.map((p) => ({ ...p }));
    status = "Saved — the line is on the map.";
    isError = false;
    callbacks.onShowMap?.();
    await mapView.refresh();
    mapView.focusLine(job);
  } catch (error) {
    status = error.message;
    isError = true;
  }
  paint();
}

function input(row, key, attrs) {
  return el("input", {
    ...attrs, value: row[key] ?? "",
    oninput: (event) => { row[key] = event.target.value; },
  });
}

function pointRow(row, index) {
  return el("div", { class: `ref-row${picking === index ? " picking" : ""}` }, [
    el("div", { class: "ref-head" }, [
      el("span", { class: "ref-index", text: `Point ${index + 1}` }),
      el("button", { class: "link-btn", text: picking === index ? "Click the map…" : "Pick on map",
        onclick: () => setPicking(picking === index ? null : index) }),
      draft.length > 2 ? el("button", { class: "link-btn danger", text: "Remove",
        onclick: () => { draft.splice(index, 1); paint(); } }) : null,
    ]),
    el("div", { class: "ref-grid" }, [
      el("label", { text: "Chainage (m)" }), input(row, "chainage_m", { type: "number", step: "0.01" }),
      el("label", { text: "Latitude" }), input(row, "lat", { type: "number", step: "0.0000001", placeholder: "decimal degrees" }),
      el("label", { text: "Longitude" }), input(row, "lon", { type: "number", step: "0.0000001", placeholder: "decimal degrees" }),
      el("label", { text: "Accuracy (m)" }), input(row, "accuracy_m", { type: "number", step: "0.01", min: "0.01" }),
      el("label", { text: "Source" }), input(row, "source", { type: "text", placeholder: "total station / RTK / site plan" }),
    ]),
  ]);
}

function placementSummary() {
  const placement = server?.placement;
  if (!placement && server?.gps) {
    const title = { track: "On the map from its onboard GPS track", location: "Location only — the GPS froze", none: "No usable GPS" }[server.gps.kind];
    return el("div", { class: `readout${server.gps.kind === "track" ? "" : " bad"}` }, [
      el("div", { class: "verdict", text: title }),
      el("div", { class: "verdict", text: server.gps.reason }),
      el("div", { class: "verdict", text: "Surveyed reference points (below) replace the GPS as soon as they are saved." }),
    ]);
  }
  if (!placement) return el("p", { class: "panel-note", text: "Not on the map yet. Enter at least two reference points — the line's start and end — then save. A third near the middle lets the Studio check the line was straight." });
  return el("div", { class: "readout" }, [
    el("div", { class: "row" }, [el("span", { text: "position error" }), el("span", { text: `±${placement.position_error_m} m` })]),
    el("div", { class: "row" }, [el("span", { text: "survey vs wheel" }), el("span", { text: `${((placement.scale_ratio - 1) * 100).toFixed(1)}%` })]),
    el("div", { class: "row" }, [el("span", { text: "straightness" }), el("span", { text: placement.straightness_m == null ? "unchecked" : `${placement.straightness_m} m` })]),
    ...placement.warnings.map((w) => el("div", { class: "verdict flag", text: w })),
  ]);
}

function exports(job) {
  return el("div", { class: "export-list" }, [
    el("div", { class: "interp-label", text: "Export GeoJSON" }),
    job && (server?.placement || server?.gps?.kind === "track" || server?.gps?.kind === "location")
      ? el("a", { class: "btn ghost", href: api.lineGeojsonUrl(job), text: `${job} — line and targets` }) : null,
    el("a", { class: "btn ghost", href: api.vendorGeojsonUrl(), text: "Vendor utilities" }),
    el("a", { class: "btn ghost", href: api.allGeojsonUrl(), text: "Everything placed" }),
    el("div", { class: "interp-label", text: "Google Earth (KML) · CAD (DXF, metres, UTM zone chosen automatically)" }),
    job ? el("a", { class: "btn ghost", href: api.exportUrl("kml", job), text: `${job} — KML` }) : null,
    job ? el("a", { class: "btn ghost", href: api.exportUrl("dxf", job), text: `${job} — DXF` }) : null,
    el("a", { class: "btn ghost", href: api.exportUrl("kml", "all"), text: "Everything placed — KML" }),
    el("a", { class: "btn ghost", href: api.exportUrl("dxf", "lines"), text: "All radar lines — DXF" }),
    el("a", { class: "btn ghost", href: api.exportUrl("dxf", "vendor"), text: "Vendor utilities — DXF" }),
  ]);
}

function paint() {
  const job = getState().job;
  if (!job) {
    replace(container,
      el("p", { class: "panel-note", text: "Open a line to place it on the map. The vendor's utilities are already placed from their drawings." }),
      el("div", { class: "btn-row" }, [el("button", { class: "btn primary", text: "Show the map", onclick: () => callbacks.onShowMap?.() })]),
      exports(null));
    return;
  }
  replace(container,
    el("p", { class: "panel-note", text: `${job} · ${server ? `${server.line_length_m.toFixed(2)} m line` : "loading…"}` }),
    placementSummary(),
    ...draft.map(pointRow),
    el("div", { class: "btn-row" }, [
      el("button", { class: "btn", text: "Add point", onclick: () => { draft.push(blankRow()); paint(); } }),
      el("button", { class: "btn primary", text: "Save placement", onclick: save }),
    ]),
    status ? el("p", { class: isError ? "caveat" : "panel-note ok-note", text: status }) : null,
    el("div", { class: "btn-row" }, [el("button", { class: "btn ghost", text: "Show the map", onclick: () => { callbacks.onShowMap?.(); mapView.focusLine(job); } })]),
    exports(job));
}

export function render() {
  const job = getState().job;
  if (job !== loadedFor) {
    setPicking(null);
    if (job) {
      load(job);
      return;
    }
    loadedFor = null;
    server = null;
    draft = [];
  }
  if (!container.childElementCount) paint();
}
