/* The Supervisor review panel — every claim a reasoning layer made, waiting for a person.
 *
 * A claim is circled on the radargram (amber while proposed). A named reviewer confirms or
 * rejects it; the decision, the name and the time go into annotations/<job>/reviews.json and
 * into every export, so a deliverable can always tell a confirmed identification from a guess.
 */

import * as actions from "./assistant_ui.js";
import * as api from "./api.js";
import { el, replace } from "./dom.js";
import { getState, setState } from "./state.js";

const container = document.getElementById("reviewsPanel");

function reviewItem(review, state) {
  const note = el("input", { type: "text", class: "review-note", placeholder: "note (optional)" });
  const canDecide = Boolean(state.reviewer.trim());
  const decided = review.status !== "proposed";
  return el("li", {
    class: `review ${review.status}${state.highlightReviewId === review.id ? " highlight" : ""}`,
    onclick: (event) => { if (event.target.tagName !== "BUTTON" && event.target.tagName !== "INPUT") actions.focusReview(review); },
  }, [
    el("div", { class: "review-head" }, [
      el("span", { class: `status-pill ${review.status}`, text: review.status }),
      el("span", { class: "review-identity", text: review.identity }),
      el("span", { class: "mono review-meta", text: `${review.channel} · ${review.material} · ${review.confidence}` }),
    ]),
    el("p", { class: "review-why", text: review.why }),
    el("p", { class: "panel-note", text: `said by ${review.layer} (${review.model})` }),
    decided
      ? el("p", { class: "panel-note", text: `${review.status} by ${review.reviewer} · ${review.decided_at}${review.note ? ` · ${review.note}` : ""}` })
      : el("div", { class: "review-actions" }, [
        note,
        el("button", { class: "btn good", disabled: !canDecide, title: canDecide ? "" : "Enter your name above first", text: "Confirm", onclick: () => actions.decide(review, "confirmed", note.value) }),
        el("button", { class: "btn bad", disabled: !canDecide, title: canDecide ? "" : "Enter your name above first", text: "Reject", onclick: () => actions.decide(review, "rejected", note.value) }),
      ]),
  ]);
}

/* ---------- utility runs: targets linked across lines (studio/runs.py), one claim per run ---------- */

const siteOf = (job) => (job?.includes("_") ? job.split("_")[0] : null);
let runsFor = null; // the site whose runs are loaded or loading

/** Fetch the open line's site's runs; `force` after a decision. Also used by the 3D view's clicks. */
export async function loadRuns(force = false) {
  const site = siteOf(getState().job);
  if (!site || (!force && runsFor === site)) return;
  runsFor = site;
  try {
    const found = await api.siteRuns(site);
    if (runsFor === site) setState({ siteRuns: found });
  } catch (error) {
    if (runsFor !== site) return;
    runsFor = null; // ask again on the next render
    setState({ siteRuns: { site, runs: [], error: error.message } });
  }
}

async function decideRun(run, status, note) {
  try {
    await api.decideRun(getState().siteRuns.site, run.id, { status, note }); // the server records who is signed in
    await loadRuns(true);
    window.dispatchEvent(new Event("gpr-runs-changed")); // the 3D view redraws its tubes
  } catch (error) {
    setState({ status: `Could not record the decision: ${error.message}` });
  }
}

function runItem(run, state) {
  const note = el("input", { type: "text", class: "review-note", placeholder: "note (optional)" });
  const first = run.targets[0];
  const linkText = run.links.map((l) => `${l.from.replace(/^[^_]*_/, "")} → ${l.to.replace(/^[^_]*_/, "")}: ` +
    `${l.gap_m} m apart, depth differs ${l.depth_delta_m} m (allowed ${l.tolerance_m} m)`);
  return el("li", {
    class: `review ${run.status}${state.highlightRunId === run.id ? " highlight" : ""}`, id: `run-${run.id}`,
    onclick: (event) => { if (event.target.tagName !== "BUTTON" && event.target.tagName !== "INPUT") setState({ highlightRunId: run.id }); },
  }, [
    el("div", { class: "review-head" }, [
      el("span", { class: `status-pill ${run.status}`, text: run.status }),
      el("span", { class: "review-identity", text: `Utility run · ${run.lines} lines` }),
      el("span", { class: "mono review-meta", text: `confidence ${run.confidence}` }),
    ]),
    el("p", { class: "review-why", text: `Depth ${run.depth_m} m below ground (${run.depth_range_m[0]}–${run.depth_range_m[1]} m), ` +
      "from each line's measured time zero. " +
      `Straight in plan to ${run.fit.rms_m} m RMS, bearing ${run.fit.bearing_deg}°, crossing its lines at ${run.fit.crossing_deg}°. ` +
      `Contrast: ${run.contrast}. Positions: ${run.position_sources.join(", ")}; velocity: ${run.velocity_sources.join(", ")}.` }),
    el("ul", { class: "panel-note run-links" }, linkText.map((t) => el("li", { text: t }))),
    run.dropped_by_fit.length ? el("p", { class: "panel-note", text: `Left out by the straight-line fit: ${run.dropped_by_fit.join(", ")}` }) : null,
    el("p", { class: `panel-note${run.chance.beats_chance ? "" : " warn"}`, text: `Site check: ${run.chance.verdict} ` +
      `(${run.chance.runs} found, ${run.chance.chance_mean} on average at random positions, p ${run.chance.p_chance}).` }),
    run.found_now ? null : el("p", { class: "panel-note warn", text: "No longer found by the current data; kept for the record." }),
    el("p", { class: "panel-note", text: `Starts on ${first.job} at ${first.chainage_m} m. Proposed ${run.proposed_at} by the run linker (no model).` }),
    run.status !== "proposed"
      ? el("p", { class: "panel-note", text: `${run.status} by ${run.reviewer} · ${run.decided_at}${run.note ? ` · ${run.note}` : ""}` })
      : el("div", { class: "review-actions" }, [
        note,
        el("button", { class: "btn good", text: "Confirm", onclick: () => decideRun(run, "confirmed", note.value) }),
        el("button", { class: "btn bad", text: "Reject", onclick: () => decideRun(run, "rejected", note.value) }),
      ]),
  ]);
}

