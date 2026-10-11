/* Field mode: a whole site at once — where things are, what is decided, what the crew found.
 *
 * The second of the Studio's two modes (Interpret is the radargram workstation). Left: the site and
 * its numbers. Centre: the site map (survey lines, suggested targets, possible runs, dig items with
 * their error circles) over the dig-items table. Right: whatever is selected, with what can be done
 * to it — confirm a suggestion onto the dig map, record what the crew found, or jump to its
 * radargram in Interpret.
 *
 * Every number shown comes from the server (studio/site_map.py, studio/dig_map.py); nothing here
 * computes a position or a depth. Suggestions and decisions never share a colour or a word.
 */

import * as api from "./api.js";
import { el, replace } from "./dom.js";

const L = window.L;
const COLOURS = { line: "#71717a", target: "#f59e0b", run: "#3b82f6", confirmed: "#22c55e", interpreted: "#a78bfa", select: "#ffffff" };
const QUICK_NAMES = ["Water pipe", "Sewer", "Storm drain", "Electric cable", "Telecom duct", "Gas pipe", "Unknown utility"];
// echo polarity against the direct wave, read on 2+ agreeing channels (studio/site_map.py)
const CONTRAST_TEXT = {
  denser: "denser (echo reversed: metal, water, wet ground)",
  lighter: "lighter (echo kept: air, as in a void or empty duct)",
  unreadable: "not readable on two channels",
};

const dom = {};
let handlers = {};
let started = false;
let map;
let groups = {};
let selectionRing = null;

const model = {
  sites: [], site: "", field: null, dig: null, reports: [], withInterpreted: true, loading: false, error: "",
  selected: null, // { kind: "target" | "dig_item" | "utility_run", props, latlng }
  layers: { lines: true, targets: true, runs: true, dig: true, errors: true, osm: false },
  tab: "dig", // the table below the map: "dig" items or "suggestions"
  sort: "channels", // suggestions: "channels" (most corroborated first) or "depth"
  message: "", busy: false,
  drafts: {}, // what was typed into a detail form, by selected id, so a repaint never wipes it
};

function draftOf(id) {
  model.drafts[id] ??= { identity: "", material: "", note: "", depth: "", checked: false };
  return model.drafts[id];
}

/** A text input bound to one field of a draft. */
function draftInput(draft, key, attrs) {
  return el("input", { class: "finput", ...attrs, value: draft[key], oninput: (e) => { draft[key] = e.target.value; } });
}

const fmt = (value, digits = 2) => (value == null || Number.isNaN(value) ? "—" : Number(value).toFixed(digits));
const shortId = (id) => id.split("#").pop();

/* ---------- data ---------- */

async function loadSites() {
  model.sites = await api.listSites();
  if (!model.site && model.sites.length) model.site = model.sites.reduce((a, b) => (b.lines > a.lines ? b : a)).site;
}

async function loadSite({ keepSelection = false } = {}) {
  if (!model.site) return;
  model.loading = true;
  model.error = "";
  paint();
  try {
    const [field, dig, reports] = await Promise.all([
      api.siteGeojson(model.site), api.digGeojson(model.site, model.withInterpreted), api.siteFieldReports(model.site),
    ]);
    Object.assign(model, { field, dig, reports });
    if (keepSelection && model.selected) {
      const all = [...field.features, ...dig.features];
      const again = all.find((f) => f.properties.id === model.selected.props.id && f.properties.kind === model.selected.kind);
      model.selected = again ? toSelection(again) : null;
    } else {
      model.selected = null;
    }
    drawMap(!keepSelection);
  } catch (error) {
    model.error = error.message;
  }
  model.loading = false;
  paint();
}

function toSelection(feature) {
  const g = feature.geometry;
  const latlng = g.type === "Point" ? [g.coordinates[1], g.coordinates[0]]
    : [g.coordinates[0][1], g.coordinates[0][0]];
  return { kind: feature.properties.kind, props: feature.properties, latlng };
}

/* ---------- map ---------- */

