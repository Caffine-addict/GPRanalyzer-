/* The site in 3D: every placed radar line as a curtain hanging along its real track (studio/site3d.py).
 *
 * x is east, y is up (depth downward, times the exaggeration), z is south, in metres from the site's
 * origin. Positions are exactly what the Map draws; a line whose GPS froze is a dashed cylinder of
 * its uncertainty, never a curtain, because its direction is unknown.
 *
 * Depth uses each line's velocity and the 2D view's time zero, so a target sits at the depth the
 * radargram's ruler gives it. Curtain images are the same processed PNG the 2D view shows, loaded
 * only once a curtain comes into view, a few at a time, and cached until the processing changes.
 */

import * as THREE from "../vendor/three/three.module.min.js";
import { OrbitControls } from "../vendor/three/OrbitControls.js";
import * as api from "./api.js";
import { el, replace } from "./dom.js";
import { token } from "./theme.js";
import { CANDIDATE_COLOURS, CANDIDATE_UNDIAGNOSED, REVIEW_STYLES } from "./view.js";

const MAX_LOADING = 4;         // curtain images in flight at once
const PICK_RADIUS_PX = 9;      // a marker within this of the pointer is the one under it
const CLICK_SLOP_PX = 4;       // more movement than this between press and release is an orbit, not a click
const MARKER_PRIORITY = { claim: 0, pick: 1, candidate: 2, detected: 3 }; // overlapping markers: the first a person acts on
const PICK_COLOUR = "#f59e0b"; // the radargram's pick colour
const CHANNELS = ["RAD", "RA1", "RA2"];
const SOURCE_TEXT = { surveyed: "surveyed points", gps_track: "GPS track" };
const VELOCITY_TEXT = { fitted: "measured (fitted)", site: "measured (site)", assumed: "assumed" };

const dom = {
  root: document.getElementById("sceneView"),
  canvas: document.getElementById("scene3d"),
  labels: document.getElementById("sceneLabels"),
  tip: document.getElementById("sceneTip"),
  panel: document.getElementById("scenePanel"),
};

let gl = null;             // { renderer, scene, camera, controls }
let world = null;          // everything placed from the scene; scaled in y by the exaggeration
let data = null;           // the last scene.json
let wanted = null;         // { site, channel, processing, display, timeZeroSample } the view should show
let shownKey = "";         // site + channel the built world is for
let textureKey = "";       // processing + display the curtain images are for
let sites = [];
let curtains = [];         // { line, mesh, url, state }
let markers = [];          // { info, line, local: Vector3 }
let labels = [];           // { node, local: Vector3 }
let loading = 0;
let frameQueued = false;
let handlers = {};
let runs = null;           // the site's runs.json: proposed / confirmed / rejected claims
let ground = null;         // the site's ground.json: each line's measured dielectric, and its sections
let runMeshes = [];        // { run, mesh }
const options = { exaggeration: 2, lines: true, targets: true, runs: true, ground: false };
/* Measured dielectric as a colour: drier (lower) blue through green to amber at the wet-ground
 * threshold (studio/site_map.WET_SOIL_EPS = 11) and red beyond. Not a soil type: see studio/ground.py. */
const GROUND_STOPS = [[4, "#3b82f6"], [7, "#22c55e"], [11, "#f59e0b"], [16, "#ef4444"]];
const WET_EPS = 11;
function groundColour(eps) {
  if (eps == null) return "#71717a";
  const [lo, hi] = [GROUND_STOPS.findLast(([v]) => v <= eps) ?? GROUND_STOPS[0], GROUND_STOPS.find(([v]) => v > eps) ?? GROUND_STOPS.at(-1)];
  const f = hi[0] === lo[0] ? 0 : (eps - lo[0]) / (hi[0] - lo[0]);
  return `#${new THREE.Color(lo[1]).lerp(new THREE.Color(hi[1]), f).getHexString()}`;
}
const RUN_STYLE = {
  proposed: { colour: REVIEW_STYLES.proposed.colour, opacity: 0.45 },
  confirmed: { colour: REVIEW_STYLES.confirmed.colour, opacity: 1 },
};
const textures = new Map(); // url -> THREE.Texture
const inFlight = new Set();  // urls being loaded

export function init(callbacks) {
  handlers = callbacks;
  window.addEventListener("gpr-theme", () => {
    if (!gl) return;
    gl.renderer.setClearColor(token("--bg-sink"));
    if (world) addGrid(); // its line colours are theme tokens
    requestFrame();
  });
  window.addEventListener("gpr-runs-changed", () => { if (!dom.root.hidden && data) loadRuns(); });
}

/** Show or hide the view; `view` says which site, channel and processing to show. */
export async function show(on, view) {
  dom.root.hidden = !on;
  if (!on) return;
  ensureGl();
  if (!sites.length) sites = await api.listSites().catch(() => []);
  const before = shownKey;
  await update({ ...view, site: view.site && sites.some((s) => s.site === view.site) ? view.site : sites[0]?.site });
  if (data && shownKey === before) await refresh(); // picks, claims or decisions may have changed in 2D meanwhile
}

/** Fetch the open site again and redraw it where the camera already is. Cheap once the site is picked. */
async function refresh() {
  const key = shownKey;
  try {
    const scene = await api.siteScene(wanted.site, wanted.channel, true);
    if (key !== shownKey) return;
    data = scene;
    build(false);
    await Promise.all([loadRuns(), loadGround()]);
  } catch (error) {
    renderPanel(`Could not refresh ${wanted.site}: ${error.message}`);
  }
}

