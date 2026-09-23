import { readable } from "src/pages/dashboard/harness/harnessShared";

// Project a hosted-conversation document (messages[] + events[]) into the
// BuilderConsole turn/step model. Pure and side-effect free: the hook that owns
// the react-query poll calls this, memoises the result, and attaches the
// onSubmit/onSkip callbacks to `ask` steps afterwards.
//
// Two streams arrive together: `messages` are the chat bubbles (role/kind/state)
// and `events` are the lower-level activity (tool calls, stage moves, turn
// lifecycle). A tool_started is paired with its tool_result by function_call_id;
// consecutive activity events fold into one "Run activity · N updates" group.

// Conversation-level states that mean the agent is actively working.
export const ACTIVE_CONVERSATION_STATES = new Set([
  "starting",
  "hydrating",
  "responding",
]);
// A message still in flight (drives the composer's brief busy state upstream).
export const IN_FLIGHT_MESSAGE_STATES = new Set([
  "queued",
  "delivered",
  "streaming",
]);

function normalizeQuestion(message, blocking) {
  const p = message.payload || {};
  // The open question also carries prompt/options on blocking_input; prefer the
  // message payload and fall back to blocking for the row that is still open.
  const source =
    blocking && blocking.message_id === message.message_id ? { ...p, ...blocking } : p;
  const rawOptions = Array.isArray(source.options) ? source.options : [];
  const options = rawOptions.map((opt) =>
    typeof opt === "string"
      ? { label: opt }
      : { label: opt?.label ?? String(opt), description: opt?.description },
  );
  return {
    prompt: source.prompt || message.content || "",
    options,
    multiSelect: Boolean(source.multiSelect ?? source.multi_select),
    step: source.step || 1,
    total: source.total || 1,
  };
}

// Demote any tool step still marked "running" to a terminal "interrupted" state,
// so its dot stops pulsing. Used when a turn ends or the run is no longer
// working: a tool left "running" never received its result (the run moved on).
function finalizeRunningSteps(steps) {
  for (const step of steps || []) {
    if (step.kind === "tool" && step.state === "running") {
      step.state = "interrupted";
      if (!step.result) step.result = "Didn't finish";
    }
  }
}