function ensureMap() {
  if (map || !L) return;
  map = L.map(dom.map, { zoomControl: true, preferCanvas: true }).setView([12.97, 77.6], 17);
  L.control.scale({ metric: true, imperial: false }).addTo(map);
  groups.osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 22, maxNativeZoom: 19, attribution: "© OpenStreetMap contributors",
  });
  map.on("click", () => select(null));
}

function select(feature, { fly = false } = {}) {
  model.selected = feature ? toSelection(feature) : null;
  model.message = "";
  if (selectionRing) { map.removeLayer(selectionRing); selectionRing = null; }
  if (model.selected && map) {
    selectionRing = L.circleMarker(model.selected.latlng, { radius: 13, color: COLOURS.select, weight: 2, fill: false, interactive: false }).addTo(map);
    if (fly) map.setView(model.selected.latlng, Math.max(map.getZoom(), 20));
  }
  paint();
}

function drawMap(fit) {
  ensureMap();
  if (!map || !model.field) return;
  for (const key of ["lines", "targets", "runs", "dig", "errors"]) {
    if (groups[key]) map.removeLayer(groups[key]);
    groups[key] = L.featureGroup();
  }
  const onPick = (feature) => (event) => { L.DomEvent.stopPropagation(event); select(feature); };
  const peekAt = (marker, job, channel, chainage, title) => marker.bindTooltip(() => peek(job, channel, chainage, title),
    { direction: "top", offset: [0, -8], className: "fpeek-tip", opacity: 1 });
  for (const f of model.field.features) {
    const p = f.properties;
    if (p.kind === "survey_line") {
      L.polyline(f.geometry.coordinates.map(([lon, lat]) => [lat, lon]), { color: COLOURS.line, weight: 2.5, opacity: 0.85 })
        .bindTooltip(p.job, { sticky: true }).addTo(groups.lines);
    } else if (p.kind === "utility_run") {
      L.polyline(f.geometry.coordinates.map(([lon, lat]) => [lat, lon]), { color: COLOURS.run, weight: 4, dashArray: "8 7" })
        .on("click", onPick(f)).addTo(groups.runs);
    } else if (p.kind === "target") {
      const [lon, lat] = f.geometry.coordinates;
      const strong = p.corroboration >= 2;
      peekAt(L.circleMarker([lat, lon], { radius: strong ? 5 : 3.5, color: "#09090b", weight: 1, fillColor: COLOURS.target,
        fillOpacity: strong ? 0.95 : 0.45 }), p.job, p.channels[0], p.chainage_m,
      `Suggestion ${shortId(p.id)} · ${fmt(p.depth_m)} m deep`).on("click", onPick(f)).addTo(groups.targets);
    }
  }
  for (const f of model.dig?.features ?? []) {
    const p = f.properties;
    const [lon, lat] = f.geometry.coordinates;
    const colour = p.status === "confirmed" ? COLOURS.confirmed : COLOURS.interpreted;
    L.circle([lat, lon], { radius: p.horizontal_error_m, color: colour, weight: 1, dashArray: "4 4", fill: false, interactive: false })
      .addTo(groups.errors);
    peekAt(L.circleMarker([lat, lon], { radius: 8, color: "#09090b", weight: 2, fillColor: colour, fillOpacity: 1 }),
      p.line, p.channel, p.chainage_m, `${p.identity || "Dig item"} · ${fmt(p.depth_range_m[0])}–${fmt(p.depth_range_m[1])} m`)
      .on("click", onPick(f)).addTo(groups.dig);
  }
  applyLayers();
  if (fit) {
    map.invalidateSize(); // the panel may have changed size since the map last measured itself
    const bounds = groups.lines.getLayers().length ? groups.lines.getBounds() : null;
    if (bounds?.isValid()) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 21 });
  }
  if (model.selected) select({ geometry: { type: "Point", coordinates: [model.selected.latlng[1], model.selected.latlng[0]] }, properties: model.selected.props });
}

