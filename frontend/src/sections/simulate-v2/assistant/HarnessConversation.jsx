import PropTypes from "prop-types";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getHarnessJob,
  sendHarnessConversationMessage,
} from "src/api/harness/harness";

import { SectionCard } from "../components/primitives";
import StudioConsole from "./AssistantConsole";

// The harness appends to its outbox as it works, so reading it back on a timer is what makes the
// conversation live. The cursor means each read carries only what is new.
const POLL_MS = 2000;

const CHIPS = [
  "What does this suite not cover?",
  "Why is this scenario weak?",
  "Which stage did the run reach?",
  "What is in the world right now?",
];

// The durable conversation keeps the transcript and the tool trail apart. The console reads one
// ordered list, so both are folded into the shape `stepFor` already understands.
function conversationEvents(conversation) {
  const messages = (conversation.messages || []).map((one) => ({
    kind: one.role === "user" ? "said" : "text",
    text: one.content || "",
    detail: { stage: one.stage || "" },
    at: one.created_at || "",
    order: one.sequence || 0,
  }));
  const trail = (conversation.events || []).map((one) => ({
    kind: one.kind === "tool_result" ? "result" : "tool",
    text: (one.payload || {}).text || "",
    tool: (one.payload || {}).tool || "",
    detail: { stage: one.stage || "", target: (one.payload || {}).text || "" },
    at: one.emitted_at || "",
    order: one.sequence || 0,
  }));
  return [...messages, ...trail].sort((a, b) => a.order - b.order);
}

function stepFor(event, answer) {
  const detail = event.detail || {};
  if (event.kind === "text") return { kind: "note", text: event.text };
  if (event.kind === "ask") {
    const question = detail.question || {};
    return {
      kind: "ask",
      question: {
        prompt: question.prompt || event.text,
        multiSelect: Boolean(question.multi_select),
        options: (question.options || []).map((one) => ({ label: String(one) })),
      },
      // The card hands its answer back through the step, and the answer carries the id of the
      // question so the harness turn blocked on it wakes with the right reply.
      onSubmit: (picked) => answer(detail.ask_id, picked),
      onSkip: () => answer(detail.ask_id, { skipped: true }),
    };
  }
  if (event.kind === "tool") {
    return { label: event.tool || detail.tool || "tool", result: detail.target || "" };
  }
  if (event.kind === "result") return { kind: "json", label: "result", value: event.text };
  if (event.kind === "artifact") {
    return { kind: "file", path: detail.path || event.text, note: "saved" };
  }
  if (event.kind === "done") {
    const spent = typeof detail.cost_usd === "number" ? ` $${detail.cost_usd.toFixed(4)}` : "";
    return { kind: "note", text: `${detail.outcome || "done"} after ${detail.turns || 0} turns${spent}` };
  }
  return { kind: "note", text: event.text || "" };
}

// Consecutive events from one stage become one assistant turn, because the harness stamps which
// stage emitted each and a turn per line reads as noise.
function foldIntoTurns(events, answer) {
  const turns = [];
  events.forEach((event, index) => {
    if (event.kind === "said") {
      turns.push({ id: `u-${index}`, role: "user", text: event.text });
      return;
    }
    const stage = (event.detail || {}).stage || "";
    const last = turns[turns.length - 1];
    if (last && last.role === "assistant" && last.stage === stage) {
      last.steps.push(stepFor(event, answer));
      return;
    }
    turns.push({ id: `a-${index}`, role: "assistant", title: stage, stage, steps: [stepFor(event, answer)] });
  });
  return turns;
}

export default function HarnessConversation({ jobId }) {
  const [events, setEvents] = useState([]);
  const [running, setRunning] = useState(false);
  const [problem, setProblem] = useState("");
  const cursor = useRef(0);

  const read = useCallback(async () => {
    try {
      const job = await getHarnessJob(jobId);
      const conversation = job?.conversation;
      if (!conversation) {
        setProblem("this run is not up, so there is nobody to talk to");
        return;
      }
      const fresh = conversationEvents(conversation);
      cursor.current = conversation.event_watermark ?? cursor.current;
      // What the person typed is shown at once so the box does not feel dead, and the harness
      // records the same line in its own transcript a turn later. Both were being rendered, so
      // every message appeared twice. The local copy is dropped as soon as its own line arrives.
      const arrived = fresh.filter((one) => one.kind === "said").map((one) => (one.text || "").trim());
      setEvents((prev) => [
        ...prev.filter((one) => one.pending && !arrived.includes((one.text || "").trim())),
        ...fresh,
      ]);
      setRunning(Boolean(conversation.active_invocation_id));
      setProblem("");
    } catch (error) {
      setProblem(error?.detail || error?.message || "this run is not up, so there is nobody to talk to");
    }
  }, [jobId]);

  useEffect(() => {
    read();
    const timer = setInterval(read, POLL_MS);
    return () => clearInterval(timer);
  }, [read]);

  const post = useCallback(
    async ({ text, replyTo = null }) => {
      try {
        const randomUUID = window.crypto?.randomUUID;
        await sendHarnessConversationMessage(jobId, {
          content: text,
          client_request_id:
            typeof randomUUID === "function"
              ? randomUUID.call(window.crypto)
              : `message-${Date.now().toString(36)}`,
          kind: "user_message",
          ...(replyTo ? { reply_to: replyTo } : {}),
        });
      } catch (error) {
        setRunning(false);
        setProblem(error?.detail || error?.message || "the message could not be delivered");
      }
    },
    [jobId]
  );

  const send = useCallback(
    (text) => {
      const said = (text || "").trim();
      if (!said) return;
      setEvents((prev) => [...prev, { kind: "said", text: said, detail: {}, pending: true }]);
      setRunning(true);
      post({ text: said });
    },
    [post]
  );

  // An answer carries the id of the question it answers, so the harness turn waiting on it wakes.
  const answer = useCallback(
    (askId, picked) => {
      const said = picked?.other || picked?.pick || (picked?.picks || []).join(", ");
      setEvents((prev) => [
        ...prev,
        { kind: "said", text: said || "(answered)", detail: {}, pending: true },
      ]);
      setRunning(true);
      post({ text: said || "(answered)", replyTo: askId });
    },
    [post]
  );

  return (
    <SectionCard
      sx={{
        height: "100%",
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <StudioConsole
        turns={foldIntoTurns(events, answer)}
        running={running}
        chips={CHIPS}
        onSend={send}
        onChip={send}
        frozen={Boolean(problem)}
        frozenReason={problem}
      />
    </SectionCard>
  );
}

HarnessConversation.propTypes = {
  jobId: PropTypes.string.isRequired,
};
