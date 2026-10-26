/* A CAD utility drawing in 3D: each utility at the depth its own label states.
 *
 * Depths come only from the drawing (studio/site_drawings.depth_lines): a label's depth reaches the
 * line its leader arrow points at, and carries along pieces joined to that line end to end. A
 * utility the drawing gives no depth for stays dashed at the surface; none is sunk to a guess.
 *
 * x is the drawing's x, z its y (north away from the default camera), y is up; depth goes down,
 * times the exaggeration, since a 177 m site with 1 m depths is otherwise flat.
 */

import * as THREE from "../vendor/three/three.module.min.js";
import { OrbitControls } from "../vendor/three/OrbitControls.js";
import { el } from "./dom.js";
import { token } from "./theme.js";

const dom = {
  root: document.getElementById("drawing3d"),
  canvas: document.getElementById("drawing3dCanvas"),
  labels: document.getElementById("drawing3dLabels"),
};

export const options = { exaggeration: 8, background: true, unknown: true, tags: true };
let gl = null;
let world = null;
let data = null;
let colourOf = (layer) => layer.colour;
let tags = [];
let centre = [0, 0];
let frameQueued = false;

export function show(on, drawing, layerColour) {
  dom.root.hidden = !on;
  if (!on) return;
  ensureGl();
  colourOf = layerColour;
  if (drawing !== data) {
    data = drawing;
    build();
    fit();
  }
  resize();
}

export function set(key, value) {
  options[key] = value;
  if (key === "exaggeration") build(); else applyVisibility();
  requestFrame();
}

export function fit() {
  if (!world || !data?.bounds) return;
  const [[x0, y0], [x1, y1]] = data.bounds;
  const span = Math.max(x1 - x0, y1 - y0);
  gl.controls.target.set(0, -1, 0);
  gl.camera.position.set(span * 0.35, span * 0.45, span * 0.75);
  gl.camera.near = span / 2000;
  gl.camera.far = span * 20;
  gl.camera.updateProjectionMatrix();
  gl.controls.update();
  requestFrame();
}

function ensureGl() {
  if (gl) return;
  const renderer = new THREE.WebGLRenderer({ canvas: dom.canvas, antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 5000);
  const controls = new OrbitControls(camera, dom.canvas);
  controls.screenSpacePanning = true;
  controls.addEventListener("change", requestFrame);
  gl = { renderer, camera, controls, scene: new THREE.Scene() };
  new ResizeObserver(resize).observe(dom.root);
  window.addEventListener("gpr-theme", () => { if (data) { build(); requestFrame(); } });
}

function resize() {
  const { clientWidth: w, clientHeight: h } = dom.root;
  if (!gl || !w || !h) return;
  gl.renderer.setSize(w, h, false);
  gl.camera.aspect = w / h;
  gl.camera.updateProjectionMatrix();
  requestFrame();
}

function requestFrame() {
  if (frameQueued || !gl) return;
  frameQueued = true;
  requestAnimationFrame(() => {
    frameQueued = false;
    gl.renderer.render(gl.scene, gl.camera);
    placeTags();
  });
}

const at = (x, y, depth = 0) => new THREE.Vector3(x - centre[0], -depth * options.exaggeration, -(y - centre[1]));

function dispose(object) {
  object.traverse((o) => { o.geometry?.dispose(); for (const m of [].concat(o.material ?? [])) m.dispose(); });
}

/** Many polylines of one colour as one LineSegments: thousands of strokes, one draw call. */
function segments(polylines, toPoint, material) {
  const positions = [];
  for (const line of polylines) {
    for (let i = 1; i < line.length; i += 1) positions.push(...toPoint(line[i - 1]).toArray(), ...toPoint(line[i]).toArray());
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  const object = new THREE.LineSegments(geometry, material);
  if (material.isLineDashedMaterial) object.computeLineDistances();
  return object;
}

function build() {
  if (world) { gl.scene.remove(world); dispose(world); }
  for (const tag of tags) tag.node.remove();
  tags = [];
  gl.renderer.setClearColor(token("--bg-sink"));
  world = new THREE.Group();
  if (!data?.bounds) { gl.scene.add(world); return; }
  const [[x0, y0], [x1, y1]] = data.bounds;
  centre = [(x0 + x1) / 2, (y0 + y1) / 2];
  const span = Math.max(x1 - x0, y1 - y0);
  const layerOf = new Map(data.layers.map((l) => [l.name, l]));

  // The ground: a faint plane, and the drawing's site plan lying on it.
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0, y1 - y0),
    new THREE.MeshBasicMaterial({ color: token("--text-faint"), transparent: true, opacity: 0.06, side: THREE.DoubleSide, depthWrite: false }));
  ground.rotation.x = -Math.PI / 2;
  world.add(ground);
  const plan = new THREE.Group();
  plan.name = "background";
  for (const layer of data.layers) {
    if (layer.utility || !data.lines[layer.name]?.length) continue;
    plan.add(segments(data.lines[layer.name], ([x, y]) => at(x, y),
      new THREE.LineBasicMaterial({ color: colourOf(layer), transparent: true, opacity: 0.35 })));
  }
  world.add(plan);

  // Utilities the drawing gives no depth for: dashed, at the surface.
  const unknown = new THREE.Group();
  unknown.name = "unknown";
  const byLayer = Map.groupBy ? Map.groupBy(data.depths.unplaced, (u) => u.layer)
    : data.depths.unplaced.reduce((m, u) => m.set(u.layer, [...(m.get(u.layer) ?? []), u]), new Map());
  for (const [name, items] of byLayer) {
    unknown.add(segments(items.map((u) => u.points), ([x, y]) => at(x, y),
      new THREE.LineDashedMaterial({ color: colourOf(layerOf.get(name)), dashSize: span / 300, gapSize: span / 300, transparent: true, opacity: 0.8 })));
  }
  world.add(unknown);

  // Utilities at their stated depth: tubes, with a drop line and tag at each labelled point.
  const radius = Math.max(0.04, span / 900);
  for (const line of data.depths.placed) {
    const colour = colourOf(layerOf.get(line.layer));
    const points = line.points.map(([x, y, d]) => at(x, y, d));
    if (points.length >= 2) {
      const path = new THREE.CurvePath();
      for (let i = 1; i < points.length; i += 1) path.add(new THREE.LineCurve3(points[i - 1], points[i]));
      world.add(new THREE.Mesh(new THREE.TubeGeometry(path, Math.max(4, points.length * 4), radius, 6, false),
        new THREE.MeshBasicMaterial({ color: colour, transparent: line.carried, opacity: line.carried ? 0.75 : 1 })));
    }
    for (const label of line.labels) {
      const top = at(label.x, label.y);
      const bottom = at(label.x, label.y, label.depth_m);
      const drop = new THREE.Line(new THREE.BufferGeometry().setFromPoints([top, bottom]),
        new THREE.LineBasicMaterial({ color: colour }));
      drop.name = "tags";
      world.add(drop);
      const dot = new THREE.Mesh(new THREE.SphereGeometry(radius * 2.2, 10, 8), new THREE.MeshBasicMaterial({ color: colour }));
      dot.position.copy(bottom);
      world.add(dot);
      const node = el("div", { class: `d3-tag${label.quality_level === "QL-A" ? " verified" : ""}`,
        text: `${label.depth_m} m · ${label.quality_level}`,
        title: `${label.kind}${label.diameter_mm ? `, Ø ~${label.diameter_mm} mm` : ""}: ${label.depth_m} m to its top, ` +
          `${label.quality_level === "QL-A" ? "exposed and verified" : "detected"} (as labelled on the drawing)` });
      node.style.borderColor = colour;
      dom.labels.append(node);
      tags.push({ node, point: bottom });
    }
  }
  addDepthPole(x1, y0, span);
  gl.scene.add(world);
  applyVisibility();
}