function applyLayers() {
  if (!map) return;
  const order = ["lines", "runs", "targets", "errors", "dig"];
  for (const key of order) {
    if (!groups[key]) continue;
    const on = model.layers[key];
    if (on && !map.hasLayer(groups[key])) groups[key].addTo(map);
    if (!on && map.hasLayer(groups[key])) map.removeLayer(groups[key]);
  }
  if (model.layers.osm && !map.hasLayer(groups.osm)) groups.osm.addTo(map).bringToBack();
  if (!model.layers.osm && map.hasLayer(groups.osm)) map.removeLayer(groups.osm);
}

/** The hover preview: the radargram around a point, or a plain note when the line has none to show. */
function peek(job, channel, chainage, title) {
  const status = el("span", { class: "fpeek-status", text: "Loading radargram…" });
  const img = el("img", { class: "fpeek-img", alt: `Radargram of ${job} ${channel} around ${fmt(chainage)} m`,
    src: api.peekUrl(job, channel, chainage, handlers.view()),
    onload: () => status.remove(), onerror: () => { img.remove(); status.textContent = "No radargram for this point."; } });
  return el("div", { class: "fpeek" }, [
    el("b", { text: title }),
    el("span", { class: "fpeek-sub", text: `${job} · ${channel} · ${fmt(chainage)} m along · ±1.5 m shown with Interpret\u2019s current processing; ticks mark the point` }),
    img, status]);
}

/* ---------- actions ---------- */

async function confirmSuggestion(p, draft) {
  const identity = draft.identity.trim();
  if (!identity) return say("Say what it is first (pick one of the names or type your own).");
  if (p.near_surface && !draft.checked) return say("Tick that you have checked its radargram first.");
  model.busy = true;
  paint();
  try {
    const made = await api.confirmSuggestion(model.site, {
      target_id: p.id, trace: p.trace, time_ns: p.time_ns, acknowledge_near_surface: Boolean(p.near_surface && draft.checked),
      identity, material: draft.material, note: draft.note, // recorded under the signed-in account
    });
    delete model.drafts[p.id];
    await loadSite({ keepSelection: false });
    model.tab = "dig";
    const item = model.dig.features.find((f) => f.properties.id === made.dig_item_id);
    if (item) select(item, { fly: true });
    say(`Confirmed — ${shortId(made.dig_item_id)} is on the dig map.`);
  } catch (error) {
    say(`Not confirmed: ${error.message}`);
  }
  model.busy = false;
  paint();
}

async function fileReport(outcome, draft) {
  model.busy = true;
  paint();
  try {
    await api.reportFromField(model.site, {
      item_id: model.selected.props.id, outcome, note: draft.note,
      depth_found_m: outcome === "dug" && draft.depth ? Number(draft.depth) : null,
    });
    delete model.drafts[model.selected.props.id];
    await loadSite({ keepSelection: true });
    say(`Recorded: ${outcome.replace("_", " ")}.`);
  } catch (error) {
    say(`Not recorded: ${error.message}`);
  }
  model.busy = false;
  paint();
}

function say(text) {
  model.message = text;
  paint();
}

function openInInterpret(job, channel, chainage) {
  handlers.openAt?.(job, channel, chainage);
}

/* ---------- painting ---------- */

const row = (label, value, cls = "") => el("div", { class: `frow ${cls}` }, [el("span", { text: label }), el("span", { class: "mono", text: value })]);
const stat = (value, label, tone = "") => el("div", { class: `fstat ${tone}` }, [el("b", { text: String(value) }), el("span", { text: label })]);


