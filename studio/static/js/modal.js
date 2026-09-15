/* The single modal surface, shared by the signature library and the save dialog.
 *
 * `promptText` exists to replace window.prompt, which blocks the event loop —
 * on this page that freezes the canvas mid-gesture and stalls the render loop
 * until the operator answers. It also cannot be styled, so it breaks the one
 * thing an instrument UI has to maintain: looking like one instrument.
 */

import { el, replace } from "./dom.js";

const backdrop = document.getElementById("modalBackdrop");
const modal = document.getElementById("modal");

let onDismiss = null;

export function init() {
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) close();
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !backdrop.hidden) close();
  });
}

export function open(...children) {
  replace(modal, ...children.filter(Boolean));
  backdrop.hidden = false;
}

export function close() {
  backdrop.hidden = true;
  replace(modal);
  const dismiss = onDismiss;
  onDismiss = null;
  dismiss?.();
}

/** Ask for one line of text. Resolves with the string, or null if dismissed. */
export function promptText({ title, note, placeholder = "", confirmLabel = "Save" }) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      onDismiss = null;
      close();
      resolve(value);
    };
    // Dismissing by backdrop click or Escape must still settle the promise,
    // or the caller waits forever for an answer that is never coming.
    onDismiss = () => finish(null);

    const input = el("input", {
      type: "text",
      placeholder,
      style: "width:100%",
      onkeydown: (event) => {
        if (event.key === "Enter") finish(input.value);
      },
    });

    open(
      el("h3", { text: title }),
      note ? el("p", { text: note }) : null,
      el("div", { style: "padding:10px 0" }, [input]),
      el("div", { class: "btn-row", style: "padding:0" }, [
        el("button", { class: "btn ghost", text: "Cancel", onclick: () => finish(null) }),
        el("button", { class: "btn primary", text: confirmLabel, onclick: () => finish(input.value) }),
      ]),
    );
    input.focus();
  });
}