/** Called on every app render while shown: reloads only what changed. */
export async function update(view) {
  if (dom.root.hidden || !(view.site ?? wanted?.site)) return;
  wanted = { ...wanted, ...view };
  const key = `${wanted.site}/${wanted.channel}`;
  if (key !== shownKey) {
    shownKey = key;
    renderPanel(`Loading ${wanted.site}…`);
    try {
      // Lines first, without the site picker's targets (seconds on a site not yet picked), then again with them.
      const current = () => `${wanted.site}/${wanted.channel}` === key; // false once a newer site or channel was asked for
      const quick = await api.siteScene(wanted.site, wanted.channel, false);
      if (!current()) return;
      data = quick;
      build(true);
      const full = await api.siteScene(wanted.site, wanted.channel, true);
      if (!current()) return;
      data = full;
      build(false);
      await Promise.all([loadRuns(), loadGround()]);
    } catch (error) {
      shownKey = "";
      renderPanel(`Could not load ${wanted.site}: ${error.message}`);
    }
    return;
  }
  const textureNow = JSON.stringify([wanted.site, wanted.channel, wanted.processing, wanted.display]);
  if (data && (textureNow !== textureKey || wanted.timeZeroSample !== world?.userData.timeZeroSample)) build(false);
}

/* ---------- three.js ---------- */

function ensureGl() {
  if (gl) return;
  const renderer = new THREE.WebGLRenderer({ canvas: dom.canvas, antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.setClearColor(token("--bg-sink"));
  const camera = new THREE.PerspectiveCamera(45, 1, 0.05, 5000);
  const controls = new OrbitControls(camera, dom.canvas);
  controls.screenSpacePanning = true;
  controls.addEventListener("change", requestFrame);
  gl = { renderer, scene: new THREE.Scene(), camera, controls };
  new ResizeObserver(resize).observe(dom.root);
  installPointer();
}

function resize() {
  const { clientWidth: w, clientHeight: h } = dom.root;
  if (!w || !h) return;
  gl.renderer.setSize(w, h, false);
  gl.camera.aspect = w / h;
  // Centre the scene in the part of the canvas the side panel leaves free.
  const panel = dom.panel.offsetWidth ? dom.panel.offsetWidth + 20 : 0;
  gl.camera.setViewOffset(w, h, panel / 2, 0, w, h);
  gl.camera.updateProjectionMatrix();
  requestFrame();
}

function requestFrame() {
  if (frameQueued) return;
  frameQueued = true;
  requestAnimationFrame(() => {
    frameQueued = false;
    gl.renderer.render(gl.scene, gl.camera);
    placeLabels();
    placeCompass();
    loadVisibleCurtains();
  });
}

/* ---------- depth ---------- */

/** Depth in metres of a time in the record (ns from its first sample), as the 2D ruler gives it. */
function depthAt(line, recordNs, dtNs) {
  const v = line.velocity.value;
  return v ? (v * Math.max(0, recordNs - wanted.timeZeroSample * dtNs)) / 2 : null;
}

function markerRecordNs(line, m) {
  return m.kind === "detected" ? m.time_ns + line.time_zero_ns : m.sample * m.dt_ns;
}

/** East/north at a chainage along the line's placed path. */
function along(line, chainage) {
  const path = line.path;
  let i = 1;
  while (i < path.length - 1 && path[i][0] < chainage) i += 1;
  const [c0, e0, n0] = path[i - 1];
  const [c1, e1, n1] = path[i];
  const f = c1 === c0 ? 0 : Math.min(1, Math.max(0, (chainage - c0) / (c1 - c0)));
  return [e0 + f * (e1 - e0), n0 + f * (n1 - n0)];
}

/* ---------- building the world ---------- */

function dispose(object) {
  object.traverse((o) => {
    o.geometry?.dispose();
    const materials = [].concat(o.material ?? []);
    for (const m of materials) m.dispose(); // textures are cached separately
  });
}

function build(refit) {
  if (world) { gl.scene.remove(world); dispose(world); }
  for (const node of labels) node.node.remove();
  const nowKey = JSON.stringify([wanted.site, wanted.channel, wanted.processing, wanted.display]);
  if (nowKey !== textureKey) {
    for (const t of textures.values()) t.dispose();
    textures.clear();
    textureKey = nowKey;
  }
  world = new THREE.Group();
  world.userData.timeZeroSample = wanted.timeZeroSample;
  world.scale.y = options.exaggeration;
  curtains = [];
  markers = [];
  labels = [];
  for (const line of data.lines) addCurtain(line);
  for (const spot of data.located) addLocated(spot);
  addMarkers();
  addGroundRibbons();
  addDepthScale();
  gl.scene.add(world);
  addGrid();
  if (refit) fitCamera();
  applyVisibility();
  buildRuns();
  renderPanel();
  requestFrame();
}

function addCurtain(line) {
  if (line.path.length < 2 || !line.velocity.value) return;
  const dt = line.sample_interval_ns;
  const bottom = depthAt(line, line.n_samples * dt, dt);
  const [from, to] = line.covered_m;
  const positions = [];
  const uvs = [];
  const index = [];
  line.path.forEach(([chainage, east, north], i) => {
    const u = (chainage - from) / Math.max(to - from, 1e-6);
    positions.push(east, 0, -north, east, -bottom, -north);
    uvs.push(u, 1, u, 0);
    if (i) index.push(2 * i - 2, 2 * i - 1, 2 * i, 2 * i, 2 * i - 1, 2 * i + 1);
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(index);
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: 0x3f3f46, side: THREE.DoubleSide }));
  mesh.userData = { line };
  mesh.name = "curtain";
  world.add(mesh);
  const top = new THREE.BufferGeometry().setFromPoints(line.path.map(([, e, n]) => new THREE.Vector3(e, 0, -n)));
  const edge = new THREE.Line(top, new THREE.LineBasicMaterial({ color: line.source === "surveyed" ? 0x22c55e : 0x3b82f6 }));
  edge.name = "curtain";
  world.add(edge);
  const url = api.curtainUrl(line.job, line.channel, from, to, wanted.processing, wanted.display);
  curtains.push({ line, mesh, url, state: textures.has(url) ? "done" : inFlight.has(url) ? "loading" : "idle" });
  if (textures.has(url)) applyTexture(mesh, textures.get(url));
  const [e, n] = line.path[0].slice(1);
  const measured = options.ground && ground?.site === data.site ? ground.lines.find((g) => g.job === line.job) : null;
  const value = measured ? (measured.dielectric == null ? " · ε not measured" : ` · ε ${measured.dielectric}`) : "";
  addLabel(line.job.replace(/^[^_]*_/, "") + value, new THREE.Vector3(e, 0, -n));
}

function addLocated(spot) {
  if (!spot.velocity.value) return;
  const dt = spot.sample_interval_ns;
  const bottom = depthAt(spot, spot.n_samples * dt, dt);
  const group = new THREE.Group();
  group.name = "curtain";
  group.position.set(spot.east, 0, -spot.north);
  const cylinder = new THREE.Mesh(
    new THREE.CylinderGeometry(spot.radius_m, spot.radius_m, bottom, 48, 1, true),
    new THREE.MeshBasicMaterial({ color: 0xf59e0b, transparent: true, opacity: 0.07, side: THREE.DoubleSide, depthWrite: false }));
  cylinder.position.y = -bottom / 2;
  group.add(cylinder);
  for (const y of [0, -bottom]) {
    const ring = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(
      Array.from({ length: 64 }, (_, k) => new THREE.Vector3(
        spot.radius_m * Math.cos((k / 64) * 2 * Math.PI), y, spot.radius_m * Math.sin((k / 64) * 2 * Math.PI)))),
    new THREE.LineDashedMaterial({ color: 0xf59e0b, dashSize: 0.6, gapSize: 0.4 }));
    ring.computeLineDistances();
    group.add(ring);
  }
  world.add(group);
  addLabel(`⚠ ${spot.job.replace(/^[^_]*_/, "")} · location only`, new THREE.Vector3(spot.east, 0, -spot.north), "warn");
}

