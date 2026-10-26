/* The Drawing view: a survey company's CAD utility map, shown as drawn (studio/site_drawings.py).
 *
 * Leaflet in CRS.Simple: plain x/y in the drawing's own units and grid, north up as drawn, nothing
 * reprojected. The drawing is not tied to the map or to any radar line: that needs reference points
 * common to both, which a survey drawing on a local grid does not carry.
 *
 * Utility layers and the GPR survey corridor are drawn heavier than the background (buildings,
 * trees, kerbs), and every utility label reads into type, depth, diameter and quality level.
 */

import * as api from "./api.js";
import { el, replace } from "./dom.js";
import { token } from "./theme.js";

const MIN_TEXT_PX = 6; // a label smaller than this on screen is noise, so it is hidden until zoomed in
const CAP_HEIGHT = 0.72; // capital height as a share of the font size, for the narrow sans-serif used
const dom = {
  root: document.getElementById("drawingView"),
  panel: document.getElementById("drawingPanel"),
};

let map = null;
let renderer = null;
let drawings = [];
let current = "";
let data = null;
let note = "";
const groups = new Map(); // layer name -> { lines: L.Polyline | null, texts: L.Marker[], on: boolean }

export async function show(on) {
  dom.root.hidden = !on;
  if (!on) return;
  ensureMap();
  map.invalidateSize();
  try {
    drawings = await api.listDrawings();
  } catch (error) {
    note = `Could not list drawings: ${error.message}`;
  }
  if (!current && drawings.length) current = drawings[0].name;
  await load();
}

function ensureMap() {
  if (map) return;
  map = L.map("drawingMap", { crs: L.CRS.Simple, minZoom: -8, maxZoom: 9, zoomSnap: 0.25, zoomDelta: 0.5,
    attributionControl: false });
  renderer = L.canvas({ padding: 0.3 });
  map.on("zoomend viewreset", scaleTexts);
  window.addEventListener("gpr-theme", () => data && build());
}

async function load() {
  if (!current) { data = null; build(); return; }
  if (data?.name === current) { renderPanel(); return; }
  renderPanel(`Loading ${current}…`);
  try {
    data = await api.drawingGeometry(current);
    note = "";
    fitAll(); // the view first: Leaflet holds markers back until the map has one
    build();
  } catch (error) {
    data = null;
    renderPanel(`Could not open ${current}: ${error.message}`);
  }
}

/* ---------- drawing ---------- */

const ll = ([x, y]) => [y, x]; // CRS.Simple: latitude is y, longitude is x

function colourOf(layer) {
  const c = layer.colour.toLowerCase();
  return c === "#ffffff" || c === "#000000" ? token("--text") : c; // CAD white/black follow the theme
}

function build() {
  for (const group of groups.values()) {
    group.lines?.remove();
    for (const marker of group.texts) marker.remove();
  }
  groups.clear();
  if (!data) { renderPanel(); return; }
  for (const layer of data.layers) {
    const strong = layer.utility || layer.gpr_corridor;
    const rings = (data.lines[layer.name] ?? []).map((line) => line.map(ll));
    const lines = rings.length ? L.polyline(rings, { renderer, interactive: false, color: colourOf(layer),
      weight: layer.gpr_corridor ? 2.5 : strong ? 2 : 0.8, opacity: strong ? 1 : 0.6,
      dashArray: layer.gpr_corridor ? "6 4" : null }) : null;
    const texts = data.texts.filter((t) => t.layer === layer.name).map((t) => textMarker(t, layer));
    groups.set(layer.name, { lines, texts, on: true });
  }
  for (const group of groups.values()) {
    group.lines?.addTo(map);
    for (const marker of group.texts) marker.addTo(map);
  }
  scaleTexts();
  renderPanel();
}

function textMarker(t, layer) {
  // Hung from its CAD anchor (top-left, centre…) and turned about it, as the CAD program draws it.
  const label = el("span", { class: `dwg-text${t.utility ? " dwg-utility" : ""}`, text: t.text,
    style: `color:${colourOf(layer)};transform:rotate(${-t.rotation}deg) translate(${-t.ax * 100}%, ${-t.ay * 100}%)` });
  const marker = L.marker(ll([t.x, t.y]), { interactive: Boolean(t.utility), keyboard: false,
    icon: L.divIcon({ className: "dwg-anchor", html: label, iconSize: null, iconAnchor: [0, 0] }) });
  marker.userData = t;
  if (t.utility) {
    const u = t.utility;
    marker.bindPopup(() => el("div", { class: "dwg-popup" }, [
      el("b", { text: u.kind }),
      el("div", { text: `Depth to top: ${u.depth_m == null ? "not stated" : `~${u.depth_m} m`}` }),
      el("div", { text: `Diameter: ${u.diameter_mm == null ? "not stated" : `~${u.diameter_mm} mm`}` }),
      el("div", { text: `Quality level: ${u.quality_level}${u.quality_level === "QL-A" ? " (exposed and verified)" : ""}` }),
      el("div", { class: "muted", text: `Layer ${t.layer} · as drawn: "${t.text}"` }),
    ]));
  }
  return marker;
}

/** Text is drawn at its CAD height: scaled with zoom, hidden while too small to read. */
function scaleTexts() {
  if (!map || !data) return;
  const scale = 2 ** map.getZoom(); // CRS.Simple: one drawing unit is 2^zoom pixels
  for (const group of groups.values()) {
    for (const marker of group.texts) {
      const node = marker.getElement()?.firstElementChild;
      if (!node) continue;
      const px = (marker.userData.height * scale) / CAP_HEIGHT; // CAD text height is the capital letters' height
      node.style.fontSize = `${px}px`;
      node.style.display = group.on && px >= MIN_TEXT_PX ? "" : "none";
    }
  }
}

