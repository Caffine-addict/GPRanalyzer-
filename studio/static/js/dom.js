/* Minimal DOM helpers.
 *
 * Panels are rebuilt from state rather than surgically patched, so everything
 * on screen is derived from one source. At this size (a few dozen controls)
 * that is both simpler and impossible to desync; `el` exists so rebuilding
 * stays readable without a framework.
 *
 * Text always goes in via textContent, never innerHTML — file headers, notes
 * and labels all originate outside this code.
 */

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") throw new Error("el(): raw HTML is not allowed — use text");
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key in node && key !== "list" && typeof value !== "object") node[key] = value;
    else node.setAttribute(key, String(value));
  }
  for (const child of [].concat(children)) {
    if (child == null || child === false) continue;
    node.append(typeof child === "string" || typeof child === "number" ? String(child) : child);
  }
  return node;
}

export function replace(container, ...children) {
  container.replaceChildren(...children.filter(Boolean));
}

/** A labelled control row. */
export function field(labelText, control, { sub = false, unit = null } = {}) {
  return el("div", { class: `field${sub ? " sub" : ""}` }, [
    el("label", { text: labelText }),
    control,
    unit ? el("span", { class: "unit", text: unit }) : null,
  ]);
}

export function checkboxRow(labelText, checked, onChange) {
  return el("div", { class: "field" }, [
    el("label", { class: "switch" }, [
      el("input", { type: "checkbox", checked, onchange: (e) => onChange(e.target.checked) }),
      labelText,
    ]),
  ]);
}

export function numberInput(value, onCommit, { min, max, step = 1 } = {}) {
  return el("input", {
    type: "number",
    value: String(value),
    min, max, step,
    onchange: (e) => {
      const parsed = Number(e.target.value);
      if (Number.isFinite(parsed)) onCommit(parsed);
      else e.target.value = String(value); // reject junk rather than sending NaN to the server
    },
  });
}

export function selectInput(value, options, onChange) {
  return el(
    "select",
    { onchange: (e) => onChange(e.target.value) },
    options.map(([optionValue, label]) =>
      el("option", { value: optionValue, text: label, selected: optionValue === value }),
    ),
  );
}

export function rangeInput(value, onInput, { min, max, step }) {
  return el("input", {
    type: "range", min, max, step, value: String(value),
    oninput: (e) => onInput(Number(e.target.value)),
  });
}

export function readoutRows(pairs) {
  return pairs.map(([key, value, className]) =>
    el("div", { class: "row" }, [el("span", { text: key }), el("span", { class: className, text: value })]),
  );
}