function markerColour(m) {
  if (m.kind === "claim") return (REVIEW_STYLES[m.status] ?? REVIEW_STYLES.proposed).colour;
  if (m.kind === "pick") return PICK_COLOUR;
  if (m.kind === "candidate") return CANDIDATE_COLOURS[m.class] ?? CANDIDATE_UNDIAGNOSED;
  return CANDIDATE_UNDIAGNOSED; // the site picker suggests no class
}

function addMarkers() {
  const sets = { dot: { positions: [], colours: [] }, ring: { positions: [], colours: [] } };
  for (const line of data.lines) {
    if (!line.velocity.value) continue;
    for (const m of line.targets) {
      // Candidates and claims belong to one channel; the picker's targets are fused across channels.
      if (m.kind !== "detected" && m.channel !== line.channel) continue;
      const dt = m.dt_ns ?? line.sample_interval_ns;
      const depth = depthAt(line, markerRecordNs(line, m), dt);
      const [e, n] = along(line, m.chainage_m);
      const local = new THREE.Vector3(e, -depth, -n);
      const set = m.kind === "claim" ? sets.ring : sets.dot;
      set.positions.push(local.x, local.y, local.z);
      set.colours.push(...new THREE.Color(markerColour(m)).toArray());
      markers.push({ info: m, line, depth, local });
    }
  }
  for (const [shape, set] of Object.entries(sets)) {
    if (!set.positions.length) continue;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(set.positions, 3));
    geometry.setAttribute("color", new THREE.Float32BufferAttribute(set.colours, 3));
    const points = new THREE.Points(geometry, new THREE.PointsMaterial({
      size: shape === "ring" ? 15 : 8, sizeAttenuation: false, vertexColors: true, map: spriteTexture(shape),
      transparent: true, alphaTest: 0.3, depthTest: false }));
    points.name = "targets";
    points.renderOrder = 2; // drawn over the curtains: a target is inside the ground the curtain shows
    world.add(points);
  }
}

const sprites = {};
function spriteTexture(shape) {
  if (sprites[shape]) return sprites[shape];
  const canvas = Object.assign(document.createElement("canvas"), { width: 64, height: 64 });
  const c = canvas.getContext("2d");
  c.beginPath();
  c.arc(32, 32, shape === "ring" ? 24 : 28, 0, 2 * Math.PI);
  if (shape === "ring") { c.lineWidth = 10; c.strokeStyle = "#fff"; c.stroke(); } else {
    c.fillStyle = "#fff"; c.fill(); c.lineWidth = 6; c.strokeStyle = "#000"; c.stroke();
  }
  sprites[shape] = new THREE.CanvasTexture(canvas);
  return sprites[shape];
}

