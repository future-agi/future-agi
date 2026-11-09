import { describe, it, expect } from "vitest";
import {
  projectConversation,
  conversationInFlight,
  conversationWaiting,
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

  it("finalizes a lingering running tool once the run is no longer active", () => {
    // A tool call whose result never arrived because the run stopped mid-call:
    // its dot must not keep pulsing after the run is no longer working.
    const turns = projectConversation({
      state: "idle",
      messages: [],
      events: [
        evt({ event_id: "e1", kind: "tool_started", function_call_id: "c1", emitted_at: "2026-09-22T10:00:00Z", payload: { label: "read run" } }),
      ],
    });
    const tool = turns.flatMap((t) => t.steps || []).find((s) => s.kind === "tool");
    expect(tool.state).not.toBe("running");
    expect(tool.state).toBe("interrupted");
  });

  it("keeps a running tool pulsing while the run is actively responding", () => {
    const turns = projectConversation({
      state: "responding",
      messages: [],
      events: [
        evt({ event_id: "e1", kind: "tool_started", function_call_id: "c1", emitted_at: "2026-09-22T10:00:00Z", payload: { label: "read run" } }),
      ],
    });
    const tool = turns.flatMap((t) => t.steps || []).find((s) => s.kind === "tool");
    expect(tool.state).toBe("running");
  });

  it("finalizes a running tool from an earlier turn when a new user turn starts", () => {
    const turns = projectConversation({
      state: "responding",
      messages: [
        msg({ message_id: "u1", role: "user", content: "go", created_at: "2026-09-22T10:00:00Z" }),
        msg({ message_id: "u2", role: "user", content: "again", created_at: "2026-09-22T10:00:10Z" }),
      ],
      events: [
        evt({ event_id: "e1", kind: "tool_started", function_call_id: "c1", emitted_at: "2026-09-22T10:00:01Z", payload: { label: "read run" } }),
      ],
    });
    const tool = turns.flatMap((t) => t.steps || []).find((s) => s.kind === "tool");
    expect(tool.state).toBe("interrupted");
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

  it("opens a Rebuild's builder turn with a line naming what it updates, not a bubble", () => {
    const command = (stage, label) =>
      msg({
        message_id: `c-${stage}`,
        role: "user",
        content: "",
        state: "delivered",
        payload: {
          command_kind: "nudge",
          nudge_id: "n1",
          stage,
          trigger_label: label,
        },
        created_at: "2026-09-22T10:00:02Z",
        sequence: 2,
      });
    const turns = projectConversation({
      messages: [
        msg({
          message_id: "u1",
          role: "user",
          content: "hi",
          created_at: "2026-09-22T10:00:00Z",
          sequence: 1,
        }),
        command("environment", "v3"),
        msg({
          message_id: "a1",
          content: "Added the tool.",
          created_at: "2026-09-22T10:00:03Z",
          sequence: 3,
        }),
      ],
      events: [],
    });
    expect(turns.filter((t) => t.role === "user").map((t) => t.text)).toEqual([
      "hi",
    ]);
    const builder = turns[turns.length - 1];
    expect(builder.role).toBe("builder");
    expect(builder.steps.map((s) => [s.kind, s.text])).toEqual([
      ["nudge-line", "Updating the environment for agent v3…"],
      ["note", "Added the tool."],
    ]);

    const suite = projectConversation({
      messages: [command("suite", "v5")],
      events: [],
    });
    expect(suite[0].steps[0].text).toBe(
      "Updating the scenarios for environment v5…",
    );
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

describe("projectConversation nudge cards", () => {
  const card = (over = {}) =>
    msg({
      message_id: "n-msg",
      role: "system",
      kind: "status",
      content: "Agent v3 changed: prompt. Rebuild the environment?",
      payload: { nudge_id: "n1", nudge_target: "environment", trigger_label: "v3" },
      created_at: "2026-09-22T10:00:01Z",
      ...over,
    });

  it("projects a nudge message to its own system turn", () => {
    const turns = projectConversation({ messages: [card()], events: [] });
    expect(turns).toEqual([
      {
        id: "n-msg",
        role: "system",
        kind: "nudge",
        nudgeId: "n1",
        target: "environment",
        text: "Agent v3 changed: prompt. Rebuild the environment?",
      },
    ]);
  });

  it("splits a builder turn, so the next tool starts a new one", () => {
    const turns = projectConversation({
      messages: [card()],
      events: [
        evt({ event_id: "e1", kind: "tool_started", function_call_id: "c1", emitted_at: "2026-09-22T10:00:00Z", payload: { label: "Read" } }),
        evt({ event_id: "e2", kind: "tool_started", function_call_id: "c2", emitted_at: "2026-09-22T10:00:02Z", payload: { label: "Write" } }),
      ],
    });
    expect(turns.map((t) => t.role)).toEqual(["builder", "system", "builder"]);
    expect(turns[0].steps.map((s) => s.label)).toEqual(["Read"]);
    expect(turns[2].steps.map((s) => s.label)).toEqual(["Write"]);
  });

  it("never lands inside background run activity", () => {
    const turns = projectConversation({
      messages: [card()],
      events: [
        evt({ event_id: "e1", emitted_at: "2026-09-22T10:00:00Z", payload: { event: { text: "reading" } } }),
        evt({ event_id: "e2", emitted_at: "2026-09-22T10:00:02Z", payload: { event: { text: "writing" } } }),
      ],
    });
    expect(turns.map((t) => t.role)).toEqual(["builder", "system", "builder"]);
    expect(turns[0].title).toBe("Background run activity");
    expect(turns[2].title).toBe("Background run activity");
  });

  it("keeps a tool that is still running when a card arrives mid-turn", () => {
    const turns = projectConversation({
      state: "responding",
      messages: [card()],
      events: [
        evt({
          event_id: "e1",
          kind: "tool_started",
          function_call_id: "c1",
          emitted_at: "2026-09-22T10:00:00Z",
          payload: { label: "Read" },
        }),
      ],
    });
    expect(turns.map((t) => t.role)).toEqual(["builder", "system", "builder"]);
    expect(turns[0].steps[0]).toMatchObject({
      label: "Read",
      state: "running",
    });
  });

  it("keeps a system message without a nudge as a builder note", () => {
    const turns = projectConversation({
      messages: [card({ payload: {}, content: "Saved." })],
      events: [],
    });
    expect(turns[0].role).toBe("builder");
    expect(turns[0].steps[0]).toMatchObject({ kind: "note", text: "Saved." });
  });

  it("projects a re-apply offer to a plain note of its own, without the question", () => {
    const turns = projectConversation({
      messages: [
        card({
          content:
            "The edits that went live with v3 were rolled back. Re-apply them in chat?",
          payload: {
            reapply: { piece: "environment", version_id: "e3", label: "v3" },
          },
        }),
      ],
      events: [
        evt({
          event_id: "e1",
          kind: "tool_started",
          function_call_id: "c1",
          emitted_at: "2026-09-22T10:00:00Z",
          payload: { label: "Read" },
        }),
      ],
    });
    expect(turns.map((t) => t.role)).toEqual(["builder", "system"]);
    expect(turns[1]).toEqual({
      id: "n-msg",
      role: "system",
      kind: "reapply",
      text: "The edits that went live with v3 were rolled back.",
    });
  });

  it("is never in flight", () => {
    expect(conversationInFlight({ state: "idle", messages: [card()] })).toBe(false);
  });
});

describe("conversationWaiting", () => {
  const question = { message_id: "q1", kind: "question_requested" };

  it("is true while a question is open and unanswered", () => {
    expect(conversationWaiting({ state: "waiting_for_user", blocking_input: question, messages: [] })).toBe(true);
    expect(conversationWaiting({ state: "waiting_for_user", messages: [] })).toBe(true);
    // The question can be open before the state catches up.
    expect(conversationWaiting({ state: "warm_idle", blocking_input: question, messages: [] })).toBe(true);
  });

  it("is false once the question is answered, or when nothing is asked", () => {
    expect(
      conversationWaiting({
        state: "waiting_for_user",
        blocking_input: question,
        messages: [{ role: "user", reply_to: "q1", state: "queued" }],
      }),
    ).toBe(false);
    expect(conversationWaiting({ state: "responding", messages: [] })).toBe(false);
    expect(conversationWaiting(null)).toBe(false);
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
