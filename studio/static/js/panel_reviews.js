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

// Rebuilt only when what it shows changes, so a note being typed survives cursor moves.
let lastDeps = [];

export function render() {
  const state = getState();
  const deps = [state.job, state.channel, state.reviews, state.reviewer, state.reviewScope, state.highlightReviewId];
  if (deps.every((dep, i) => dep === lastDeps[i])) return;
  lastDeps = deps;
  if (!state.job) {
    replace(container, el("p", { class: "empty", text: "Open a line to review what the assistant has claimed." }));
    return;
  }
  const scope = state.reviewScope;
  const shown = state.reviews
    .filter((r) => scope === "all" || r.channel === state.channel)
    .sort((a, b) => (a.status === "proposed" ? 0 : 1) - (b.status === "proposed" ? 0 : 1));
  const counts = { proposed: 0, confirmed: 0, rejected: 0 };
  for (const r of state.reviews) counts[r.status] += 1;

  const name = el("input", { type: "text", value: state.reviewer, placeholder: "Your name", title: "Required to confirm or reject a claim" });
  name.addEventListener("change", () => actions.setReviewerName(name.value.trim()));

  replace(container,
    el("div", { class: "field" }, [el("label", { text: "Reviewer" }), name]),
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
  );
}