const niceStep = (span) => [0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10].find((s) => span / s <= 6) ?? 20;

/** A depth pole at the site's front-right corner, ticked in metres on the radargram ruler's zero. */
function addDepthScale() {
  const deepest = Math.max(0, ...data.lines.filter((l) => l.velocity.value).map((l) => depthAt(l, l.n_samples * l.sample_interval_ns, l.sample_interval_ns)));
  const box = new THREE.Box3().setFromObject(world);
  if (!deepest || box.isEmpty()) return;
  const x = box.max.x + 0.5; // the front-right corner at the default camera: clear of the canvas edge
  const z = box.max.z + 0.5;
  const step = niceStep(deepest);
  const points = [new THREE.Vector3(x, 0, z), new THREE.Vector3(x, -deepest, z)];
  for (let d = step; d <= deepest + 1e-6; d += step) points.push(new THREE.Vector3(x, -d, z), new THREE.Vector3(x + 0.4, -d, z));
  const pole = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(points),
    new THREE.LineBasicMaterial({ color: token("--text-dim") }));
  pole.name = "scale";
  world.add(pole);
  addLabel("0 m", new THREE.Vector3(x, 0, z), "tick");
  for (let d = step; d <= deepest + 1e-6; d += step) addLabel(`${+d.toFixed(2)} m`, new THREE.Vector3(x, -d, z), "tick");
}

/** The compass: which way north lies on screen right now. */
function placeCompass() {
  const compass = document.getElementById("sceneCompass");
  if (!compass || !world) return;
  const origin = gl.controls.target.clone();
  const a = origin.clone().project(gl.camera);
  const b = origin.clone().add(new THREE.Vector3(0, 0, -1)).project(gl.camera); // north is -z
  const right = (b.x - a.x) * dom.root.clientWidth; // north's direction on screen, in pixels
  const up = (b.y - a.y) * dom.root.clientHeight;
  compass.style.transform = `rotate(${(Math.atan2(right, up) * 180) / Math.PI}deg)`; // the arrow points up at 0°
}

function addGrid() {
  const old = gl.scene.getObjectByName("grid");
  if (old) { old.removeFromParent(); dispose(old); }
  const box = new THREE.Box3().setFromObject(world);
  if (box.isEmpty()) return;
  const size = Math.max(4, Math.ceil(Math.max(box.max.x - box.min.x, box.max.z - box.min.z) * 1.3));
  const grid = new THREE.GridHelper(size, size <= 200 ? size : Math.ceil(size / 10), token("--line-strong"), token("--line"));
  grid.name = "grid";
  grid.position.set((box.min.x + box.max.x) / 2, 0.001, (box.min.z + box.max.z) / 2);
  gl.scene.add(grid);
}

/** Bring one run into close view, so it can be inspected and clicked. */
function focusRun(run) {
  const found = runMeshes.find((r) => r.run.id === run.id);
  if (!found) return;
  const box = new THREE.Box3().setFromObject(found.mesh);
  const centre = box.getCenter(new THREE.Vector3());
  const span = Math.max(2.5, box.getSize(new THREE.Vector3()).length());
  const away = gl.camera.position.clone().sub(gl.controls.target).normalize();
  gl.controls.target.copy(centre);
  gl.camera.position.copy(centre).add(away.multiplyScalar(span * 1.6));
  gl.controls.update();
  requestFrame();
}

function siteRadius() {
  const box = new THREE.Box3().setFromObject(world);
  return box.isEmpty() ? 5 : box.getSize(new THREE.Vector3()).length() / 2;
}

function fitCamera() {
  const box = new THREE.Box3().setFromObject(world);
  if (box.isEmpty()) return;
  const centre = box.getCenter(new THREE.Vector3());
  const radius = Math.max(3, box.getSize(new THREE.Vector3()).length() / 2);
  gl.controls.target.copy(centre);
  gl.camera.position.copy(centre).add(new THREE.Vector3(0.45, 0.75, 1).normalize().multiplyScalar(radius * 2.2));
  gl.camera.near = radius / 200;
  gl.camera.far = radius * 50;
  gl.camera.updateProjectionMatrix();
  gl.controls.update();
}

function applyVisibility() {
  world.traverse((o) => {
    if (o.name === "curtain") o.visible = options.lines;
    if (o.name === "targets") o.visible = options.targets;
  });
  for (const label of labels) label.node.hidden = !options.lines;
  for (const { mesh } of runMeshes) mesh.visible = options.runs;
}

/* ---------- utility runs (studio/runs.py) as tubes through the targets they link ---------- */

/* ---------- ground: each line's measured dielectric (studio/ground.py) ---------- */

async function loadGround() {
  const site = data.site;
  try {
    const found = await api.siteGround(site);
    if (data?.site !== site) return;
    ground = found;
  } catch (error) {
    ground = { site, lines: [], sections: [], error: error.message };
  }
  if (options.ground) build(false); else renderPanel();
}

