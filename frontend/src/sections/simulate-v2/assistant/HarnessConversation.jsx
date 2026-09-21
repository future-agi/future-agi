import PropTypes from "prop-types";
import { useCallback, useEffect, useRef, useState } from "react";
import axiosInstance from "src/utils/axios";

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
      const { data } = await axiosInstance.get(`/simulate/api/harness-jobs/${jobId}/chat/`, {
        params: { after: cursor.current },
      });
      const fresh = data?.events || [];
      cursor.current = data?.cursor ?? cursor.current;
      if (fresh.length) {
        // What the person typed is shown at once so the box does not feel dead, and the harness
        // records the same line in its own transcript a turn later. Both were being rendered, so
        // every message appeared twice. The local copy is dropped as soon as its own line arrives.
        const arrived = fresh.filter((one) => one.kind === "said").map((one) => (one.text || "").trim());
        setEvents((prev) => [
          ...prev.filter((one) => !(one.pending && arrived.includes((one.text || "").trim()))),
          ...fresh,
        ]);
        setRunning(false);
      }
      setProblem("");
    } catch (error) {
      setProblem(error?.response?.data?.message || "this run is not up, so there is nobody to talk to");
    }
  }, [jobId]);

  useEffect(() => {
    read();
    const timer = setInterval(read, POLL_MS);
    return () => clearInterval(timer);
  }, [read]);

  const post = useCallback(
    async (body) => {
      try {
        await axiosInstance.post(`/simulate/api/harness-jobs/${jobId}/chat/`, body);
      } catch (error) {
        setRunning(false);
        setProblem(error?.response?.data?.message || "the message could not be delivered");
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
      post({ answer_to: askId, ...picked, text: said });
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
