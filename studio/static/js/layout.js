/* Show or hide the workspace's parts, as an editor does: the project tree (left), the A-scan strip
 * (bottom), the inspector (right), or all three at once to give the view the whole window.
 *
 * Each part is hidden by a body attribute the stylesheet reads, so the view in the middle simply
 * gets the room; the radargram, map and 3D views already re-lay themselves out on resize. The
 * choice is this browser's own, remembered between visits.
 */

const PARTS = ["left", "bottom", "right"];
const STORE = "gpr-layout";
let hidden = new Set();
let beforeFocus = null; // what was hidden before "focus" hid everything, to put back

function save() {
  try { localStorage.setItem(STORE, JSON.stringify([...hidden])); } catch { /* not remembered */ }
}

function apply() {
  for (const part of PARTS) document.body.toggleAttribute(`data-hide-${part}`, hidden.has(part));
  for (const button of document.querySelectorAll("[data-layout]")) {
    const part = button.dataset.layout;
    const on = part === "focus" ? PARTS.every((p) => hidden.has(p)) : !hidden.has(part);
    button.classList.toggle("on", on);
    button.setAttribute("aria-pressed", String(on));
  }
  window.dispatchEvent(new Event("resize")); // canvases and maps fit the space they now have
}

export function toggle(part) {
  if (part === "focus") {
    if (PARTS.every((p) => hidden.has(p))) {
      hidden = beforeFocus ?? new Set();
      beforeFocus = null;
    } else {
      beforeFocus = new Set(hidden);
      hidden = new Set(PARTS);
    }
  } else if (hidden.has(part)) hidden.delete(part);
  else hidden.add(part);
  save();
  apply();
}

export function init() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORE) ?? "[]");
    hidden = new Set(stored.filter((p) => PARTS.includes(p)));
  } catch { hidden = new Set(); }
  for (const button of document.querySelectorAll("[data-layout]")) {
    button.addEventListener("click", () => toggle(button.dataset.layout));
  }
  // The editor's own shortcuts: ⌘/Ctrl+B left, ⌘/Ctrl+J bottom, ⌘/Ctrl+Alt+B right. Not while typing.
  window.addEventListener("keydown", (event) => {
    const typing = event.target instanceof HTMLElement && event.target.closest("input, textarea, select, [contenteditable]");
    if (typing || !(event.metaKey || event.ctrlKey) || event.shiftKey) return;
    // The physical key: with ⌥ held a Mac types "∫" for B, so event.key would never match.
    const part = event.code === "KeyB" ? (event.altKey ? "right" : "left") : event.code === "KeyJ" && !event.altKey ? "bottom" : null;
    if (!part) return;
    event.preventDefault();
    toggle(part);
  });
  apply();
}