/** A coloured ribbon on the ground along each line, by its measured dielectric. */
function addGroundRibbons() {
  if (!options.ground || ground?.site !== data.site) return;
  const width = Math.max(0.25, siteRadius() / 80); // visible from a whole-site view, narrower than line spacing
  for (const line of data.lines) {
    const measured = ground.lines.find((g) => g.job === line.job);
    if (!measured || line.path.length < 2) continue;
    const positions = [];
    const index = [];
    line.path.forEach(([, e, n], i) => {
      const [, e0, n0] = line.path[Math.max(0, i - 1)];
      const [, e1, n1] = line.path[Math.min(line.path.length - 1, i + 1)];
      const len = Math.hypot(e1 - e0, n1 - n0) || 1;
      const [ox, oz] = [(-(n1 - n0) / len) * width / 2, (-(e1 - e0) / len) * width / 2]; // sideways, in plan
      positions.push(e + ox, 0.02, -n + oz, e - ox, 0.02, -n - oz);
      if (i) index.push(2 * i - 2, 2 * i - 1, 2 * i, 2 * i, 2 * i - 1, 2 * i + 1);
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geometry.setIndex(index);
    const ribbon = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
      color: groundColour(measured.dielectric), side: THREE.DoubleSide,
      transparent: measured.dielectric == null, opacity: measured.dielectric == null ? 0.5 : 1 }));
    ribbon.name = "curtain";
    world.add(ribbon);
  }
}

function groundPanel() {
  if (ground?.site !== data.site) return [el("h4", { text: "Ground" }), el("p", { class: "note", text: "Measuring each line's dielectric…" })];
  if (ground.error) return [el("h4", { text: "Ground" }), el("p", { class: "note warn", text: `Could not measure: ${ground.error}` })];
  const name = (job) => job.replace(/^[^_]*_/, "");
  const notMeasured = ground.lines.filter((g) => g.dielectric == null).map((g) => name(g.job));
  const placed = new Set(ground.sections.flatMap((z) => z.lines));
  const unplacedMeasured = ground.lines.filter((g) => g.dielectric != null && !placed.has(g.job));
  return [
    el("h4", { text: `Ground · ${ground.sections.length} section${ground.sections.length === 1 ? "" : "s"}` }),
    el("label", { class: "check" }, [el("input", { type: "checkbox", checked: options.ground,
      onchange: (e) => { options.ground = e.target.checked; build(false); } }), el("span", { text: "Colour lines by measured dielectric" })]),
    el("ul", {}, GROUND_STOPS.map(([v, c]) => legendRow(c, v >= WET_EPS ? `ε ${v}${v === WET_EPS ? " · wet ground from here" : ""}` : `ε ${v}`))),
    el("ul", {}, ground.sections.map((z, i) => el("li", { title: `Lines: ${z.lines.map(name).join(", ")}` +
      (z.outliers.length ? `\nOutlier (one line disagreeing, kept in the section): ${z.outliers.map(name).join(", ")}` : "") }, [
      el("span", {}, [el("span", { class: "swatch", style: `background:${groundColour(z.dielectric)}` }), `Section ${i + 1} · ${z.lines.length} lines`]),
      el("span", { class: z.wet ? "warn" : "muted", text: `ε ${z.dielectric} (${z.range[0]}–${z.range[1]}) · v ${z.velocity_m_per_ns}` }),
    ]))),
    unplacedMeasured.length ? el("p", { class: "note", text: `Measured but not placed in a section (no usable position): ` +
      unplacedMeasured.map((g) => `${name(g.job)} ε ${g.dielectric}`).join(", ") + "." }) : null,
    notMeasured.length ? el("p", { class: "note", text: `Not measured (fewer than ${ground.min_arches} clear hyperbolas): ${notMeasured.join(", ")}.` }) : null,
    el("p", { class: "note", text: `Each value comes from that line's own clearest hyperbolas: typical error about ${Math.round(ground.typical_error * 100)}%, ` +
      `so sections split only where two neighbouring lines differ by more than ${Math.round(ground.section_split * 100)}%. ` +
      "Dielectric mostly tracks water, not soil type. Depths still use each line's existing velocity." }),
  ];
}

async function loadRuns() {
  const site = data.site;
  try {
    const found = await api.siteRuns(site);
    if (data?.site !== site) return;
    runs = found;
  } catch (error) {
    runs = { site, runs: [], error: error.message };
  }
  buildRuns();
  renderPanel();
  requestFrame();
}

function buildRuns() {
  for (const { mesh } of runMeshes) { gl.scene.remove(mesh); dispose(mesh); }
  runMeshes = [];
  if (!runs || runs.site !== data?.site) return;
  const at = new Map(markers.map((m) => [m.info.id, m.local]));
  for (const run of runs.runs) {
    const style = RUN_STYLE[run.status];
    if (!style) continue; // rejected: listed in the panel, not drawn
    // Through the very markers it links (scaled like the world), so the tube never drifts off them.
    const points = run.targets.map((t) => {
      const local = at.get(t.id);
      return local ? new THREE.Vector3(local.x, local.y * options.exaggeration, local.z) : null;
    }).filter(Boolean);
    if (points.length < 2) continue;
    const path = new THREE.CurvePath();
    for (let i = 1; i < points.length; i += 1) path.add(new THREE.LineCurve3(points[i - 1], points[i]));
    // Thick enough to see and click from a whole-site view; drawn over the curtains like the markers,
    // since a run lies inside the ground the curtains show.
    const radius = Math.max(0.05, siteRadius() / 220);
    const mesh = new THREE.Mesh(new THREE.TubeGeometry(path, points.length * 12, radius, 10, false),
      new THREE.MeshBasicMaterial({ color: style.colour, transparent: true, opacity: style.opacity, depthTest: false, depthWrite: false }));
    mesh.renderOrder = 3;
    mesh.visible = options.runs;
    gl.scene.add(mesh);
    runMeshes.push({ run, mesh });
  }
}