function runsSection(state) {
  const site = siteOf(state.job);
  if (!site) return null;
  const found = state.siteRuns?.site === site ? state.siteRuns : null;
  if (!found) return el("p", { class: "panel-note", text: `Linking targets across ${site}'s lines…` });
  const waiting = found.runs.filter((r) => r.status === "proposed").length;
  return el("div", { class: "runs-section" }, [
    el("div", { class: "interp-label", text: `Utility runs on ${site} · ${found.runs.length} (${waiting} waiting)` }),
    found.error ? el("p", { class: "panel-note warn", text: `Could not link runs: ${found.error}` }) : null,
    found.excluded_lines?.length ? el("p", { class: "panel-note", text: `Not linked (no usable position): ${found.excluded_lines.map((l) => l.job).join(", ")}.` }) : null,
    found.runs.length
      ? el("ul", { class: "review-list" }, [...found.runs]
        .sort((a, b) => (a.status === "proposed" ? 0 : 1) - (b.status === "proposed" ? 0 : 1) || b.confidence - a.confidence)
        .map((r) => runItem(r, state)))
      : el("p", { class: "empty", text: "No targets line up across three or more neighbouring lines here." }),
  ]);
}

// Rebuilt only when what it shows changes, so a note being typed survives cursor moves.
let lastDeps = [];

export function render() {
  const state = getState();
  const deps = [state.job, state.channel, state.reviews, state.reviewer, state.reviewScope, state.highlightReviewId,
    state.siteRuns, state.highlightRunId];
  if (deps.every((dep, i) => dep === lastDeps[i])) return;
  lastDeps = deps;
  if (!state.job) {
    replace(container, el("p", { class: "empty", text: "Open a line to review what the assistant has claimed." }));
    return;
  }
  loadRuns(); // a no-op once this site's runs are loaded
  const scope = state.reviewScope;
  const shown = state.reviews
    .filter((r) => scope === "all" || r.channel === state.channel)
    .sort((a, b) => (a.status === "proposed" ? 0 : 1) - (b.status === "proposed" ? 0 : 1));
  const counts = { proposed: 0, confirmed: 0, rejected: 0 };
  for (const r of state.reviews) counts[r.status] += 1;

  replace(container,
    el("p", { class: "panel-note", text: state.reviewer ? `Deciding as ${state.reviewer} (signed in)` : "Signing in…" }),
    el("p", { class: "panel-note", text: `${counts.proposed} waiting · ${counts.confirmed} confirmed · ${counts.rejected} rejected` }),
    el("div", { class: "btn-row" }, [
      el("button", { class: `btn ghost${scope === "channel" ? " active" : ""}`, text: "This channel", onclick: () => setState({ reviewScope: "channel" }) }),
      el("button", { class: `btn ghost${scope === "all" ? " active" : ""}`, text: "All channels", onclick: () => setState({ reviewScope: "all" }) }),
    ]),
    shown.length
      ? el("ul", { class: "review-list" }, shown.map((r) => reviewItem(r, state)))
      : el("p", { class: "empty", text: "No claims yet — ask the Assistant about this line." }),
    el("div", { class: "btn-row" }, [
      el("a", { class: "btn ghost", href: api.reviewsCsvUrl(state.job), text: "Export CSV" }),
      state.channel ? el("a", { class: "btn ghost", href: api.reviewReportUrl(state.job, state.channel), target: "_blank", rel: "noopener", text: "Review report (PDF)" }) : null,
    ]),
    runsSection(state),
  );
  document.getElementById(`run-${state.highlightRunId}`)?.scrollIntoView({ block: "nearest" });
}
