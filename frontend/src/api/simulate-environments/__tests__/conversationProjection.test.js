import { describe, it, expect } from "vitest";
import {
  projectConversation,
  conversationInFlight,
} from "../conversationProjection";

const msg = (over = {}) => ({
  message_id: "m1",
  sequence: 1,
  role: "assistant",
  kind: "message",
  state: "completed",
  content: "hi",
  created_at: "2026-09-22T10:00:00Z",
  ...over,
});
const evt = (over = {}) => ({
  event_id: "e1",
  sequence: 1,
  kind: "authoring_activity",
  emitted_at: "2026-09-22T10:00:01Z",
  payload: {},
  ...over,
});

describe("projectConversation", () => {
  it("returns [] for a missing conversation", () => {
    expect(projectConversation(null)).toEqual([]);
    expect(projectConversation(undefined)).toEqual([]);
  });

  it("maps a user message to a user turn and an assistant message to a builder note", () => {
    const turns = projectConversation({
      messages: [
        msg({ message_id: "u1", role: "user", content: "add a scenario", created_at: "2026-09-22T10:00:00Z" }),
        msg({ message_id: "a1", role: "assistant", content: "done", created_at: "2026-09-22T10:00:02Z" }),
      ],
      events: [],
    });
    expect(turns).toHaveLength(2);
    expect(turns[0]).toMatchObject({ role: "user", text: "add a scenario", id: "u1" });
    expect(turns[1].role).toBe("builder");
    expect(turns[1].steps[0]).toMatchObject({ kind: "note", text: "done", markdown: true });
  });

  it("joins tool_started and tool_result by function_call_id, keeping is_error", () => {
    const turns = projectConversation({
      messages: [],
      events: [
        evt({ event_id: "e1", kind: "tool_started", function_call_id: "c1", emitted_at: "2026-09-22T10:00:00Z", payload: { label: "Read", tool: "read_file" } }),
        evt({ event_id: "e2", kind: "tool_result", function_call_id: "c1", emitted_at: "2026-09-22T10:00:01Z", payload: { text: "42 lines", is_error: false } }),
      ],
    });
    const tools = turns[0].steps.filter((s) => s.kind === "tool");
    expect(tools).toHaveLength(1);
    expect(tools[0]).toMatchObject({ label: "Read", state: "completed", result: "42 lines" });
  });

  it("marks a tool failed when the result is an error", () => {
    const turns = projectConversation({
      messages: [],
      events: [
        evt({ event_id: "e1", kind: "tool_started", function_call_id: "c1", payload: { label: "Read" } }),
        evt({ event_id: "e2", kind: "tool_result", function_call_id: "c1", emitted_at: "2026-09-22T10:00:02Z", payload: { text: "boom", is_error: true } }),
      ],
    });
    expect(turns[0].steps.find((s) => s.kind === "tool")).toMatchObject({ state: "failed", result: "boom" });
  });

  it("folds consecutive activity/stage events into one group with a count", () => {
    const turns = projectConversation({
      messages: [],
      events: [
        evt({ event_id: "e1", kind: "authoring_activity", emitted_at: "2026-09-22T10:00:00Z", payload: { event: { text: "scanning tests" } } }),
        evt({ event_id: "e2", kind: "stage_changed", emitted_at: "2026-09-22T10:00:01Z", payload: { to: "generating_environment" } }),
        evt({ event_id: "e3", kind: "authoring_activity", emitted_at: "2026-09-22T10:00:02Z", payload: { event_type: "read" } }),
      ],
    });
    const group = turns[0].steps.find((s) => s.kind === "group");
    expect(group.count).toBe(3);
    expect(group.lines).toEqual(["scanning tests", "ALK moved to Generating environment", "Read"]);
  });

  it("renders a question as an unresolved ask with normalized string options", () => {
    const turns = projectConversation({
      messages: [
        msg({ message_id: "q1", kind: "question", content: "How strict?", payload: { prompt: "How strict?", options: ["Strict", "Lenient"] } }),
      ],
      events: [],
      blocking_input: { message_id: "q1", kind: "question_requested", prompt: "How strict?", options: ["Strict", "Lenient"] },
    });
    const ask = turns[0].steps[0];
    expect(ask.kind).toBe("ask");
    expect(ask.replyTo).toBe("q1");
    expect(ask.resolved).toBe(false);
    expect(ask.question.options).toEqual([{ label: "Strict" }, { label: "Lenient" }]);
  });

  it("marks a question resolved once a user message replies to it", () => {
    const turns = projectConversation({
      messages: [
        msg({ message_id: "q1", kind: "question", content: "How strict?", payload: { options: ["Strict"] }, created_at: "2026-09-22T10:00:00Z" }),
        msg({ message_id: "u2", role: "user", content: "Strict", reply_to: "q1", created_at: "2026-09-22T10:00:05Z" }),
      ],
      events: [],
    });
    const ask = turns[0].steps.find((s) => s.kind === "ask");
    expect(ask.resolved).toBe(true);
    expect(ask.answerText).toBe("Strict");
  });

  it("maps a failed assistant message to an error step and an interrupt to cancelled", () => {
    const turns = projectConversation({
      messages: [msg({ message_id: "a1", state: "failed", content: "nope" })],
      events: [evt({ event_id: "e9", kind: "turn_interrupted", emitted_at: "2026-09-22T10:00:05Z" })],
    });
    const steps = turns.flatMap((t) => t.steps || []);
    expect(steps.find((s) => s.kind === "error")).toMatchObject({ text: "nope" });
    expect(steps.find((s) => s.kind === "cancelled")).toBeTruthy();
  });

  it("keeps background run activity out of a failed coordinator reply", () => {
    const turns = projectConversation({
      messages: [
        msg({ message_id: "u1", role: "user", content: "Why did it restart?", created_at: "2026-09-22T10:00:00Z" }),
        msg({ message_id: "a1", content: "I couldn't complete that turn: ResultError.", created_at: "2026-09-22T10:00:01Z" }),
      ],
      events: [
        evt({ event_id: "failure", kind: "turn_completed", emitted_at: "2026-09-22T10:00:02Z", payload: { outcome: "failed" } }),
        evt({ event_id: "build", kind: "authoring_activity", emitted_at: "2026-09-22T10:00:03Z", payload: { event: { text: "19 records put into places" } } }),
      ],
    });
    const reply = turns.find((turn) => turn.steps?.some((step) => step.text?.includes("ResultError")));
    const activity = turns.find((turn) => turn.title === "Background run activity");
    expect(reply.steps.some((step) => step.kind === "group")).toBe(false);
    expect(activity.steps[0]).toMatchObject({ kind: "group", lines: ["19 records put into places"] });
  });

  it("pairs chat tool results across independent background activity", () => {
    const turns = projectConversation({
      messages: [],
      events: [
        evt({ event_id: "start", kind: "tool_started", function_call_id: "call-1", emitted_at: "2026-09-22T10:00:00Z", payload: { label: "Read" } }),
        evt({ event_id: "build", kind: "authoring_activity", emitted_at: "2026-09-22T10:00:01Z", payload: { event: { text: "Seeding places" } } }),
        evt({ event_id: "result", kind: "tool_result", function_call_id: "call-1", emitted_at: "2026-09-22T10:00:02Z", payload: { text: "42 lines" } }),
      ],
    });
    expect(turns).toHaveLength(2);
    expect(turns[0].steps[0]).toMatchObject({ kind: "tool", state: "completed", result: "42 lines" });
    expect(turns[1]).toMatchObject({ title: "Background run activity" });
  });

  it("appends a heartbeat while the run is actively working and not blocked", () => {
    const turns = projectConversation({
      state: "responding",
      messages: [],
      events: [evt({ event_id: "e1", kind: "authoring_activity", emitted_at: "2026-09-22T10:00:00Z", payload: { event_type: "read" } })],
    });
    expect(turns.at(-1).steps.at(-1)).toMatchObject({ kind: "heartbeat", since: "2026-09-22T10:00:00Z" });
  });

  it("does NOT heartbeat while waiting for the user", () => {
    const turns = projectConversation({
      state: "waiting_for_user",
      blocking_input: { message_id: "q1", kind: "question_requested" },
      messages: [msg({ message_id: "q1", kind: "question", payload: { options: [] } })],
      events: [],
    });
    expect(turns.flatMap((t) => t.steps).some((s) => s.kind === "heartbeat")).toBe(false);
  });

  it("omits interrupt/cancel control messages from the transcript", () => {
    const turns = projectConversation({
      messages: [
        msg({ message_id: "u1", role: "user", content: "hi", created_at: "2026-09-22T10:00:00Z", sequence: 1 }),
        msg({ message_id: "s1", role: "user", content: "Stop", payload: { command_kind: "interrupt" }, created_at: "2026-09-22T10:00:02Z", sequence: 2 }),
      ],
      events: [],
    });
    const userTexts = turns.filter((t) => t.role === "user").map((t) => t.text);
    expect(userTexts).toEqual(["hi"]);
  });

  it("gives every turn and step a stable id", () => {
    const turns = projectConversation({
      messages: [msg({ message_id: "a1", content: "x" })],
      events: [evt({ kind: "authoring_activity", payload: { event_type: "read" } })],
    });
    turns.forEach((t) => {
      expect(t.id).toBeTruthy();
      (t.steps || []).forEach((s) => expect(s.id).toBeTruthy());
    });
  });
});

describe("conversationInFlight", () => {
  it("is true when a message is queued/delivered/streaming or the state is active", () => {
    expect(conversationInFlight({ messages: [{ state: "queued" }] })).toBe(true);
    expect(conversationInFlight({ state: "responding", messages: [] })).toBe(true);
    expect(conversationInFlight({ state: "warm_idle", messages: [{ state: "completed" }] })).toBe(false);
    expect(conversationInFlight(null)).toBe(false);
  });
});
