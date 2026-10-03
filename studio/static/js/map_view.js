/* The Map view — vendor utilities and georeferenced radar lines, from the Studio's GeoJSON.
 *
 * Nothing is placed here that the server did not place: every point comes from
 * /api/geo/vendor.geojson (interpolated from the vendor's printed Lat/Long) or
 * /api/jobs/<job>/geo.geojson (interpolated between a line's surveyed reference points), and
 * each popup says how the position was obtained and how far off it may be.
 *
 * Basemap is "none" by default so the Studio works offline on site; OpenStreetMap tiles are an
 * opt-in that needs internet. Leaflet (BSD-2) is vendored in /static/vendor/leaflet.
 */

import * as api from "./api.js";
import { el, replace } from "./dom.js";

const L = window.L;

export const UTILITY_COLOURS = {
  ELECTRIC: "#ff5d5d", WATER: "#38bdf8", SEWER: "#e879f9", UC: "#4ade80", OFC: "#fb923c",
  "STORM WATER": "#facc15", DRAIN: "#2dd4bf", METALLIC: "#cbd5e1", "PIPE LINE": "#a78bfa",
  GAS: "#fde047", TELECOM: "#f472b6",
};
const REVIEW_COLOURS = { proposed: "#f5a623", confirmed: "#34c77b" };

let map = null;
let renderer = null;
let osm = null;
const layers = { vendor: null, risk: null, lines: null };
const visible = { vendor: true, riskOnly: false, lines: true, osm: false };
let vendorData = null;
let clickHandler = null;
let fitted = false;

function fmt(value, digits = 2, unit = "") {
  return value == null ? "—" : `${Number(value).toFixed(digits)}${unit}`;
}

/** A popup body built from DOM nodes (never HTML strings: the values come from files and models). */
function popup(rows, link) {
  return el("div", { class: "map-popup" }, [
    ...rows.map(([key, value]) => el("div", { class: "row" }, [el("span", { text: key }), el("span", { class: "mono", text: value })])),
    link ? el("a", { href: link.href, target: "_blank", rel: "noopener", text: link.text }) : null,
  ]);
}

function vendorLayer(features, riskOnly) {
  const group = L.featureGroup();  // a featureGroup, not a layerGroup: it can report its bounds
  for (const feature of features) {
    const p = feature.properties;
    const risk = p.status === "reaches_safe_path";
    if (riskOnly && !risk) continue;
    const [lon, lat] = feature.geometry.coordinates;
    const marker = L.circleMarker([lat, lon], {
      renderer, radius: risk ? 7 : 4.5, weight: risk ? 2.5 : 1,
      color: risk ? "#ffffff" : "#0b0d11", fillColor: UTILITY_COLOURS[p.utility] ?? "#94a3b8", fillOpacity: 0.95,
    });
    marker.bindPopup(() => popup([
      ["Utility", p.utility],
      ["Depth", `${fmt(p.depth_m, 2, " m")} (vendor, ±30%)`],
      ["Chainage", `${fmt(p.chainage_m, 1, " m")} · sheet ${p.sheet}`],
      ["Bore depth", risk ? "could reach it (±30%)" : "clear"],
      ["Position", p.position_error_m == null ? "from the other side's points; road width not measured"
        : `±${fmt(p.position_error_m, 1, " m")} + label offset`],
      ["Read by", p.read_by === "ocr" ? "OCR (lower bound)" : "text layer"],
      ["Drawing", p.drawing],
    ], p.marked_pdf ? { href: `${api.documentUrl(p.marked_pdf)}#page=${p.sheet}`, text: "Open the marked drawing at this sheet ↗" } : null));
    group.addLayer(marker);
  }
  return group;
}

