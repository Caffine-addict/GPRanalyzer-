/* Light or dark. The page's <head> applies the saved choice (or the device's) before first paint;
 * this switches it, remembers it in this browser, and tells canvases to repaint. */

const KEY = "gpr-theme";

export const current = () => (document.documentElement.dataset.theme === "light" ? "light" : "dark");

function apply(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem(KEY, theme); } catch { /* private window: this page only */ }
  window.dispatchEvent(new CustomEvent("gpr-theme", { detail: theme }));
}

/** A button that shows the mode it switches to. */
export function initToggle(button) {
  const paint = () => {
    const next = current() === "light" ? "dark" : "light";
    button.querySelector("use").setAttribute("href", next === "light" ? "#i-sun" : "#i-moon");
    button.title = `Switch to ${next} mode`;
    button.setAttribute("aria-label", button.title);
  };
  button.addEventListener("click", () => { apply(current() === "light" ? "dark" : "light"); paint(); });
  paint();
}

/** A colour token's current value, for drawing on a canvas. */
export const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