/* ---------- curtain images: lazily, as they come into view ---------- */

function applyTexture(mesh, texture) {
  mesh.material.map = texture;
  mesh.material.color.set(0xffffff);
  mesh.material.needsUpdate = true;
}

function loadVisibleCurtains() {
  if (!world || !options.lines) return;
  gl.camera.updateMatrixWorld();
  const frustum = new THREE.Frustum().setFromProjectionMatrix(
    new THREE.Matrix4().multiplyMatrices(gl.camera.projectionMatrix, gl.camera.matrixWorldInverse));
  for (const curtain of curtains) {
    if (loading >= MAX_LOADING) break;
    if (curtain.state !== "idle" || !frustum.intersectsObject(curtain.mesh)) continue;
    curtain.state = "loading";
    loading += 1;
    inFlight.add(curtain.url);
    // The world may be rebuilt while this loads; whatever curtains show this image then get it.
    const live = () => curtains.filter((c) => c.url === curtain.url);
    new THREE.TextureLoader().load(curtain.url, (texture) => {
      loading -= 1;
      inFlight.delete(curtain.url);
      if (!live().length) { texture.dispose(); return; } // rebuilt for other settings: no longer shown
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.generateMipmaps = false;
      texture.minFilter = THREE.LinearFilter;
      textures.set(curtain.url, texture);
      for (const c of live()) { c.state = "done"; applyTexture(c.mesh, texture); }
      renderPanelStatus();
      requestFrame();
    }, undefined, () => {
      loading -= 1;
      inFlight.delete(curtain.url);
      for (const c of live()) c.state = "failed";
      renderPanelStatus();
      requestFrame();
    });
  }
  renderPanelStatus();
}

/* ---------- labels, hover and click ---------- */

/** A text label at a point in the world. Ticks and warnings are placed first and never hidden by others. */
function addLabel(text, local, kind = "") {
  const node = el("div", { class: `scene-label${kind ? ` ${kind}` : ""}`, text, title: text });
  dom.labels.append(node);
  const label = { node, local, always: kind === "tick" };
  if (kind) labels.unshift(label); else labels.push(label);
}

function toScreen(local) {
  const p = world.localToWorld(local.clone()).project(gl.camera);
  return { x: ((p.x + 1) / 2) * dom.root.clientWidth, y: ((1 - p.y) / 2) * dom.root.clientHeight, front: p.z < 1 };
}

function placeLabels() {
  if (!world) return;
  const placed = [];
  for (const { node, local, always } of labels) {
    const s = toScreen(local);
    node.style.left = `${s.x}px`;
    node.style.top = `${s.y}px`;
    // Where CSS draws it: a tick just right of its point, any other label centred above it.
    const w = node.offsetWidth || 60;
    const h = node.offsetHeight || 16;
    const box = always ? { x: s.x + 6, y: s.y - h / 2, w, h } : { x: s.x - w / 2, y: s.y - 1.2 * h, w, h };
    const clear = placed.every((o) => box.x + box.w + 3 <= o.x || o.x + o.w + 3 <= box.x || box.y + box.h + 2 <= o.y || o.y + o.h + 2 <= box.y);
    const show = s.front && (always || (options.lines && clear));
    node.style.display = show ? "" : "none";
    if (show) placed.push(box);
  }
}

function markerAt(x, y) {
  if (!options.targets) return null;
  // Where markers overlap (a claim sits on the target it is about), the one a person acts on wins.
  let best = null;
  for (const m of markers) {
    const s = toScreen(m.local);
    const d = Math.hypot(s.x - x, s.y - y);
    const rank = MARKER_PRIORITY[m.info.kind] ?? 9;
    if (s.front && d <= PICK_RADIUS_PX && (!best || rank < best.rank || (rank === best.rank && d < best.d))) best = { m, d, rank };
  }
  return best?.m ?? null;
}

function rayFrom(event) {
  const rect = dom.canvas.getBoundingClientRect();
  const ray = new THREE.Raycaster();
  ray.setFromCamera(new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1), gl.camera);
  return ray;
}

function runAt(event) {
  if (!options.runs || !runMeshes.length) return null;
  const hit = rayFrom(event).intersectObjects(runMeshes.map((r) => r.mesh))[0];
  return hit ? runMeshes.find((r) => r.mesh === hit.object).run : null;
}

function curtainAt(event) {
  if (!options.lines) return null;
  const hit = rayFrom(event).intersectObjects(curtains.map((c) => c.mesh))[0];
  if (!hit) return null;
  const { line } = hit.object.userData;
  const [from, to] = line.covered_m;
  return { line, chainage: from + hit.uv.x * (to - from), depth: depthAt(line, (1 - hit.uv.y) * line.n_samples * line.sample_interval_ns
    + wanted.timeZeroSample * line.sample_interval_ns * hit.uv.y, line.sample_interval_ns) };
}