function lineLayer(collections) {
  const group = L.featureGroup();
  for (const collection of collections) {
    for (const feature of collection.features) {
      const p = feature.properties;
      if (feature.geometry.type === "LineString") {
        const latlngs = feature.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
        L.polyline(latlngs, { renderer, color: "#4f9cff", weight: 4, opacity: 0.9 })
          .bindPopup(() => popup([
            ["Line", p.id],
            ["Placed by", p.position_method === "onboard_gps_track" ? "onboard GPS track" : "surveyed reference points"],
            ["Chainage", `${fmt(p.chainage_start_m, 1)}–${fmt(p.chainage_end_m, 1, " m")}`],
            ["Scale check", `${fmt((p.scale_ratio - 1) * 100, 1, "%")} survey vs wheel`],
            ["Straightness", p.straightness_m == null ? "unchecked (2 points)" : `${fmt(p.straightness_m, 2, " m")}`],
            ["Position", `±${fmt(p.position_error_m, 2, " m")}`],
          ])).addTo(group);
        continue;
      }
      const [lon, lat] = feature.geometry.coordinates;
      if (p.kind === "line_location") {
        // Frozen GPS: where the survey happened, not where the line ran — a circle, never a line.
        L.circle([lat, lon], { radius: p.uncertainty_radius_m, color: "#4f9cff", weight: 1.5, dashArray: "6 5",
          fillColor: "#4f9cff", fillOpacity: 0.08 }).bindPopup(() => popup([
          ["Line", p.id], ["Placed by", "onboard GPS — location only"],
          ["Within", `${fmt(p.uncertainty_radius_m, 1, " m")} of here`], ["Direction", "unknown (GPS froze)"],
          ["GPS fixes", String(p.gps_fixes)],
        ])).addTo(group);
        L.circleMarker([lat, lon], { renderer, radius: 4, color: "#4f9cff", fillColor: "#4f9cff", fillOpacity: 1, weight: 1 }).addTo(group);
        continue;
      }
      const colour = p.kind === "claim" ? REVIEW_COLOURS[p.review_status] ?? "#f5a623"
        : p.origin === "interpreter pick" ? "#f5b041" : "#94a3b8";
      L.circleMarker([lat, lon], {
        renderer, radius: p.kind === "claim" ? 6 : 4, weight: p.kind === "claim" ? 2 : 1,
        color: colour, fillColor: colour, fillOpacity: p.kind === "claim" && p.review_status !== "confirmed" ? 0.25 : 0.85,
        dashArray: p.kind === "claim" && p.review_status === "proposed" ? "3 3" : null,
      }).bindPopup(() => popup([
        ["Kind", p.kind === "claim" ? `model claim — ${p.review_status}` : p.origin],
        ["Line · channel", `${p.source} · ${p.channel}`],
        ["Chainage", fmt(p.chainage_m, 2, " m")],
        ...(p.kind === "claim" ? [["Identity", `${p.identity} (${p.confidence})`], ["Reviewer", p.reviewer ?? "—"]]
          : [["Depth", fmt(p.depth_m, 2, " m")], ["Shape", p.shape ?? p.label ?? "—"]]),
        ["Position", `±${fmt(p.position_error_m, 2, " m")}`],
      ])).addTo(group);
    }
  }
  return group;
}

function renderLegend() {
  const legend = document.getElementById("mapLegend");
  const counts = {};
  for (const f of vendorData?.features ?? []) counts[f.properties.utility] = (counts[f.properties.utility] ?? 0) + 1;
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  replace(legend,
    el("div", { class: "legend-title", text: "Vendor utilities" }),
    ...entries.map(([name, n]) => el("div", { class: "legend-row" }, [
      el("span", { class: "dot", style: `background:${UTILITY_COLOURS[name] ?? "#94a3b8"}` }),
      el("span", { text: name }), el("span", { class: "mono", text: String(n) }),
    ])),
    el("div", { class: "legend-row" }, [el("span", { class: "dot ring" }), el("span", { text: "could reach bore depth" })]),
    el("div", { class: "legend-title", text: "Radar lines" }),
    el("div", { class: "legend-row" }, [el("span", { class: "dot", style: "background:#4f9cff" }), el("span", { text: "line track" })]),
    el("div", { class: "legend-row" }, [el("span", { class: "dot dashed blue" }), el("span", { text: "line location only (GPS froze)" })]),
    el("div", { class: "legend-row" }, [el("span", { class: "dot", style: "background:#34c77b" }), el("span", { text: "confirmed claim" })]),
    el("div", { class: "legend-row" }, [el("span", { class: "dot dashed" }), el("span", { text: "claim awaiting review" })]),
  );
}