export function projectConversation(conversation) {
  if (!conversation) return [];
  const messages = Array.isArray(conversation.messages) ? conversation.messages : [];
  const events = Array.isArray(conversation.events) ? conversation.events : [];
  const blocking = conversation.blocking_input || null;

  // A question is resolved once a user message replies to it; capture the answer
  // text so a refreshed conversation renders the resolved summary.
  const answerFor = {};
  for (const m of messages) {
    if (m.reply_to && m.role === "user") answerFor[m.reply_to] = m.content;
  }

  const timeline = [
    ...messages.map((m) => ({ src: "msg", t: m.created_at, seq: m.sequence, data: m })),
    ...events.map((e) => ({ src: "evt", t: e.emitted_at, seq: e.sequence, data: e })),
  ].sort((a, b) => {
    const ta = Date.parse(a.t) || 0;
    const tb = Date.parse(b.t) || 0;
    if (ta !== tb) return ta - tb;
    // At the same wall-clock a message sorts just before its own events.
    if (a.src !== b.src) return a.src === "msg" ? -1 : 1;
    return (a.seq || 0) - (b.seq || 0);
  });

  const turns = [];
  let builder = null;
  let toolByCall = null;

  const startBuilder = () => {
    if (!builder) {
      builder = { id: null, role: "builder", steps: [] };
      toolByCall = new Map();
      turns.push(builder);
    }
    return builder;
  };
  const endBuilder = () => {
    // A turn that ends with a tool still "running" never received its result —
    // the run moved on (a new turn, an interrupt, a stop). Demote it so its dot
    // stops pulsing, rather than leaving a permanently in-flight indicator.
    finalizeRunningSteps(builder?.steps);
    builder = null;
    toolByCall = null;
  };

  const pushActivity = (line, seedId) => {
    const b = startBuilder();
    const last = b.steps[b.steps.length - 1];
    if (last && last.kind === "group") {
      last.lines.push(line);
      last.count = last.lines.length;
    } else {
      b.steps.push({ id: `grp-${seedId}`, kind: "group", count: 1, lines: [line] });
    }
  };

  for (const item of timeline) {
    if (item.src === "msg") {
      const m = item.data;
      if (m.role === "user") {
        // Control commands (Stop / cancel) are stored as user messages with a
        // command_kind — they are not chat turns, so keep them out of the transcript.
        const cmd = m.payload?.command_kind;
        if (cmd === "interrupt" || cmd === "cancel_operation") continue;
        // A reply to a blocking question is already surfaced in that question's
        // resolved ask card (answerText) — don't also render it as a bubble.
        if (m.reply_to) continue;
        endBuilder();
        turns.push({ id: m.message_id, role: "user", text: m.content });
        continue;
      }
      if (m.kind === "question" || m.kind === "confirmation") {
        const b = startBuilder();
        const answered = Object.prototype.hasOwnProperty.call(answerFor, m.message_id);
        b.steps.push({
          id: m.message_id,
          kind: "ask",
          question: normalizeQuestion(m, blocking),
          replyTo: m.message_id,
          confirmation: m.kind === "confirmation",
          resolved: answered,
          answerText: answered ? answerFor[m.message_id] : undefined,
        });
        continue;
      }
      const b = startBuilder();
      if (m.state === "failed") {
        b.steps.push({
          id: m.message_id,
          kind: "error",
          text: m.content || "The builder hit an error.",
        });
      } else if (m.content) {
        b.steps.push({ id: m.message_id, kind: "note", text: m.content, markdown: true });
      }
      continue;
    }

    const e = item.data;
    switch (e.kind) {
      case "tool_started": {
        const b = startBuilder();
        const step = {
          id: `tool-${e.function_call_id || e.event_id}`,
          kind: "tool",
          label: e.payload?.label || e.payload?.tool || "Tool",
          state: "running",
        };
        if (e.function_call_id) toolByCall.set(e.function_call_id, step);
        b.steps.push(step);
        break;
      }
      case "tool_result": {
        const b = startBuilder();
        const existing = e.function_call_id ? toolByCall.get(e.function_call_id) : null;
        const isError = Boolean(e.payload?.is_error);
        const result =
          e.payload?.text ||
          (e.payload?.artifact ? `wrote ${e.payload.artifact}` : "");
        if (existing) {
          existing.state = isError ? "failed" : "completed";
          existing.result = result;
        } else {
          b.steps.push({
            id: `tool-${e.function_call_id || e.event_id}`,
            kind: "tool",
            label: e.payload?.tool || "Tool",
            state: isError ? "failed" : "completed",
            result,
          });
        }
        break;
      }
      case "authoring_activity": {
        const p = e.payload || {};
        const line = p.event?.text || readable(p.event?.tool || p.event_type || "activity");
        pushActivity(line, e.event_id);
        break;
      }
      case "stage_changed": {
        pushActivity(`ALK moved to ${readable(e.payload?.to || "")}`.trim(), e.event_id);
        break;
      }
      case "turn_interrupted": {
        const b = startBuilder();
        b.steps.push({ id: `int-${e.event_id}`, kind: "cancelled", text: "Stopped." });
        break;
      }
      case "turn_completed": {
        if (e.payload?.outcome === "failed") {
          const b = startBuilder();
          b.steps.push({ id: `fail-${e.event_id}`, kind: "error", text: "The turn failed." });
        }
        break;
      }
      default:
        // turn_started / assistant_delta / checkpoint_committed / capability_changed:
        // not user-facing on the poll-parity path.
        break;
    }
  }

  // A persistent "working autonomously" cue while the run is active and not
  // blocked on the user — rendered as a step so the composer stays enabled for
  // interjections (unlike the composer-blocking `running` flag).
  const active = ACTIVE_CONVERSATION_STATES.has(conversation.state);
  const waiting = conversation.state === "waiting_for_user" || Boolean(blocking);
  if (active && !waiting) {
    const b = startBuilder();
    const since = events.length ? events[events.length - 1].emitted_at : undefined;
    b.steps.push({ id: "heartbeat", kind: "heartbeat", since });
  } else {
    // Not actively working (stopped, failed, or waiting on the user): nothing is
    // in flight, so no tool dot should keep pulsing. Finalize any lingering
    // "running" step across every turn (the last one is never ended otherwise).
    turns.forEach((t) => finalizeRunningSteps(t.steps));
  }

  turns.forEach((t, ti) => {
    if (!t.id) t.id = `turn-${ti}`;
    (t.steps || []).forEach((s, si) => {
      if (!s.id) s.id = `${t.id}-s${si}`;
    });
  });

  return turns;
}

// Whether the agent has a turn in flight — used to derive the composer's brief
// busy state and the adaptive poll cadence.
export function conversationInFlight(conversation) {
  if (!conversation) return false;
  if (ACTIVE_CONVERSATION_STATES.has(conversation.state)) return true;
  const messages = Array.isArray(conversation.messages) ? conversation.messages : [];
  return messages.some((m) => IN_FLIGHT_MESSAGE_STATES.has(m.state));
}
