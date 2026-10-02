/* The Assistant panel — five reasoning layers over what the Studio has measured.
 *
 * Chat, Line summary, What is it?, Live and Briefing all show the same three things, kept
 * apart as the Interpretation panel keeps them apart: the model's words, the targets it made
 * claims about (each a chip that jumps to the circle on the radargram), and — folded away —
 * exactly the context it was shown. Every claim waits in the Review panel for a supervisor.
 */

import * as actions from "./assistant_ui.js";
import * as api from "./api.js";
import { el, replace } from "./dom.js";
import { getState, setState } from "./state.js";

const container = document.getElementById("assistantPanel");

const TABS = [
  ["chat", "Chat"],
  ["line_summary", "Line summary"],
  ["what_is_what", "What is it?"],
  ["live", "Live"],
  ["briefing", "Briefing"],
];

const RUN_LABEL = {
  line_summary: "Summarise this line",
  what_is_what: "Identify the targets",
  live: "Explain the detections now",
  briefing: "Brief me on the survey",
};

const INTRO = {
  line_summary: "Reads every target on this channel together: likely utility runs, weak targets, what to check.",
  what_is_what: "Material first (polarity, amplitude), then size, then — only as a low-confidence guess — the service. Service type needs an EM locator or a dig.",
  live: "Explains what the automatic detector is flagging on the open line: real objects, clutter or ringing.",
  briefing: "A written briefing across the whole dataset: what exists, what is verified, what is not, and next steps.",
};

function mentionChip(mention) {
  const review = actions.reviewForMention(mention);
  return el("button", {
    class: `chip conf-${mention.confidence}`,
    title: mention.why,
    onclick: () => review && actions.focusReview(review),
    text: `${mention.target_id} · ${mention.identity} (${mention.confidence})`,
  });
}

function replyBlock(reply) {
  if (!reply) return null;
  const nodes = [];
  if (reply.error) nodes.push(el("p", { class: "caveat", text: reply.error }));
  if (reply.answer) {
    for (const paragraph of reply.answer.split(/\n{2,}/)) nodes.push(el("p", { class: "interp-text", text: paragraph }));
  }
  if (reply.mentions?.length) {
    nodes.push(el("div", { class: "interp-label", text: "Circled on the radargram — waiting for review" }),
      el("div", { class: "chips" }, reply.mentions.map(mentionChip)));
  }
  if (reply.dropped?.length) {
    nodes.push(el("p", { class: "panel-note", text: `Claims not kept — ${reply.dropped.join("; ")}` }));
  }
  if (reply.next_steps?.length) {
    nodes.push(el("div", { class: "interp-label", text: "Next steps" }),
      el("ul", { class: "steps" }, reply.next_steps.map((s) => el("li", { text: s }))));
  }
  if (reply.model) {
    nodes.push(el("p", { class: "panel-note", text: `${reply.model} · ${reply.latency_ms} ms${reply.cached ? " · cached" : ""} · written by a model, not measured` }));
  }
  if (reply.context_text) {
    nodes.push(el("details", { class: "interp-evidence" }, [
      el("summary", { text: "What the model was shown" }),
      el("pre", { class: "mono", text: reply.context_text }),
    ]));
  }
  return el("div", { class: "assistant-reply" }, nodes);
}

function chatTab(state) {
  const input = el("textarea", { class: "chat-input", rows: 2, placeholder: "Ask about this line — e.g. which targets could be one pipe run?" });
  const send = () => {
    const question = input.value.trim();
    if (question) actions.ask("chat", question);
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });
  const SUGGESTED = [
    "Which targets could be one continuous pipe run?",
    "Which detections look like clutter or ringing?",
    "Which targets should be dug first, and why?",
  ];
  const suggestions = state.assistant.chat.length ? null : [
    el("div", { class: "suggestions-title", text: "Try asking" }),
    el("div", { class: "suggestions" }, SUGGESTED.map((q) =>
      el("button", { class: "suggestion", disabled: Boolean(state.assistant.busy), text: q, onclick: () => actions.ask("chat", q) }))),
  ];
  const log = state.assistant.chat.map((turn) =>
    turn.role === "operator"
      ? el("div", { class: "turn operator", text: turn.text })
      : el("div", { class: "turn assistant" }, [replyBlock(turn.reply)]));
  return [
    ...(suggestions ?? [el("div", { class: "chat-log" }, log)]),
    input,
    el("div", { class: "btn-row" }, [
      el("button", { class: "btn primary", disabled: Boolean(state.assistant.busy), text: state.assistant.busy === "chat" ? "Thinking…" : "Send", onclick: send }),
    ]),
  ];
}

function layerTab(state, layer) {
  const busy = state.assistant.busy === layer;
  const nodes = [el("p", { class: "panel-note", text: INTRO[layer] })];
  if (layer === "live") {
    nodes.push(el("label", { class: "check" }, [
      el("input", { type: "checkbox", checked: state.assistant.live, onchange: actions.toggleLive }),
      " Explain automatically whenever a line or channel opens",
    ]));
  }
  nodes.push(el("div", { class: "btn-row" }, [
    el("button", {
      class: "btn primary",
      disabled: Boolean(state.assistant.busy),
      text: busy ? "Reading…" : RUN_LABEL[layer],
      onclick: () => (layer === "briefing" ? actions.briefing() : actions.ask(layer)),
    }),
  ]));
  nodes.push(replyBlock(state.assistant.replies[layer]));
  if (layer === "briefing" && state.assistant.replies.briefing) {
    nodes.push(el("div", { class: "btn-row" }, [
      el("a", { class: "btn ghost", href: api.briefingReportUrl(), target: "_blank", rel: "noopener", text: "Download briefing (PDF)" }),
    ]));
  }
  return nodes;
}

// Rebuilt only when what it shows changes: render() runs on every cursor move, and a rebuild
// would wipe a question half-typed into the chat box. State is immutable, so references suffice.
let lastDeps = [];

export function render() {
  const state = getState();
  const deps = [state.job, state.channel, state.assistant, state.reviews];
  if (deps.every((dep, i) => dep === lastDeps[i])) return;
  lastDeps = deps;
  const tab = state.assistant.tab;
  const tabs = el("div", { class: "tabs", role: "tablist" }, TABS.map(([key, label]) =>
    el("button", { class: `tab${key === tab ? " active" : ""}`, role: "tab", text: label, onclick: () => setState({ assistant: { ...state.assistant, tab: key } }) })));
  const needsLine = tab !== "briefing" && !state.job;
  const body = needsLine
    ? [el("p", { class: "empty", text: "Open a line to ask about it." })]
    : tab === "chat" ? chatTab(state) : layerTab(state, tab);
  const error = state.assistant.error && !state.assistant.replies[tab]?.error
    ? el("p", { class: "caveat", text: state.assistant.error }) : null;
  replace(container, tabs, error, ...body);
}
