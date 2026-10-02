/* Actions for the reasoning layers and the supervisor review — the panels only render.
 *
 * Every claim a layer makes about a target comes back as a `proposed` review tied to that
 * target's measured box (studio/reviews.py). They are merged into state.reviews so the canvas
 * circles them at once and the Review panel lists them for a supervisor to decide.
 */

import * as api from "./api.js";
import * as view from "./view.js";
import { getState, setState } from "./state.js";

const REVIEWER_KEY = "gprStudioReviewer";

export function reviewerName() {
  try {
    return window.localStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setReviewerName(name) {
  try {
    window.localStorage.setItem(REVIEWER_KEY, name);
  } catch {
    /* storage blocked: the name just is not remembered */
  }
  setState({ reviewer: name });
}

function patchAssistant(patch) {
  setState({ assistant: { ...getState().assistant, ...patch } });
}

function mergeReviews(incoming) {
  if (!incoming?.length) return;
  const byId = new Map(getState().reviews.map((r) => [r.id, r]));
  for (const review of incoming) byId.set(review.id, { ...byId.get(review.id), ...review });
  setState({ reviews: [...byId.values()] });
}

export async function loadReviews(job) {
  try {
    setState({ reviews: await api.listReviews(job) });
  } catch (error) {
    setState({ reviews: [], status: `Could not load reviews: ${error.message}` });
  }
}

/** A new line or channel: the conversation was about the old one, so it starts afresh. */
export function onLineChanged() {
  patchAssistant({ chat: [], replies: {}, error: null });
  if (getState().assistant.live) ask("live");
}

export function toggleLive() {
  const live = !getState().assistant.live;
  patchAssistant({ live });
  if (live) ask("live");
}

export async function ask(layer, question = "") {
  const { job, channel, assistant } = getState();
  if (!job || !channel || assistant.busy) return;
  const history = assistant.chat.map((turn) => ({ role: turn.role, text: turn.text }));
  const chat = layer === "chat" ? [...assistant.chat, { role: "operator", text: question }] : assistant.chat;
  patchAssistant({ busy: layer, error: null, chat });
  try {
    const reply = await api.askAssistant(job, channel, { layer, question, history });
    // The operator may have opened another line while the model was thinking.
    if (getState().job !== job || getState().channel !== channel) return patchAssistant({ busy: null });
    mergeReviews(reply.reviews);
    const now = getState().assistant;
    patchAssistant({
      busy: null,
      error: reply.error,
      replies: { ...now.replies, [layer]: reply },
      chat: layer === "chat" ? [...now.chat, { role: "assistant", text: reply.answer || reply.error || "", reply }] : now.chat,
    });
  } catch (error) {
    patchAssistant({ busy: null, error: error.message });
  }
}

export async function briefing() {
  if (getState().assistant.busy) return;
  patchAssistant({ busy: "briefing", error: null });
  try {
    const reply = await api.surveyBriefing();
    patchAssistant({ busy: null, error: reply.error, replies: { ...getState().assistant.replies, briefing: reply } });
  } catch (error) {
    patchAssistant({ busy: null, error: error.message });
  }
}

export async function decide(review, status, note = "") {
  const { job, reviewer } = getState();
  if (!job) return;
  try {
    mergeReviews([await api.decideReview(job, review.id, { status, reviewer, note })]);
  } catch (error) {
    setState({ status: `Could not record the decision: ${error.message}` });
  }
}

/** Centre the radargram on a reviewed target and make its circle stand out. */
export function focusReview(review) {
  const state = getState();
  if (review.channel !== state.channel) return setState({ highlightReviewId: review.id });
  const plot = view.plotRect();
  setState({
    highlightReviewId: review.id,
    view: {
      ...state.view,
      originTrace: review.x + review.w / 2 - plot.w / (2 * state.view.scaleX),
      originSample: review.y + review.h / 2 - plot.h / (2 * state.view.scaleY),
    },
  });
}

/** A mention in a reply points at a review by target; find the review standing for it. */
export function reviewForMention(mention) {
  return getState().reviews.find((r) => r.target_id === mention.target_ref && r.status === "proposed"
    && r.identity.toLowerCase() === mention.identity.toLowerCase())
    ?? getState().reviews.find((r) => r.target_id === mention.target_ref);
}
