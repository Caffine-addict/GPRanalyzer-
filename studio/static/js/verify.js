/* Verify the detector's targets one at a time (studio/verify.py). Keyboard first: Y / N / U, arrows. */

import * as api from "./api.js";
import { el, replace } from "./dom.js";

const $ = (id) => document.getElementById(id);
const state = { items: [], index: 0, busy: false, progress: null, request: 0 };

const label = (item) => `${item.job} · ${item.chainage_m.toFixed(2)} m along · ${item.time_ns.toFixed(1)} ns`;

function paintProgress() {
  const p = state.progress;
  if (!p) return;
  const done = p.yes + p.no + p.unsure;
  $("counts").textContent = `${done} verified · ${p.yes} yes · ${p.no} no · ${p.unsure} unsure · goal ${p.goal} yes`;
  $("bar").style.width = `${Math.min(100, (100 * p.yes) / p.goal)}%`;
}

function paint() {
  const item = state.items[state.index];
  for (const id of ["yes", "no", "unsure", "back", "skip"]) $(id).disabled = state.busy || !item;
  $("site").disabled = state.busy;
  if (!item) {
    replace($("meta"));
    replace($("channels"), el("p", { class: "done", text: state.items.length ? "Every target on this site has a decision. Pick another site." : "No targets on this site." }));
    return;
  }
  const position = state.items.filter((i) => i.job === item.job);
  replace($("meta"),
    el("b", { text: label(item) }),
    el("span", { text: `target ${position.indexOf(item) + 1} of ${position.length} on this line · ${state.index + 1} of ${state.items.length} on the site` }),
    item.decision ? el("span", { class: `pill ${item.decision}`, text: `${item.decision}${item.decided_by ? ` · ${item.decided_by}` : ""}` }) : null);
  replace($("channels"), ...item.channels.map((c) => el("figure", {}, [
    el("figcaption", {}, [el("span", { text: c.extension }),
      el("span", { class: "seen", text: item.seen_on.includes(c.extension) ? "detector saw it here" : "" })]),
    el("img", { src: api.verifyImageUrl(item.job, c.extension, item.chainage_m, c.mark_ns, item.line_length_m),
      alt: `${c.extension} radargram, 1.5 m either side of the target, ticks at its position and time` }),
  ])));
  preload(state.items[state.index + 1]);
}

function preload(item) {
  for (const c of item?.channels ?? []) new Image().src = api.verifyImageUrl(item.job, c.extension, item.chainage_m, c.mark_ns, item.line_length_m);
}

function say(text) { $("message").textContent = text; }

async function decide(decision) {
  const item = state.items[state.index];
  if (!item || state.busy) return;
  const request = state.request;
  state.busy = true;
  paint();
  try {
    await api.verifyLabel({ job: item.job, trace: item.trace, time_ns: item.time_ns, decision });
    if (request === state.request) state.items[state.index] = { ...item, decision, decided_by: "you" };
    state.progress = await api.verifyProgress();
    paintProgress();
    say("");
    move(1);
  } catch (error) {
    say(`Not saved: ${error.message}`);
  }
  state.busy = false;
  paint();
}

function move(step) {
  state.index = Math.max(0, Math.min(state.items.length, state.index + step));
  paint();
}

async function openSite(site) {
  const request = ++state.request; // a slower, older answer must not replace a newer site's list
  say("Loading targets… the first time on a site takes a few seconds per line.");
  replace($("channels"));
  try {
    const items = (await api.verifyQueue(site)).items;
    if (request !== state.request) return;
    state.items = items;
    const next = state.items.findIndex((i) => !i.decision);
    state.index = next === -1 ? state.items.length : next;
    say("");
  } catch (error) {
    if (request !== state.request) return;
    state.items = [];
    say(`Could not load ${site}: ${error.message}`);
  }
  paint();
}

async function start() {
  for (const [id, decision] of [["yes", "yes"], ["no", "no"], ["unsure", "unsure"]]) $(id).addEventListener("click", () => decide(decision));
  $("back").addEventListener("click", () => move(-1));
  $("skip").addEventListener("click", () => move(1));
  document.addEventListener("keydown", (event) => {
    if (event.target instanceof HTMLSelectElement || event.metaKey || event.ctrlKey) return;
    const key = event.key.toLowerCase();
    if (key === "y") decide("yes");
    else if (key === "n") decide("no");
    else if (key === "u") decide("unsure");
    else if (key === "arrowleft") move(-1);
    else if (key === "arrowright") move(1);
  });
  const { sites } = await api.verifySites();
  replace($("site"), ...sites.map((s) => el("option", { value: s.site, text: `${s.site} — ${s.lines} lines` })));
  $("site").addEventListener("change", () => { $("site").blur(); openSite($("site").value); }); // keys go back to Y/N/U
  state.progress = await api.verifyProgress();
  paintProgress();
  if (sites.length) await openSite(sites[0].site);
}

start().catch((error) => say(`Could not start: ${error.message}`));
