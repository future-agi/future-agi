import { describe, expect, it } from "vitest";

import { TracerInternalErrorFeedV2AttemptsConversationEvidenceCreateResponse as Response } from "src/generated/api-contracts/api.zod";

// One served row, as the backend builds it for a provider call (see
// futureagi/tracer/tests/test_conversation_evidence.py): nested objects,
// numbers, booleans, nulls, and one turn of every kind.
const row = {
  project_id: "22222222-2222-2222-2222-222222222222",
  trace_id: "33333333-3333-3333-3333-333333333333",
  org_id: "44444444-4444-4444-4444-444444444444",
  id: "span-1",
  parent_span_id: "",
  name: "Call Log",
  observation_type: "conversation",
  start_time: "2026-09-11T10:00:00+00:00",
  end_time: "2026-09-11T10:00:42+00:00",
  attrs_string: {
    "conversation.recording.mono.combined":
      "https://recordings.example.test/call.wav",
  },
  conversation: {
    provider: "retell",
    agent_instructions: "Answer in one sentence.",
    call: {
      status: "ended",
      direction: "outbound",
      duration_seconds: 42.5,
      ended_reason: "agent_hangup",
      agent: { id: "agent_1", version: 3, name: "Scheduler" },
    },
    variables: {
      configured: { greeting_line: "Hello.", retries: 2 },
      collected: { current_node: "voicemail" },
    },
    analysis: {
      summary: "Reached voicemail.",
      successful: false,
      in_voicemail: true,
      sentiment: "Neutral",
      flags: { wrong_person: false },
    },
    latency_ms: { llm: { p50: 800, p90: 1200, max: 1500, num: 3 } },
    turns: [
      {
        i: 0,
        role: "transition",
        at: 0.1,
        from: "start",
        to: "greeting",
        type: "auto",
      },
      {
        i: 1,
        role: "agent",
        start: 0.5,
        end: 2.6,
        text: "Hello.",
        spoken: true,
      },
      {
        i: 2,
        role: "user",
        start: 3,
        end: 4.5,
        text: "Please leave a message.",
      },
      {
        i: 3,
        role: "tool_call",
        at: 6.2,
        id: "call_1",
        name: "end_call",
        arguments: "{}",
      },
      {
        i: 4,
        role: "tool_result",
        at: 6.4,
        id: "call_1",
        ok: true,
        content: "ok",
      },
      {
        i: 5,
        role: "agent",
        start: null,
        end: null,
        text: "Hi",
        spoken: false,
      },
      { i: 6, role: "transfer_target", start: null, end: null, text: "No." },
      { i: 7, role: "dtmf", at: null, digit: "2" },
      { i: 8, role: "sms", at: null, text: "Code 1234", media: [null] },
      { i: 9, role: "injected", at: 7, text: "A returning customer." },
    ],
    provider_log_issues: [{ at: 12.5, level: "error", message: "timed out" }],
    not_included: ["recording_audio"],
  },
};

describe("generated conversation evidence contract", () => {
  it("accepts a served call record and keeps every key", () => {
    expect(Response.parse({ rows: [row] })).toEqual({ rows: [row] });
  });

  it("accepts a record without instructions, end time or duration", () => {
    const sparse = {
      ...row,
      end_time: null,
      conversation: {
        ...row.conversation,
        agent_instructions: null,
        call: { ...row.conversation.call, duration_seconds: null },
        provider_log_issues: undefined,
        not_included: ["provider_log", "recording_audio"],
      },
    };

    expect(Response.safeParse({ rows: [sparse] }).success).toBe(true);
  });

  it.each([
    ["a turn without its index", { role: "agent" }],
    [
      "a turn time that is not a number",
      { i: 0, role: "agent", start: "soon" },
    ],
  ])("rejects %s", (_name, turn) => {
    const broken = {
      ...row,
      conversation: { ...row.conversation, turns: [turn] },
    };

    expect(Response.safeParse({ rows: [broken] }).success).toBe(false);
  });
});