function addDepthPole(x, y, span) {
  const deepest = Math.max(1, ...data.depths.placed.flatMap((l) => l.points.map((p) => p[2])));
  const step = deepest > 2 ? 1 : 0.5;
  const points = [at(x + span * 0.02, y), at(x + span * 0.02, y, Math.ceil(deepest / step) * step)];
  for (let d = step; d <= deepest + 1e-6; d += step) points.push(at(x + span * 0.02, y, d), at(x + span * 0.035, y, d));
  world.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(points),
    new THREE.LineBasicMaterial({ color: token("--text-dim") })));
  for (let d = 0; d <= deepest + 1e-6; d += step) {
    const node = el("div", { class: "d3-tick", text: `${d} m` });
    dom.labels.append(node);
    tags.push({ node, point: at(x + span * 0.04, y, d), tick: true });
  }
}

function applyVisibility() {
  if (!world) return;
  world.traverse((o) => {
    if (o.name === "background") o.visible = options.background;
    if (o.name === "unknown") o.visible = options.unknown;
    if (o.name === "tags") o.visible = options.tags;
  });
  requestFrame();
}

function placeTags() {
  const w = dom.root.clientWidth;
  const h = dom.root.clientHeight;
  const placed = [];
  for (const tag of tags) {
    const p = tag.point.clone().project(gl.camera);
    const x = ((p.x + 1) / 2) * w;
    const y = ((1 - p.y) / 2) * h;
    tag.w ||= tag.node.offsetWidth; // measured while shown: a hidden tag reports 0
    const box = { x: x + 4, y: y - 8, w: tag.w || 90, h: 16 };
    const clear = tag.tick || placed.every((o) => box.x + box.w <= o.x || o.x + o.w <= box.x || box.y + box.h <= o.y || o.y + o.h <= box.y);
    const shown = p.z < 1 && x > 0 && x < w && y > 0 && y < h && (tag.tick || (options.tags && clear));
    tag.node.style.display = shown ? "" : "none";
    tag.node.style.left = `${x + 4}px`;
    tag.node.style.top = `${y - 8}px`;
    if (shown && !tag.tick) placed.push(box);
  }
}