function describe(m) {
  const titles = { // functions: each reads fields only its own kind has
    candidate: (i) => `Candidate · ${i.class ?? "no class suggested"}`, pick: (i) => `Pick · ${i.label || "unlabelled"}`,
    claim: (i) => `Claim · ${i.identity} · ${i.status}`,
    detected: (i) => `Picker target · seen on ${i.channels.join(", ")}`,
  };
  const what = titles[m.info.kind](m.info);
  const belowGround = m.info.kind === "detected" ? ` (${((m.line.velocity.value * m.info.time_ns) / 2).toFixed(2)} m below ground, from the line's measured time zero)` : "";
  return [el("b", { text: what }), el("br"),
    `${m.line.job} · ${m.info.chainage_m.toFixed(2)} m along · depth ${m.depth.toFixed(2)} m on the radargram's ruler${belowGround}`, el("br"),
    el("span", { class: "muted", text: `v ${m.line.velocity.value} m/ns, ${VELOCITY_TEXT[m.line.velocity.source]}` }), el("br"),
    el("span", { class: "muted", text: m.info.kind === "claim" ? "Click to open its Review card" : "Click to open it on the radargram" })];
}

function describeRun(run) {
  return [el("b", { text: `Utility run · ${run.status} · ${run.lines} lines` }), el("br"),
    `${run.depth_m} m below ground · confidence ${run.confidence} · straight to ${run.fit.rms_m} m`, el("br"),
    el("span", { class: run.chance.beats_chance ? "muted" : "warn", text: run.chance.verdict }), el("br"),
    el("span", { class: "muted", text: "Click to open its Review card" })];
}

function installPointer() {
  let down = null;
  dom.canvas.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY }; });
  dom.canvas.addEventListener("pointerleave", () => { dom.tip.hidden = true; });
  dom.canvas.addEventListener("pointermove", (e) => {
    if (!world || e.buttons) return;
    const rect = dom.canvas.getBoundingClientRect();
    const m = markerAt(e.clientX - rect.left, e.clientY - rect.top);
    const run = m ? null : runAt(e);
    const c = m || run ? null : curtainAt(e);
    dom.canvas.style.cursor = m || run || c ? "pointer" : "grab";
    if (!m && !run && !c) { dom.tip.hidden = true; return; }
    replace(dom.tip, ...(m ? describe(m) : run ? describeRun(run) : [el("b", { text: c.line.job }), el("br"),
      `${c.chainage.toFixed(2)} m along · depth ${c.depth.toFixed(2)} m`, el("br"),
      el("span", { class: "muted", text: "Click to open this trace on the radargram" })]));
    dom.tip.hidden = false;
    dom.tip.style.left = `${Math.min(e.clientX - rect.left + 14, rect.width - 310)}px`;
    dom.tip.style.top = `${e.clientY - rect.top + 14}px`;
  });
  dom.canvas.addEventListener("pointerup", (e) => {
    if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > CLICK_SLOP_PX || !world) return;
    const rect = dom.canvas.getBoundingClientRect();
    const m = markerAt(e.clientX - rect.left, e.clientY - rect.top);
    if (m) {
      const channel = m.info.kind === "detected" ? m.line.channel : m.info.channel;
      if (m.info.kind === "claim") handlers.openReview?.(m.line.job, channel, m.info.chainage_m, m.info.id);
      else handlers.openAt?.(m.line.job, channel, m.info.chainage_m, m.info.kind === "candidate");
      return;
    }
    const run = runAt(e);
    if (run) return handlers.openRun?.(run);
    const c = curtainAt(e);
    if (c) handlers.openAt?.(c.line.job, c.line.channel, c.chainage);
  });
}

/* ---------- side panel ---------- */

function check(label, key, title = "") {
  return el("label", { class: "check", title }, [
    el("input", { type: "checkbox", checked: options[key], onchange: (e) => { options[key] = e.target.checked; applyVisibility(); requestFrame(); } }),
    el("span", { text: label })]);
}

function legendRow(colour, text, ring = false) {
  return el("li", {}, [el("span", {}, [el("span", { class: `swatch${ring ? " ring" : ""}`, style: `background:${colour};color:${colour}` }), text])]);
}

function lineRow(job, right, title, kind = "") {
  return el("li", { title }, [el("button", { text: job, onclick: () => handlers.openAt?.(job, wanted.channel, null) }),
    el("span", { class: kind || "muted", text: right })]);
}

function renderPanelStatus() {
  const status = document.getElementById("sceneStatus");
  if (!status) return;
  const done = curtains.filter((c) => c.state === "done").length;
  const failed = curtains.filter((c) => c.state === "failed").length;
  status.textContent = `Line images: ${done} of ${curtains.length} loaded${failed ? `, ${failed} failed` : ""}` +
    (done + failed < curtains.length ? " (more load as they come into view)" : "") +
    (data && !data.detected_included ? ". Finding the site picker's targets…" : "");
}

