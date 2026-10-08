/* The field view: walk a crew to each dig item, then record what they found.
 *
 * Opens on a phone at /field?site=<site>. Shows the site's survey lines and its dig items (only
 * what people decided — studio/dig_map.py; interpreter picks are labelled UNCONFIRMED in words, not
 * only by colour), the phone's own position, and for the selected item the distance and direction.
 *
 * Guidance comes from geo_nav.guidance: it stops when the fix is too weak or too old, and inside
 * the combined GPS error it gives the tape-out instruction instead of a direction. The arrow turns
 * with the phone's compass; without a compass there is no arrow, only the compass-point text,
 * because an arrow drawn north-up points the wrong way for anyone not facing north.
 *
 * The report form is built once per selected item and never rebuilt by a GPS fix, so text typed
 * in the hole survives. Found / Not found / Dug go back as field reports: the excavation truth.
 */

import { initToggle } from "./theme.js";
import * as api from "./api.js";
import { el, replace } from "./dom.js";
import { arrowAngle, guidance } from "./geo_nav.js";

const L = window.L;
const site = new URLSearchParams(location.search).get("site") ?? "";
const dom = {
  title: document.getElementById("title"), gps: document.getElementById("gps"), banners: document.getElementById("banners"),
  target: document.getElementById("target"), items: document.getElementById("items"), attention: document.getElementById("attention"),
};
const COLOURS = { confirmed: "#22c55e", interpreted: "#a78bfa", line: "#a1a1aa", me: "#3b82f6" };
const colourOf = (status) => (status === "confirmed" ? COLOURS.confirmed : COLOURS.interpreted); // unknown fails closed
const label = (item) => (item.status === "confirmed" ? "confirmed" : "UNCONFIRMED");

let items = [];
let here = null; // {lat, lon, accuracy, timestamp}
let heading = null; // degrees clockwise from north, when the phone has a compass
let selected = null;
let reporter = "";
let busy = false;
let map;
let meMarker;
let meCircle;
let guideNode = null; // the only part of the card a GPS fix or compass change updates
let messageNode = null;

try { reporter = localStorage.getItem("gpr-field-reporter") ?? ""; } catch { /* private mode: ask each time */ }

const banner = (text, kind) => el("div", { class: `banner ${kind}`, text });

function toItem(feature) {
  const p = feature.properties;
  const [lon, lat] = feature.geometry.coordinates;
  return { ...p, lat, lon, tape: `${p.chainage_m.toFixed(2)} m along ${p.line} from its start mark` };
}

function sorted() {
  const usable = (item) => guidance(here, item).usable;
  if (!items.length || !usable(items[0])) return items; // no fix worth sorting by
  return [...items].sort((a, b) => guidance(here, a).metres - guidance(here, b).metres);
}

function select(item) {
  selected = item;
  if (item && map) map.setView([item.lat, item.lon], Math.max(map.getZoom(), 19));
  buildCard();
  paintList();
}

function paintList() {
  replace(dom.items, ...sorted().map((item) => {
    const g = guidance(here, item);
    return el("li", {}, el("button", { class: item === selected ? "selected" : "", onclick: () => select(item) }, [
      el("span", { text: `${item.id.split("#").pop()} · ${item.identity || "unnamed"}${item.field_outcome ? ` · ${item.field_outcome}` : ""}` }),
      el("span", { class: `pill ${item.status === "confirmed" ? "confirmed" : "interpreted"}`,
        text: g.usable ? `${label(item)} · ${g.metres.toFixed(0)} m` : label(item) }),
    ]));
  }));
}

function paintGuide() {
  if (!guideNode || !selected) return;
  const g = guidance(here, selected);
  const children = [];
  if (g.usable && !g.arrived && heading != null) {
    children.push(el("div", { class: "big" }, [
      el("span", { class: "arrow", text: "↑", style: `transform: rotate(${arrowAngle(g.bearing, heading)}deg)` }), ` ${g.text}`]));
  } else if (g.usable && !g.arrived) {
    children.push(el("div", { class: "big", text: g.text }));
    children.push(el("p", { class: "muted", text: "No compass on this phone: use the direction letters (N is up on the map)." }));
  } else if (g.usable) {
    children.push(el("div", { class: "big", text: "Here — tape it out" }), el("p", { text: g.text }));
  } else {
    children.push(el("p", { class: "banner warn", text: g.text }));
  }
  replace(guideNode, ...children);
}