function renderOverlay(note) {
  const toggle = (key, label) => el("label", { class: "check" }, [
    el("input", { type: "checkbox", checked: visible[key], onchange: (e) => { visible[key] = e.target.checked; redraw(); } }),
    ` ${label}`,
  ]);
  replace(document.getElementById("mapOverlay"),
    el("div", { class: "map-card" }, [
      toggle("vendor", "Vendor utilities"),
      toggle("riskOnly", "Only bore-depth risks"),
      toggle("lines", "Radar lines"),
      toggle("osm", "OpenStreetMap (needs internet)"),
      note ? el("p", { class: "map-note", text: note }) : null,
    ]));
}

function redraw() {
  if (!map) return;
  for (const key of ["vendor", "risk"]) if (layers[key]) map.removeLayer(layers[key]);
  if (vendorData && visible.vendor) {
    layers.vendor = vendorLayer(vendorData.features, visible.riskOnly).addTo(map);
  }
  if (layers.lines) {
    if (visible.lines) layers.lines.addTo(map);
    else map.removeLayer(layers.lines);
  }
  if (visible.osm && !map.hasLayer(osm)) osm.addTo(map);
  if (!visible.osm && map.hasLayer(osm)) map.removeLayer(osm);
  renderOverlay(currentNote);
}

let currentNote = "";

function ensureMap() {
  if (map) return;
  renderer = L.canvas({ padding: 0.5 });
  map = L.map("map", { zoomControl: true, attributionControl: true, preferCanvas: true }).setView([12.975, 77.61], 14);
  osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 20, maxNativeZoom: 19, attribution: "© OpenStreetMap contributors",
  });
  L.control.scale({ metric: true, imperial: false }).addTo(map);
  map.on("click", (event) => clickHandler?.(event.latlng));
}

/** Load (or reload) everything and draw it. Called when the map is shown and after a save. */
export async function refresh() {
  ensureMap();
  map.invalidateSize();
  try {
    vendorData = vendorData ?? await api.vendorGeojson();
    const lines = await api.georeferencedLines();
    const placed = lines.filter((l) => l.georeferenced);
    const collections = await Promise.all(placed.map((l) => api.lineGeojson(l.job)));
    if (layers.lines) map.removeLayer(layers.lines);
    layers.lines = lineLayer(collections);
    const unplaced = vendorData.metadata?.unplaced ?? {};
    const skipped = Object.values(unplaced).reduce((a, b) => a + b, 0);
    const by = (source) => placed.filter((l) => l.source === source).length;
    currentNote = `${vendorData.features.length} vendor call-outs placed${skipped ? `, ${skipped} not placeable` : ""}. ` +
      `Radar lines: ${by("surveyed")} surveyed, ${by("gps_track")} from GPS track, ${by("gps_location")} location only (GPS froze), ` +
      `${lines.length - placed.length} not placed.`;
    redraw();
    renderLegend();
    if (!fitted) {
      const bounds = L.latLngBounds([]);
      for (const layer of [layers.vendor, layers.lines]) {
        if (layer && layer.getLayers().length) bounds.extend(layer.getBounds());
      }
      if (bounds.isValid()) map.fitBounds(bounds, { padding: [30, 30] });
      fitted = true;
    }
  } catch (error) {
    currentNote = `Could not load the map data: ${error.message}`;
    renderOverlay(currentNote);
  }
}

export function show(on) {
  document.getElementById("mapView").hidden = !on;
  if (on) refresh();
}

/** Zoom to one line's track, if it has one. */
export async function focusLine(job) {
  ensureMap();
  try {
    const collection = await api.lineGeojson(job);
    const track = collection.features.find((f) => f.geometry.type === "LineString");
    const spot = collection.features.find((f) => f.properties.kind === "line_location");
    if (track) map.fitBounds(L.geoJSON(track).getBounds(), { padding: [60, 60], maxZoom: 20 });
    else if (spot) map.setView([spot.geometry.coordinates[1], spot.geometry.coordinates[0]], 19);
  } catch {
    /* no track yet: nothing to zoom to */
  }
}

/** While set, a click on the map reports its lat/lon here (used to fill in a reference point). */
export function onMapClick(handler) {
  clickHandler = handler;
  document.getElementById("map")?.classList.toggle("picking", Boolean(handler));
}
