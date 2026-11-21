import { it, expect } from "vitest";
import {
  conversationTimeline,
  discoverRecordings,
} from "../conversationTimeline";
import { parent, spanImage } from "../../attachments/__tests__/fixtures";
const rec = (i) =>
  spanImage(i, {
    reference: {
      modality: "audio",
      mime_type: "audio/wav",
      purpose: "conversation_recording",
    },
  });
const node = (id, name, attachments = [], children = [], meta = {}) => ({
  observation_span: {
    id: String(id).padStart(16, "0"),
    trace: parent.trace_id,
    name,
    attachments,
    ...meta,
  },
  children,
});
it("matches the exact parent on root/owner only, and keeps distinct recordings distinct", () => {
  const turn = node(3, "user_turn", [
      spanImage(3, {
        reference: {
          modality: "audio",
          mime_type: "audio/wav",
          purpose: "turn_recording",
        },
      }),
    ]),
    owner = node(2, "agent_session", [rec(1), rec(2)], [turn]),
    root = node(1, "job_entrypoint", [], [owner]),
    other = node(4, "conversation", [rec(3)]),
    tree = [root, other];
  expect(
    discoverRecordings(tree, root.observation_span, true).map((r) => r.owner),
  ).toEqual([owner, owner]);
  expect(discoverRecordings(tree, owner.observation_span, false)).toHaveLength(
    2,
  );
  expect(discoverRecordings(tree, turn.observation_span, false)).toEqual([]);
  expect(
    discoverRecordings(tree, other.observation_span, true).map((r) => r.owner),
  ).toEqual([other]);
});
it("keeps source order, timed speech, operations, trace-relative time and untimed events distinct", () => {
  const item = rec(1),
    origin = "2026-01-01T01:00:00Z",
    childTime = "2026-01-01T01:00:00.500Z";
  const owner = node(
    2,
    "session",
    [item],
    [
      node(3, "generate", [], [], {
        input: [{ content: "old conversation" }],
        observation_type: "llm",
        start_time: childTime,
      }),
      node(4, "search", [], [], {
        start_time: childTime,
        observation_type: "retriever",
      }),
      node(5, "Fulfill", [], [], {
        observation_type: "agent",
        span_attributes: { "gen_ai.agent.name": "Shipping" },
      }),
    ],
    {
      start_time: origin,
      span_attributes: {
        "fi.conversation.transcript": JSON.stringify([
          { speaker_name: "Jess", role: "user", text: "Hello", start_ms: 25 },
          { role: "assistant", text: "Done", occurrence_id: "last" },
        ]),
      },
      span_events: [{ name: "closed", timestamp: "2026-01-01T01:01:00Z" }],
    },
  );
  const rows = conversationTimeline(
    { owner, item },
    { duration_ms: 1000, segments: [{ occurrence_id: "last", start_ms: 842 }] },
  );
  expect(rows.map((r) => [r.actor, r.text, r.seekMs])).toEqual([
    ["Jess", "Hello", 25],
    ["LLM", "generate", 500],
    ["Retrieval", "search", 500],
    ["Agent", "Done", 842],
    ["Event", "closed", null],
    ["Shipping", "Fulfill", null],
  ]);
  expect(rows.map((r) => r.clockLabel)).toEqual([
    "Recording time",
    "Trace time",
    "Trace time",
    "Recording time",
    "Trace time",
    "Time not captured",
  ]);
});
it("without a transcript renders declared turn text, not inferred chat history", () => {
  const item = rec(0),
    owner = node(
      1,
      "session",
      [item],
      [
        node(2, "user_turn", [], [], { input: "Weather?" }),
        node(3, "completion", [], [], {
          observation_type: "llm",
          input: [{ text: "history" }],
        }),
      ],
    );
  expect(
    conversationTimeline({ owner, item }).map((r) => [r.text, r.seekMs]),
  ).toEqual([
    ["Weather?", null],
    ["completion", null],
  ]);
});