function say(text) {
  if (messageNode) messageNode.textContent = text;
}

async function report(outcome) {
  if (!selected || busy) return;
  if (!reporter.trim()) {
    say("Enter your name first — every field report says who made it.");
    return;
  }
  const depthText = document.getElementById("depthFound")?.value ?? "";
  busy = true;
  try {
    const fix = guidance(here, selected).usable ? here : null; // a stale or weak fix is not recorded as where we were
    await api.reportFromField(site, {
      item_id: selected.id, outcome, reporter, note: document.getElementById("note")?.value ?? "",
      lat: fix?.lat ?? null, lon: fix?.lon ?? null, accuracy_m: fix?.accuracy ?? null,
      depth_found_m: outcome === "dug" && depthText ? Number(depthText) : null,
    });
    try { localStorage.setItem("gpr-field-reporter", reporter); } catch { /* not remembered */ }
    const done = `Recorded: ${outcome.replace("_", " ")} at ${selected.id.split("#").pop()}.`;
    await load();
    buildCard();
    paintList();
    say(done);
  } catch (error) {
    say(`Not recorded: ${error.message}`);
  } finally {
    busy = false;
  }
}

function buildCard() {
  if (!items.length) {
    replace(dom.target, el("p", { text: "No dig items on this site yet." }),
      el("p", { class: "muted", text: "A supervisor confirms targets in the Studio; they appear here." }));
    guideNode = null;
    return;
  }
  if (!selected) {
    replace(dom.target, el("p", { class: "muted", text: "Choose an item below." }));
    guideNode = null;
    return;
  }
  const item = selected;
  guideNode = el("div");
  messageNode = el("p", { class: "muted" });
  replace(dom.target,
    item.status === "confirmed"
      ? el("div", { class: "muted", text: `${item.id.split("#").pop()} · confirmed by ${item.decided_by}` })
      : banner(`${item.id.split("#").pop()} · UNCONFIRMED — an interpreter's pick no supervisor has checked. Do not dig on this alone.`, "bad"),
    guideNode,
    el("div", { class: "row" }, [el("span", { text: "Depth" }), el("span", { text: `${item.depth_range_m[0].toFixed(2)}–${item.depth_range_m[1].toFixed(2)} m` })]),
    el("div", { class: "row" }, [el("span", { text: "Tape out" }), el("span", { text: item.tape })]),
    el("div", { class: "row" }, [el("span", { text: "What" }), el("span", { text: [item.identity, item.material].filter(Boolean).join(" · ") || "—" })]),
    el("div", { class: "row" }, [el("span", { text: "Map position" }), el("span", { text: `± ${item.horizontal_error_m.toFixed(2)} m` })]),
    item.field_outcome ? el("div", { class: "row" }, [el("span", { text: "Last report" }), el("span", { text: `${item.field_outcome} — ${item.field_report}` })]) : null,
    el("input", { placeholder: "Your name", value: reporter, oninput: (e) => { reporter = e.target.value; } }),
    el("input", { id: "note", placeholder: "Note (optional)" }),
    el("input", { id: "depthFound", type: "number", step: "0.01", min: "0", placeholder: "Depth found in the hole (m), if dug" }),
    el("div", { class: "actions" }, [
      el("button", { class: "found", text: "Found", onclick: () => report("found") }),
      el("button", { class: "notfound", text: "Not found", onclick: () => report("not_found") }),
      el("button", { class: "dug", text: "Dug", onclick: () => report("dug") }),
    ]),
    messageNode);
  paintGuide();
}

function drawMap(field, dig) {
  if (!L) return;
  map = L.map("map", { zoomControl: true }).setView([12.97, 77.6], 18);
  L.control.scale({ metric: true, imperial: false }).addTo(map);
  const group = L.featureGroup().addTo(map);
  for (const f of field.features) {
    if (f.properties.kind === "survey_line") {
      L.polyline(f.geometry.coordinates.map(([lon, lat]) => [lat, lon]), { color: COLOURS.line, weight: 2 }).addTo(group);
    }
  }
  for (const f of dig.features) {
    const [lon, lat] = f.geometry.coordinates;
    const colour = colourOf(f.properties.status);
    L.circle([lat, lon], { radius: f.properties.horizontal_error_m, color: colour, weight: 1, fill: false }).addTo(group);
    L.circleMarker([lat, lon], { radius: 8, color: "#09090b", weight: 2, fillColor: colour, fillOpacity: 1 })
      .on("click", () => select(items.find((i) => i.id === f.properties.id) ?? null))
      .addTo(group);
  }
  if (group.getLayers().length) map.fitBounds(group.getBounds(), { padding: [30, 30], maxZoom: 20 });
}