function leftPanel() {
  const parts = [
    el("div", { class: "flabel", text: "Site" }),
    el("select", { class: "finput", disabled: model.loading, onchange: (e) => { model.site = e.target.value; loadSite(); } },
      model.sites.map((s) => el("option", { value: s.site, text: `${s.site} — ${s.lines} lines`, selected: s.site === model.site }))),
  ];
  if (model.loading) parts.push(el("p", { class: "fnote", text: "Assembling the site… the first time takes a few seconds per line." }));
  if (model.error) parts.push(el("p", { class: "fnote bad", text: model.error }));
  if (model.field && model.dig) {
    const m = model.field.metadata;
    const targets = model.field.features.filter((f) => f.properties.kind === "target");
    const digs = model.dig.features.map((f) => f.properties);
    const outcome = (o) => digs.filter((d) => d.field_outcome === o).length;
    const ev = m.runs_evidence;
    parts.push(
      el("div", { class: "fstats" }, [
        stat(`${m.lines_placed}/${m.lines_placed + m.lines_not_placed.length}`, "lines placed"),
        stat(targets.length, `suggestions · ${targets.filter((t) => t.properties.corroboration >= 2).length} multi-channel`),
        stat(digs.filter((d) => d.status === "confirmed").length, "confirmed dig items", "ok"),
        stat(digs.filter((d) => d.status !== "confirmed").length, "unconfirmed picks", "violet"),
        stat(outcome("found") + outcome("dug"), "found / dug"),
        stat(outcome("not_found"), "not found"),
      ]),
      m.dielectric ? el("div", { class: "fverdict warn" }, [
        el("b", { text: `Soil ε ${fmt(m.dielectric.value, 1)} for depths` }),
        el("span", { text: `${m.dielectric.basis}. The radar files record a dielectric that no soil has (e.g. 1.00, air), so depths use this instead.` }),
      ]) : null,
      el("div", { class: `fverdict ${ev.verdict.startsWith("runs beat") ? "ok" : "warn"}` }, [
        el("b", { text: ev.verdict.startsWith("runs beat") ? "Runs beat chance" : "Runs unconfirmed" }),
        el("span", { text: `${ev.runs} possible run${ev.runs === 1 ? "" : "s"}; chance alone gives ${ev.chance_mean}.` }),
      ]),
    );
    const attention = model.dig.metadata.needs_attention ?? [];
    if (attention.length) {
      parts.push(el("div", { class: "flabel", text: `Needs attention (${attention.length})` }),
        ...attention.map((a) => el("p", { class: "fnote bad", text: `${a.line}${a.ref ? ` #${a.ref}` : ""}: ${a.reason}` })));
    }
    if (m.lines_not_placed.length) {
      parts.push(el("p", { class: "fnote", text: `Not on the map (no usable GPS): ${m.lines_not_placed.join(", ")}` }));
    }
  }
  const layer = (key, label, colour) => el("label", { class: "fcheck" }, [
    el("input", { type: "checkbox", checked: model.layers[key], onchange: (e) => { model.layers[key] = e.target.checked; applyLayers(); } }),
    colour ? el("i", { class: "swatch", style: `background:${colour}` }) : null,
    el("span", { text: label }),
  ]);
  parts.push(
    el("div", { class: "flabel", text: "Layers" }),
    layer("lines", "Survey lines", COLOURS.line), layer("targets", "Suggested targets", COLOURS.target),
    layer("runs", "Possible runs", COLOURS.run), layer("dig", "Dig items", COLOURS.confirmed),
    layer("errors", "Position error circles"), layer("osm", "OpenStreetMap (needs internet)"),
    el("label", { class: "fcheck" }, [
      el("input", { type: "checkbox", checked: model.withInterpreted, onchange: (e) => { model.withInterpreted = e.target.checked; loadSite(); } }),
      el("span", { text: "Show interpreter picks not yet confirmed" }),
    ]),
  );
  if (model.site) {
    parts.push(
      el("div", { class: "flabel", text: "For the crew" }),
      el("a", { class: "fbtn primary", href: api.fieldViewUrl(model.site), target: "_blank", rel: "noopener", text: "Open phone view ↗" }),
      el("a", { class: "fbtn", href: api.digDownloadUrl(model.site, "pdf"), text: "Dig sheet (PDF)" }),
      el("div", { class: "fdl" }, [
        el("a", { href: api.digDownloadUrl(model.site, "csv"), text: "Dig CSV" }),
        el("a", { href: api.digDownloadUrl(model.site, "geojson"), text: "Dig GeoJSON" }),
        el("a", { href: api.siteDownloadUrl(model.site), text: "Site GeoJSON" }),
        el("a", { href: api.exportUrl("kml", `site:${model.site}`), text: "KML" }),
        el("a", { href: api.exportUrl("dxf", `site:${model.site}`), text: "DXF" }),
      ]),
    );
  }
  replace(dom.left, ...parts);
}

function reportHistory(id) {
  const mine = model.reports.filter((r) => r.item_id === id);
  if (!mine.length) return [el("p", { class: "fnote", text: "No field reports yet." })];
  return mine.slice().reverse().map((r) => el("div", { class: `freport ${r.outcome}` }, [
    el("b", { text: r.outcome.replace("_", " ") }),
    el("span", { text: `${r.reporter} · ${r.reported_at.replace("T", " ").slice(0, 16)}${r.depth_found_m != null ? ` · ${fmt(r.depth_found_m)} m deep` : ""}` }),
    r.note ? el("span", { class: "fnote", text: r.note }) : null,
  ]));
}

function targetDetail(p) {
  const draft = draftOf(p.id);
  const identity = draftInput(draft, "identity", { placeholder: "What is it?" });
  return [
    el("div", { class: "fhead" }, [el("h2", { text: `Suggestion ${shortId(p.id)}` }), el("span", { class: "fpill suggested", text: "detector suggestion" })]),
    p.near_surface ? el("p", { class: "fnote bad", text: "Near the surface (within 1.5 ns of the direct wave): it may be the surface itself or clutter on it. Check the radargram before relying on it." }) : null,
    row("Line", p.job), row("Along the line", `${fmt(p.chainage_m)} m`),
    row("Depth", `${fmt(p.depth_m)} ± ${fmt(p.depth_error_m)} m`), row("Map position", `± ${fmt(p.horizontal_error_m)} m`),
    row("Seen on", `${p.channels.join(", ")} (${p.corroboration} channel${p.corroboration === 1 ? "" : "s"})`),
    row("Depth from", p.depth_basis),
    row("Contrast with soil", CONTRAST_TEXT[p.contrast] ?? "—"),
    p.reading ? el("p", { class: "fnote", text: `Reading: ${p.reading}. From echo polarity and continuity, not an identity: records and digging decide what it is.` }) : null,
    el("button", { class: "fbtn", text: "Open its radargram in Interpret",
      onclick: () => openInInterpret(p.job, p.channels[0], p.chainage_m) }),
    el("div", { class: "flabel", text: "Confirm as a dig item" }),
    el("p", { class: "fnote", text: "Look at the radargram first. Confirming puts it on the dig map under your name." }),
    el("div", { class: "fchips" }, QUICK_NAMES.map((name) => el("button", { class: "fchip", text: name,
      onclick: () => { draft.identity = name; identity.value = name; } }))),
    identity, draftInput(draft, "material", { placeholder: "Material (optional)" }),
    draftInput(draft, "note", { placeholder: "Note (optional)" }),
    p.near_surface ? el("label", { class: "fcheck" }, [
      el("input", { type: "checkbox", checked: draft.checked, onchange: (e) => { draft.checked = e.target.checked; } }),
      " I've checked its radargram: this is a target, not the surface"]) : null,
    el("button", { class: "fbtn primary", disabled: model.busy, text: model.busy ? "Confirming…" : "Confirm onto the dig map",
      onclick: () => confirmSuggestion(p, draft) }),
  ];
}

function digDetail(p) {
  const confirmed = p.status === "confirmed";
  const draft = draftOf(p.id);
  return [
    el("div", { class: "fhead" }, [el("h2", { text: `Dig item ${shortId(p.id)}` }),
      el("span", { class: `fpill ${confirmed ? "confirmed" : "interpreted"}`, text: confirmed ? "confirmed" : "UNCONFIRMED" })]),
    confirmed ? null : el("p", { class: "fnote bad", text: "An interpreter's pick no supervisor has checked. Do not dig on this alone." }),
    p.near_surface ? el("p", { class: "fnote bad", text: "Near the surface (within 1.5 ns of the direct wave): it may be the surface itself or clutter on it. Check the radargram before relying on it." }) : null,
    row("What", [p.identity, p.material].filter(Boolean).join(" · ") || "—"),
    row("Depth", `${fmt(p.depth_range_m[0])}–${fmt(p.depth_range_m[1])} m`),
    row("Tape out", `${fmt(p.chainage_m)} m along ${p.line}`),
    row("Map position", `± ${fmt(p.horizontal_error_m)} m`),
    row("Decided by", `${p.decided_by}`), row("When", p.decided_at.replace("T", " ").slice(0, 16)),
    p.note ? row("Note", p.note) : null,
    el("button", { class: "fbtn", text: "Open its radargram in Interpret", onclick: () => openInInterpret(p.line, p.channel, p.chainage_m) }),
    el("div", { class: "flabel", text: "Field reports" }),
    ...reportHistory(p.id),
    el("div", { class: "flabel", text: "Record what the crew found" }),
    draftInput(draft, "depth", { type: "number", step: "0.01", min: "0", placeholder: "Depth found in the hole (m)" }),
    draftInput(draft, "note", { placeholder: "Note (optional)" }),
    el("div", { class: "factions" }, [
      el("button", { class: "fbtn found", disabled: model.busy, text: "Found", onclick: () => fileReport("found", draft) }),
      el("button", { class: "fbtn notfound", disabled: model.busy, text: "Not found", onclick: () => fileReport("not_found", draft) }),
      el("button", { class: "fbtn dug", disabled: model.busy, text: "Dug", onclick: () => fileReport("dug", draft) }),
    ]),
  ];
}

function runDetail(p) {
  return [
    el("div", { class: "fhead" }, [el("h2", { text: `Possible run ${shortId(p.id)}` }), el("span", { class: "fpill suggested", text: "suggested" })]),
    row("Crosses", `${p.lines} lines`), row("Depth", `${fmt(p.depth_m)} m (${fmt(p.depth_range_m[0])}–${fmt(p.depth_range_m[1])})`),
    row("Contrast with soil", CONTRAST_TEXT[p.contrast] ?? "—"),
    el("p", { class: "fnote", text: `Evidence: ${p.evidence}. A run is targets that line up from line to line at a steady depth; it is not a confirmed pipe.` }),
  ];
}

function rightPanel() {
  const s = model.selected;
  let body;
  if (!s) {
    body = [
      el("h2", { text: "Nothing selected" }),
      el("p", { class: "fnote", text: "Click a target, a run or a dig item on the map, or a row in the table." }),
      el("div", { class: "flegend" }, [
        el("p", {}, [el("i", { class: "swatch", style: `background:${COLOURS.target}` }), " Suggested target — the detector's, unreviewed"]),
        el("p", {}, [el("i", { class: "swatch", style: `background:${COLOURS.confirmed}` }), " Dig item — confirmed by a named supervisor"]),
        el("p", {}, [el("i", { class: "swatch", style: `background:${COLOURS.interpreted}` }), " Unconfirmed interpreter pick"]),
        el("p", {}, [el("i", { class: "swatch line", style: `background:${COLOURS.run}` }), " Possible run across lines"]),
      ]),
    ];
  } else if (s.kind === "target") {
    body = targetDetail(s.props);
  } else if (s.kind === "dig_item") {
    body = digDetail(s.props);
  } else {
    body = runDetail(s.props);
  }
  replace(dom.right, ...body, model.message ? el("p", { class: "fmessage", text: model.message }) : null);
}

function tableTabs(digCount, suggestionCount) {
  const tab = (key, text) => el("button", { class: `ftab${model.tab === key ? " active" : ""}`, text,
    onclick: () => { model.tab = key; table(); } });
  const sorter = model.tab === "suggestions" ? el("label", { class: "fsort" }, [
    el("span", { text: "Sort" }),
    el("select", { class: "finput small", onchange: (e) => { model.sort = e.target.value; table(); } }, [
      el("option", { value: "channels", text: "most channels first", selected: model.sort === "channels" }),
      el("option", { value: "depth", text: "shallowest first", selected: model.sort === "depth" }),
    ]),
  ]) : null;
  return el("div", { class: "ftabs" }, [tab("dig", `Dig items (${digCount})`), tab("suggestions", `Suggestions (${suggestionCount})`), sorter]);
}

function rowFor(feature, cells, kind) {
  const p = feature.properties;
  const active = model.selected?.kind === kind && model.selected.props.id === p.id;
  return el("tr", { class: active ? "active" : "", tabindex: "0", onclick: () => select(feature, { fly: true }),
    onkeydown: (e) => { if (e.key === "Enter") select(feature, { fly: true }); } }, cells);
}

function table() {
  if (!model.field) {
    replace(dom.table);
    return;
  }
  const digs = model.dig?.features ?? [];
  const suggestions = model.field.features.filter((f) => f.properties.kind === "target");
  const tabs = tableTabs(digs.length, suggestions.length);
  if (model.tab === "suggestions") {
    const ordered = [...suggestions].sort((a, b) => (model.sort === "depth"
      ? a.properties.depth_m - b.properties.depth_m
      : b.properties.corroboration - a.properties.corroboration || a.properties.depth_m - b.properties.depth_m));
    const header = el("tr", {}, ["Suggestion", "Line", "Along line", "Depth", "± map", "Channels"].map((h) => el("th", { text: h })));
    const rows = ordered.map((f) => {
      const p = f.properties;
      return rowFor(f, [
        el("td", { class: "mono", text: shortId(p.id) }), el("td", { text: p.job }),
        el("td", { class: "mono", text: `${fmt(p.chainage_m)} m` }),
        el("td", { class: "mono", text: `${fmt(p.depth_m)} ± ${fmt(p.depth_error_m)} m` }),
        el("td", { class: "mono", text: fmt(p.horizontal_error_m) }),
        el("td", {}, [`${p.corroboration} · ${p.channels.join(" ")} `, p.near_surface ? el("span", { class: "fpill warnpill", text: "near surface" }) : null]),
      ], "target");
    });
    replace(dom.table, tabs, el("table", { class: "ftable" }, [el("thead", {}, header), el("tbody", {}, rows)]));
    return;
  }
  if (!digs.length) {
    replace(dom.table, tabs, el("p", { class: "fnote pad", text: "No dig items yet. Pick a suggestion (map or the Suggestions tab) and confirm it, or confirm claims in Interpret › Review." }));
    return;
  }
  const header = el("tr", {}, ["Item", "Status", "What", "Line", "Tape out", "Depth", "± map", "Field"].map((h) => el("th", { text: h })));
  const rows = digs.map((f) => {
    const p = f.properties;
    return rowFor(f, [
      el("td", { class: "mono", text: shortId(p.id) }),
      el("td", {}, el("span", { class: `fpill ${p.status === "confirmed" ? "confirmed" : "interpreted"}`, text: p.status === "confirmed" ? "confirmed" : "UNCONFIRMED" })),
      el("td", {}, [[p.identity, p.material].filter(Boolean).join(" · ") || "—", " ",
        p.near_surface ? el("span", { class: "fpill warnpill", text: "near surface" }) : null]),
      el("td", { text: p.line }),
      el("td", { class: "mono", text: `${fmt(p.chainage_m)} m` }),
      el("td", { class: "mono", text: `${fmt(p.depth_range_m[0])}–${fmt(p.depth_range_m[1])} m` }),
      el("td", { class: "mono", text: fmt(p.horizontal_error_m) }),
      el("td", { text: p.field_outcome ? p.field_outcome.replace("_", " ") : "—" }),
    ], "dig_item");
  });
  replace(dom.table, tabs, el("table", { class: "ftable" }, [el("thead", {}, header), el("tbody", {}, rows)]));
}

function paint() {
  if (!started) return;
  leftPanel();
  rightPanel();
  table();
}

/* ---------- lifecycle ---------- */

export function init(callbacks) {
  handlers = callbacks;
  dom.left = document.getElementById("fieldLeft");
  dom.right = document.getElementById("fieldRight");
  dom.map = document.getElementById("fieldMap");
  dom.table = document.getElementById("fieldTable");
}

/** Called when Field mode is shown. Loads once; later visits keep their place. */
export async function show() {
  started = true;
  ensureMap();
  map?.invalidateSize();
  if (!model.sites.length) {
    try {
      await loadSites();
    } catch (error) {
      model.error = error.message;
    }
    if (model.site) await loadSite();
    else paint();
  } else {
    paint();
  }
}