function fitAll() {
  if (!data?.bounds) return;
  const [[x0, y0], [x1, y1]] = data.bounds;
  map.fitBounds([[y0, x0], [y1, x1]], { paddingTopLeft: [20, 20], paddingBottomRight: [dom.panel.offsetWidth + 30, 20] });
}

function setLayer(name, on) {
  const group = groups.get(name);
  if (!group) return;
  group.on = on;
  if (group.lines) on ? group.lines.addTo(map) : group.lines.remove();
  for (const marker of group.texts) on ? marker.addTo(map) : marker.remove();
  scaleTexts();
}

function zoomTo(t) {
  map.setView(ll([t.x, t.y]), Math.max(map.getZoom(), 4));
  // `utilities` and `texts` arrive as separate copies of the same labels: match by place and words.
  const marker = groups.get(t.layer)?.texts.find((m) => m.userData.x === t.x && m.userData.y === t.y && m.userData.text === t.text);
  if (marker) { setLayer(t.layer, true); renderPanel(); marker.openPopup(); }
}

/* ---------- panel ---------- */

function layerRow(layer) {
  const group = groups.get(layer.name);
  return el("label", { class: "check dwg-layer", title: `${layer.lines} lines · ${layer.texts} labels` }, [
    el("input", { type: "checkbox", checked: group?.on ?? true, onchange: (e) => setLayer(layer.name, e.target.checked) }),
    el("span", { class: "swatch", style: `background:${colourOf(layer)}` }),
    el("span", { text: layer.name }),
  ]);
}

async function importFiles(files) {
  renderPanel(`Importing ${[...files].map((f) => f.name).join(", ")}…`);
  try {
    const { drawings: stored } = await api.importDrawings(files);
    drawings = await api.listDrawings();
    current = stored[0] ?? current;
    data = null;
    await load();
  } catch (error) {
    renderPanel(`Not imported: ${error.message}`);
  }
}

function renderPanel(message) {
  const picker = el("input", { type: "file", accept: ".dwg,.dxf", multiple: true, hidden: true,
    onchange: (e) => importFiles(e.target.files) });
  const head = [
    el("div", { class: "row" }, [
      drawings.length ? el("select", { "aria-label": "Drawing", onchange: (e) => { current = e.target.value; load(); } },
        drawings.map((d) => el("option", { value: d.name, text: d.name, selected: d.name === current }))) : el("span", { text: "No drawings yet" }),
      el("button", { class: "btn ghost", text: "Import…", title: "A CAD utility map (.dwg or .dxf)", onclick: () => picker.click() }),
      picker,
    ]),
  ];
  if (message || note || !data) {
    return replace(dom.panel, ...head, el("p", { class: "note", text: message || note
      || "Import a survey company's CAD utility map (.dwg or .dxf) to see it here as drawn." }));
  }
  const [[x0, y0], [x1, y1]] = data.bounds;
  const utilities = data.layers.filter((l) => l.utility || l.gpr_corridor);
  const others = data.layers.filter((l) => !l.utility && !l.gpr_corridor);
  const byKind = new Map();
  for (const t of data.utilities) {
    const key = `${t.utility.kind} · ${t.utility.quality_level}`;
    byKind.set(key, [...(byKind.get(key) ?? []), t]);
  }
  const levels = data.utilities.reduce((acc, t) => ({ ...acc, [t.utility.quality_level]: (acc[t.utility.quality_level] ?? 0) + 1 }), {});
  replace(dom.panel, ...head,
    el("div", { class: "row" }, [
      el("button", { class: "btn ghost", text: "Fit whole drawing", onclick: fitAll }),
      el("span", { class: "muted", text: `${(x1 - x0).toFixed(0)} × ${(y1 - y0).toFixed(0)} drawing units` }),
    ]),
    el("p", { class: "note warn", text: "No radar scan is imported for this site, and the drawing uses its own grid: "
      + "it cannot be placed on the map or against any radar line until reference points tie them together." }),
    el("p", { class: "note", text: `File declares units: ${data.units_declared}. Depths are to the top of each utility, `
      + "from ground level, as the drawing's notes say." }),
    el("h4", { text: `Utility labels (${data.utilities.length}) · ${Object.entries(levels).sort().map(([k, n]) => `${k} ${n}`).join(" · ")}` }),
    el("ul", {}, [...byKind.entries()].sort().map(([key, items]) => el("li", {}, [
      el("button", { text: `${key} ×${items.length}`, title: "Zoom to the first; click again for the next",
        onclick: () => { const t = items.shift(); items.push(t); zoomTo(t); } }),
      el("span", { class: "muted", text: [...new Set(items.map((t) => t.utility.depth_m).filter((d) => d != null))]
        .sort((a, b) => a - b).map((d) => `${d} m`).join(", ") || "no depth" }),
    ]))),
    el("p", { class: "note", text: "QL-A: exposed and verified by the surveyor. QL-B: detected geophysically. QL-D: from records only." }),
    el("h4", { text: `Utility layers (${utilities.length})` }), ...utilities.map(layerRow),
    el("details", {}, [el("summary", { class: "muted", text: `Other layers (${others.length}): buildings, roads, trees…` }),
      ...others.map(layerRow)]),
  );
}