async function load() {
  const dig = await api.digGeojson(site, true);
  items = dig.features.map(toItem);
  if (selected) selected = items.find((i) => i.id === selected.id) ?? null;
  const attention = dig.metadata.needs_attention ?? [];
  dom.attention.hidden = !attention.length;
  replace(dom.attention, el("div", { class: "muted", text: "Needs attention before digging (not shown as items)" }),
    ...attention.map((a) => el("p", { text: `${a.line}${a.ref ? ` #${a.ref}` : ""}: ${a.reason}` })));
  return dig;
}

function onFix(pos) {
  here = { lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy, timestamp: pos.timestamp };
  dom.gps.textContent = `Your position ±${here.accuracy.toFixed(1)} m`;
  if (map && L) {
    if (!meMarker) {
      meCircle = L.circle([here.lat, here.lon], { radius: here.accuracy, color: COLOURS.me, weight: 1 }).addTo(map);
      meMarker = L.circleMarker([here.lat, here.lon], { radius: 7, color: "#fff", weight: 2, fillColor: COLOURS.me, fillOpacity: 1 }).addTo(map);
    } else {
      meMarker.setLatLng([here.lat, here.lon]);
      meCircle.setLatLng([here.lat, here.lon]).setRadius(here.accuracy);
    }
  }
  if (!selected && items.length) select(sorted()[0]);
  paintGuide();
  paintList();
}

function watchCompass() {
  const onTurn = (event) => {
    // iOS gives a compass heading directly; elsewhere an absolute alpha counts anticlockwise.
    const value = event.webkitCompassHeading ?? (event.absolute && event.alpha != null ? (360 - event.alpha) % 360 : null);
    if (value == null) return;
    heading = value;
    paintGuide();
  };
  const listen = () => {
    window.addEventListener("deviceorientationabsolute", onTurn);
    window.addEventListener("deviceorientation", onTurn);
  };
  if (typeof DeviceOrientationEvent !== "undefined" && typeof DeviceOrientationEvent.requestPermission === "function") {
    // iOS asks for permission, and only from a tap.
    dom.banners.append(el("button", { class: "save dug", text: "Use the compass for the arrow", onclick: async (event) => {
      event.target.remove();
      if (await DeviceOrientationEvent.requestPermission().catch(() => "denied") === "granted") listen();
    } }));
  } else {
    listen();
  }
}

function watchPosition() {
  if (!window.isSecureContext) {
    dom.gps.textContent = "Position unavailable";
    dom.banners.append(banner("This page is not on a secure (HTTPS) connection, so the phone will not share its position. "
      + "The dig items, depths and tape-out distances below still work.", "bad"));
    return;
  }
  if (!navigator.geolocation) {
    dom.gps.textContent = "This browser has no location service.";
    return;
  }
  navigator.geolocation.watchPosition(onFix, (error) => {
    dom.gps.textContent = `Position unavailable: ${error.message}`;
  }, { enableHighAccuracy: true, maximumAge: 2000, timeout: 20000 });
  // Re-judge the last fix as it ages, so lost GPS stops guiding instead of freezing an old answer.
  setInterval(() => { paintGuide(); }, 2000);
  watchCompass();
}

async function start() {
  dom.title.textContent = site ? `Field view — ${site}` : "Field view";
  document.title = dom.title.textContent;
  if (!site) {
    replace(dom.target, el("p", { text: "Open this page from the Studio's Map panel (Open field view)." }));
    return;
  }
  try {
    const [field, dig] = await Promise.all([api.siteGeojson(site), load()]);
    drawMap(field, dig);
    select(items[0] ?? null);
    watchPosition();
  } catch (error) {
    replace(dom.target, el("p", { text: `Could not load the site: ${error.message}` }));
  }
}

initToggle(document.getElementById("themeToggle"));
start();