function renderPanel(message) {
  const siteSelect = el("select", { "aria-label": "Site", onchange: (e) => update({ site: e.target.value }) },
    sites.map((s) => el("option", { value: s.site, text: `${s.site} · ${s.lines} lines`, selected: s.site === wanted?.site })));
  const channelSelect = el("select", { "aria-label": "Channel", onchange: (e) => update({ channel: e.target.value }) },
    CHANNELS.map((c) => el("option", { value: c, text: c, selected: c === wanted?.channel })));
  const head = [el("div", { class: "row" }, [siteSelect, channelSelect])];
  if (message || !data) return replace(dom.panel, ...head, el("p", { class: "note", text: message ?? "" }));

  const exaggeration = el("output", { text: `×${options.exaggeration}` });
  const sources = new Set(data.lines.map((l) => `${l.velocity.source}`));
  const lines = data.lines.map((l) => lineRow(l.job, `${SOURCE_TEXT[l.source]} · ${VELOCITY_TEXT[l.velocity.source] ?? "no velocity"}`,
    [`v ${l.velocity.value ?? "—"} m/ns: ${l.velocity.basis ?? "unavailable"}`, `position error ±${l.position_error_m} m`, ...l.warnings].join("\n")));
  replace(dom.panel, ...head,
    el("div", { class: "row" }, [el("span", { text: "Depth ×" }),
      el("input", { type: "range", min: 1, max: 10, step: 0.5, value: options.exaggeration, "aria-label": "Depth exaggeration",
        oninput: (e) => { options.exaggeration = Number(e.target.value); exaggeration.textContent = `×${options.exaggeration}`;
          world.scale.y = options.exaggeration; buildRuns(); requestFrame(); } }), exaggeration]),
    el("div", { class: "row" }, [
      el("button", { class: "btn ghost", text: "Reset view", title: "Back to the whole site", onclick: () => { fitCamera(); requestFrame(); } }),
      el("span", { class: "muted", text: "Depth pole: metres on the ruler's zero" })]),
    check("Radar lines", "lines"), check("Targets and claims", "targets"),
    el("label", { class: "check" }, [el("input", { type: "checkbox", checked: options.runs,
      onchange: (e) => { options.runs = e.target.checked; applyVisibility(); requestFrame(); } }), el("span", { text: "Utility runs" })]),
    el("label", { class: "check off", title: "No surface model has been imported for this site" }, [
      el("input", { type: "checkbox", disabled: true }), el("span", { text: "Topography (none for this site)" })]),
    el("p", { class: "note", id: "sceneStatus" }),
    data.lines.length
      ? el("p", { class: "note", text: `Depth uses each line's velocity: ${[...sources].map((s) => VELOCITY_TEXT[s] ?? s).join(", ")}. ` +
        "Hover a line below for its basis. Drag to orbit, right-drag to pan, scroll to zoom." })
      : el("p", { class: "note warn", text: `No line on ${data.site} has a usable position yet, so there is nothing to draw. ` +
        "Add surveyed reference points in the Map tab (see Needs positions below)." }),
    el("h4", { text: "Legend" }),
    el("ul", {}, [
      ...Object.entries(CANDIDATE_COLOURS).map(([cls, colour]) => legendRow(colour, cls.replaceAll("_", " "))),
      legendRow(CANDIDATE_UNDIAGNOSED, "no class suggested / picker target"),
      legendRow(PICK_COLOUR, "interpreter pick"),
      ...Object.entries(REVIEW_STYLES).map(([status, s]) => legendRow(s.colour, `claim ${status}`, true)),
      legendRow("#3b82f6", "line top · GPS track"), legendRow("#22c55e", "line top · surveyed"),
      legendRow("#f59e0b", "location only (GPS froze)", true),
      legendRow(RUN_STYLE.proposed.colour, "utility run · proposed (translucent)"), legendRow(RUN_STYLE.confirmed.colour, "utility run · confirmed")]),
    el("h4", { text: `Utility runs (${runs?.site === data.site ? runs.runs.length : "…"})` }),
    runs?.site === data.site ? el("p", { class: `note${runs.chance?.beats_chance ? "" : " warn"}`,
      text: runs.error ? `Could not link runs: ${runs.error}` : `${runs.runs.filter((r) => r.status === "proposed").length} waiting for review, ` +
        `${runs.runs.filter((r) => r.status === "confirmed").length} confirmed, ${runs.runs.filter((r) => r.status === "rejected").length} rejected. ` +
        `${runs.chance.verdict} (p ${runs.chance.p_chance}). Location-only lines are not linked.` }) : el("p", { class: "note", text: "Linking targets…" }),
    runs?.site === data.site && runs.runs.length ? el("ul", {}, runs.runs.map((r) => el("li", {}, [
      el("button", { text: `${r.lines} lines · ${r.depth_m} m`, title: "Show it close up; click the tube for its Review card",
        onclick: () => focusRun(r) }),
      el("span", { class: r.status === "proposed" ? "warn" : "muted", text: r.status })]))) : null,
    ...groundPanel(),
    el("h4", { text: `Lines (${data.lines.length})` }), el("ul", {}, lines),
    data.located.length ? el("h4", { class: "warn", text: `⚠ Location only, GPS froze (${data.located.length})` }) : null,
    data.located.length ? el("p", { class: "note", text: "Drawn as the circle the survey happened in. Their direction is unknown, " +
      "so no curtain or targets are drawn, and they are left out of run linking." }) : null,
    data.located.length ? el("ul", {}, data.located.map((s) => lineRow(s.job, `±${s.radius_m.toFixed(1)} m`, s.reason, "warn"))) : null,
    data.unplaced.length ? el("h4", { text: `Needs positions (${data.unplaced.length})` }) : null,
    data.unplaced.length ? el("p", { class: "note", text: "Not shown in 3D. Add surveyed reference points in the Map tab to place them." }) : null,
    data.unplaced.length ? el("ul", {}, data.unplaced.map((u) => lineRow(u.job, "not placed", u.reason))) : null,
  );
  renderPanelStatus();
}
