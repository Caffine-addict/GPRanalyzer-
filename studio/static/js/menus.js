/* The menu bar: File / Process / View / Tools as dropdowns.
 *
 * Deliberately owns no behaviour of its own. Every item calls an action app.js
 * already exposes through the toolbar or panels, so a menu can never do
 * something subtly different from the button beside it.
 */

import { el, replace } from "./dom.js";

let openMenu = null;
const dropdown = el("div", { class: "menu-dropdown", hidden: true, role: "menu" });

function close() {
  dropdown.hidden = true;
  openMenu?.classList.remove("open");
  openMenu = null;
}

/** `definitions` maps a menu name to a function returning its items, read fresh on each open
 * so enabled/disabled state always reflects the current line. An item is
 * {label, action, disabled?, hint?} or the string "-" for a separator. */
export function install(definitions) {
  document.body.append(dropdown);

  for (const button of document.querySelectorAll(".menus .menu")) {
    button.setAttribute("aria-haspopup", "true");
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      if (openMenu === button) return close();
      close();
      const items = definitions[button.dataset.menu]?.() ?? [];
      replace(dropdown, ...items.map((item) =>
        item === "-"
          ? el("div", { class: "menu-sep" })
          : el("button", {
              class: "menu-item",
              role: "menuitem",
              disabled: Boolean(item.disabled),
              title: item.hint ?? "",
              onclick: () => { close(); item.action(); },
            }, [el("span", { text: item.label }), item.shortcut ? el("kbd", { text: item.shortcut }) : null]),
      ));
      const box = button.getBoundingClientRect();
      dropdown.style.left = `${box.left}px`;
      dropdown.style.top = `${box.bottom + 2}px`;
      dropdown.hidden = false;
      button.classList.add("open");
      openMenu = button;
    });
    // Moving across the bar with a menu already open switches menus, like a desktop app.
    button.addEventListener("mouseenter", () => {
      if (openMenu && openMenu !== button) button.click();
    });
  }
  document.addEventListener("click", close);
  window.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); });
  window.addEventListener("resize", close);
}
