import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { useWorkspaceChat } from "../workspaceChat";
import { clearScenarioSelection, publishScenarioSelection } from "src/sections/simulate/environments/buildEnvironment/console/scenarioSelectionBus";

vi.mock("src/api/harness/harness", () => ({
  getHarnessJob: vi.fn(),
  sendHarnessConversationMessage: vi.fn(),
  harnessIdempotencyKey: vi.fn(() => "req-test"),
}));

import {
  getHarnessJob,
  sendHarnessConversationMessage,
  harnessIdempotencyKey,
} from "src/api/harness/harness";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

import axios from "src/utils/axios";
import { nudgesKey } from "../versions";

const ENV = { id: "job-1", name: "Support triage" };

const conversationWith = (over = {}) => ({
  conversation_id: "c1",
  state: "warm_idle",
  runtime: { available: true },
  messages: [],
  events: [],
  ...over,
});

const jobWith = (conversation) => ({
  job: { job_id: "job-1" },
  status: { stage: "completed" },
  conversation,
});

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // eslint-disable-next-line react/prop-types
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, Wrapper };
}

const renderChat = (conversation, opts = {}) => {
  getHarnessJob.mockResolvedValue(jobWith(conversation));
  const { client, Wrapper } = wrapper();
  client.setQueryData(["harness-job", "job-1"], jobWith(conversation));
  const view = renderHook(() => useWorkspaceChat(ENV, { source: "harness" }), { wrapper: Wrapper });
  return { ...view, client, ...opts };
};

describe("useWorkspaceChat (real)", () => {
  beforeEach(() => {
    clearScenarioSelection();
    sendHarnessConversationMessage.mockReset();
    getHarnessJob.mockReset();
    harnessIdempotencyKey.mockReset();
    harnessIdempotencyKey.mockReturnValue("req-test");
  });
  afterEach(() => clearScenarioSelection());

  it("projects turns from the polled conversation", () => {
    const { result } = renderChat(
      conversationWith({
        messages: [
          { message_id: "u1", role: "user", kind: "message", state: "completed", content: "hi", created_at: "2026-09-22T10:00:00Z", sequence: 1 },
          { message_id: "a1", role: "assistant", kind: "message", state: "completed", content: "hello", created_at: "2026-09-22T10:00:01Z", sequence: 2 },
        ],
      }),
    );
    expect(result.current.turns).toHaveLength(2);
    expect(result.current.turns[0]).toMatchObject({ role: "user", text: "hi" });
    expect(result.current.turns[1].steps[0]).toMatchObject({ kind: "note", markdown: true });
    expect(result.current.running).toBe(false);
  });

  it("send posts a user_message and writes the returned conversation into the cache", async () => {
    const next = conversationWith({
      messages: [{ message_id: "u1", role: "user", kind: "message", state: "queued", content: "add a scenario", created_at: "2026-09-22T10:00:05Z", sequence: 1 }],
    });
    sendHarnessConversationMessage.mockResolvedValue(next);
    const { result } = renderChat(conversationWith());

    await act(async () => {
      result.current.send("add a scenario");
    });

    expect(sendHarnessConversationMessage).toHaveBeenCalledWith(
      "job-1",
      expect.objectContaining({
        content: "add a scenario",
        client_request_id: "req-test",
        kind: "user_message",
      }),
    );
    // reply_to must be absent for a plain message
    expect(sendHarnessConversationMessage.mock.calls[0][1].reply_to).toBeUndefined();
    await waitFor(() =>
      expect(result.current.turns.some((t) => t.role === "user" && t.text === "add a scenario")).toBe(true),
    );
  });

  it("reports waiting while the builder's question is unanswered, and not otherwise", async () => {
    const asked = renderChat(
      conversationWith({
        state: "waiting_for_user",
        blocking_input: { message_id: "q1", kind: "question_requested", prompt: "How strict?" },
        messages: [{ message_id: "q1", role: "assistant", kind: "question", state: "completed", content: "How strict?", created_at: "2026-09-22T10:00:00Z", sequence: 1 }],
      }),
    );
    expect(asked.result.current.waiting).toBe(true);
    // Not "in flight": the rail dot needs `waiting` to stay on while the user is asked.
    expect(asked.result.current.inFlight).toBe(false);

    const idle = renderChat(conversationWith());
    expect(idle.result.current.waiting).toBe(false);
  });

  it("doesn't report waiting once the chat can no longer accept an answer", () => {
    // The question stays open on the backend when the job ends without a saved
    // workspace, but the composer is frozen: nothing for the user to do.
    const { result } = renderChat(
      conversationWith({
        state: "waiting_for_user",
        runtime: { available: false },
        blocking_input: { message_id: "q1", kind: "question_requested", prompt: "How strict?" },
        messages: [{ message_id: "q1", role: "assistant", kind: "question", state: "completed", content: "How strict?", created_at: "2026-09-22T10:00:00Z", sequence: 1 }],
      }),
    );
    expect(result.current.frozen).toBe(true);
    expect(result.current.waiting).toBe(false);
  });

  it("routes a reply to a blocking question as user_response with reply_to", async () => {
    sendHarnessConversationMessage.mockResolvedValue(conversationWith());
    const { result } = renderChat(
      conversationWith({
        state: "waiting_for_user",
        blocking_input: { message_id: "q1", kind: "question_requested", prompt: "How strict?", options: ["Strict"] },
        messages: [{ message_id: "q1", role: "assistant", kind: "question", state: "completed", content: "How strict?", payload: { options: ["Strict"] }, created_at: "2026-09-22T10:00:00Z", sequence: 1 }],
      }),
    );

    await act(async () => {
      result.current.send("Strict");
    });

    expect(sendHarnessConversationMessage).toHaveBeenCalledWith(
      "job-1",
      expect.objectContaining({ kind: "user_response", reply_to: "q1", content: "Strict" }),
    );
  });

  it("sends a NEW message (not a reply) when the blocking question is already answered", async () => {
    sendHarnessConversationMessage.mockResolvedValue(conversationWith());
    const { result } = renderChat(
      conversationWith({
        state: "waiting_for_user",
        blocking_input: { message_id: "q1", kind: "question_requested" },
        messages: [
          { message_id: "q1", role: "assistant", kind: "question", state: "completed", content: "How strict?", payload: { options: ["Strict"] }, created_at: "2026-09-22T10:00:00Z", sequence: 1 },
          { message_id: "u1", role: "user", kind: "message", state: "queued", content: "Strict", reply_to: "q1", created_at: "2026-09-22T10:00:05Z", sequence: 2 },
        ],
      }),
    );

    await act(async () => result.current.send("explain the tools"));

    const payload = sendHarnessConversationMessage.mock.calls[0][1];
    expect(payload.kind).toBe("user_message");
    expect(payload.reply_to).toBeUndefined();
  });

  it("carries selected scenario ids and clears the selection on send", async () => {
    sendHarnessConversationMessage.mockResolvedValue(conversationWith());
    publishScenarioSelection({ ids: ["s1", "s2"], rows: [{ name: "a" }, { name: "b" }] });
    const { result } = renderChat(conversationWith());

    await act(async () => {
      result.current.send("make them impatient");
    });

    expect(sendHarnessConversationMessage.mock.calls[0][1].payload.scenario_ids).toEqual(["s1", "s2"]);
  });

  it("answers a confirmation with kind:approval", async () => {
    sendHarnessConversationMessage.mockResolvedValue(conversationWith());
    const { result } = renderChat(
      conversationWith({
        state: "waiting_for_user",
        blocking_input: { message_id: "cf1", kind: "confirmation_requested" },
        messages: [{ message_id: "cf1", role: "assistant", kind: "confirmation", state: "completed", content: "Proceed?", payload: { options: ["Yes"] }, created_at: "2026-09-22T10:00:00Z", sequence: 1 }],
      }),
    );
    await act(async () => result.current.send("Yes"));
    const payload = sendHarnessConversationMessage.mock.calls[0][1];
    expect(payload.kind).toBe("approval");
    expect(payload.reply_to).toBe("cf1");
  });

  it("surfaces the backend error message on a failed send and offers retry only when retryable", async () => {
    sendHarnessConversationMessage.mockRejectedValue({ message: "reply_to does not identify the open harness question", retryable: false, statusCode: 409 });
    const { result } = renderChat(conversationWith());
    await act(async () => result.current.send("hi"));
    await waitFor(() => {
      const errStep = result.current.turns.flatMap((t) => t.steps || []).find((s) => s.kind === "error");
      expect(errStep?.text).toContain("does not identify the open harness question");
      expect(errStep?.onRetry).toBeUndefined();
    });
  });

  it("replays the original payload and idempotency key on retry, not current state", async () => {
    // The first send clears the scenario selection and mints a client_request_id.
    // A retry must replay THAT command — same scenario_ids, same id — so a lost
    // response can't post the turn twice or with a narrower scope.
    harnessIdempotencyKey.mockReturnValueOnce("req-A").mockReturnValueOnce("req-B");
    sendHarnessConversationMessage
      .mockRejectedValueOnce({ message: "network blip", retryable: true, statusCode: 503 })
      .mockResolvedValueOnce(conversationWith());
    publishScenarioSelection({ ids: ["s1", "s2"], rows: [{ name: "a" }, { name: "b" }] });
    const { result } = renderChat(conversationWith());

    await act(async () => result.current.send("make them impatient"));

    let errStep;
    await waitFor(() => {
      errStep = result.current.turns.flatMap((t) => t.steps || []).find((s) => s.kind === "error");
      expect(errStep?.onRetry).toBeInstanceOf(Function);
    });

    await act(async () => errStep.onRetry());

    await waitFor(() => expect(sendHarnessConversationMessage).toHaveBeenCalledTimes(2));
    const first = sendHarnessConversationMessage.mock.calls[0][1];
    const second = sendHarnessConversationMessage.mock.calls[1][1];
    expect(second.payload.scenario_ids).toEqual(["s1", "s2"]);
    expect(second.client_request_id).toBe(first.client_request_id);
    expect(second.client_request_id).toBe("req-A");
  });

  it("writes the response into the job that submitted it, not the mounted job", async () => {
    // Navigate from env A to a cached env B while A's send is still posting. The
    // success handler must update A's ["harness-job", A] cache, never B's.
    let resolveSend;
    sendHarnessConversationMessage.mockImplementation(
      () => new Promise((res) => { resolveSend = res; }),
    );
    const { client, Wrapper } = wrapper();
    // Per-id so the background poll for job-2 restores cB, not cA — isolating
    // the mutation write from the query refetch.
    getHarnessJob.mockImplementation((id) =>
      Promise.resolve(jobWith(conversationWith({ conversation_id: id === "job-2" ? "cB" : "cA" }))),
    );
    client.setQueryData(["harness-job", "job-1"], jobWith(conversationWith({ conversation_id: "cA" })));
    client.setQueryData(["harness-job", "job-2"], jobWith(conversationWith({ conversation_id: "cB" })));

    const { result, rerender } = renderHook(
      ({ env }) => useWorkspaceChat(env, { source: "harness" }),
      { wrapper: Wrapper, initialProps: { env: { id: "job-1", name: "A" } } },
    );

    await act(async () => result.current.send("hi"));
    rerender({ env: { id: "job-2", name: "B" } });

    await act(async () => {
      resolveSend(conversationWith({ conversation_id: "cA-updated" }));
      await Promise.resolve();
    });

    await waitFor(() =>
      expect(client.getQueryData(["harness-job", "job-1"]).conversation.conversation_id).toBe("cA-updated"),
    );
    expect(client.getQueryData(["harness-job", "job-2"]).conversation.conversation_id).toBe("cB");
  });

  it("uses the no-workspace frozen reason for a terminal run with no runtime", () => {
    const { result } = renderChat(conversationWith({ runtime: { available: false } }));
    expect(result.current.frozen).toBe(true);
    expect(result.current.frozenReason).toMatch(/no saved workspace/i);
  });

  it("freezes for a non-harness env and when the runtime is unavailable", () => {
    const nonHarness = renderHook(() => useWorkspaceChat({ id: "x" }, { source: "template" }), { wrapper: wrapper().Wrapper });
    expect(nonHarness.result.current.frozen).toBe(true);

    const { result } = renderChat(conversationWith({ runtime: { available: false } }));
    expect(result.current.frozen).toBe(true);
  });

  it("ignores blank sends", async () => {
    const { result } = renderChat(conversationWith());
    await act(async () => result.current.send("   "));
    expect(sendHarnessConversationMessage).not.toHaveBeenCalled();
  });

  it("keeps a stable turns reference across a re-render with unchanged cache", () => {
    const { result, rerender } = renderChat(
      conversationWith({
        messages: [{ message_id: "a1", role: "assistant", kind: "message", state: "completed", content: "hi", created_at: "2026-09-22T10:00:00Z", sequence: 1 }],
      }),
    );
    const first = result.current.turns;
    rerender();
    // Memoisation must survive a parent re-render, or BuilderConsole autoscroll
    // yanks the reader to the bottom on every unrelated render.
    expect(result.current.turns).toBe(first);
  });

  it("stop() sends a non-blank interrupt", async () => {
    sendHarnessConversationMessage.mockResolvedValue(conversationWith());
    const { result } = renderChat(conversationWith({ state: "responding" }));
    await act(async () => result.current.stop());
    const payload = sendHarnessConversationMessage.mock.calls[0][1];
    expect(payload.kind).toBe("interrupt");
    expect(payload.content.length).toBeGreaterThan(0);
  });
});

describe("useWorkspaceChat nudges", () => {
  const VERSIONED = { ...ENV, versioning: { enabled: true, nudges_enabled: true } };
  const card = (nudgeId, over = {}) => ({
    message_id: `m-${nudgeId}`,
    role: "system",
    kind: "status",
    state: "completed",
    content: "Agent v3 changed: prompt. Rebuild the environment?",
    payload: { nudge_id: nudgeId, nudge_target: "environment", trigger_label: "v3" },
    created_at: "2026-09-22T10:00:00Z",
    sequence: 1,
    ...over,
  });
  const nudge = (id, over = {}) => ({
    id,
    target: "environment",
    state: "pending",
    facts: { prompt_changed: true },
    trigger_agent_version: { id: "a3", label: "v3" },
    ...over,
  });
  const nudgeCalls = (spy) =>
    spy.mock.calls.filter(([f]) => f.queryKey.join() === nudgesKey("job-1").join());
  const jobCalls = (spy) =>
    spy.mock.calls.filter(([f]) => f.queryKey.join() === "harness-job,job-1");

  // Serves the nudges and the drafts by path.
  const serveLists = (list, drafts = {}) =>
    axios.get.mockImplementation((url) =>
      Promise.resolve({ data: url.endsWith("/drafts/") ? drafts : list }),
    );
  const renderNudges = (
    messages,
    list,
    env = VERSIONED,
    drafts = {},
    over = {},
  ) => {
    const conversation = conversationWith({ messages, ...over });
    getHarnessJob.mockResolvedValue(jobWith(conversation));
    serveLists(list, drafts);
    const { client, Wrapper } = wrapper();
    client.setQueryData(["harness-job", "job-1"], jobWith(conversation));
    const spy = vi.spyOn(client, "invalidateQueries");
    const view = renderHook(
      (props) => useWorkspaceChat(props.env, { source: "harness" }),
      { wrapper: Wrapper, initialProps: { env } },
    );
    return { ...view, client, spy };
  };

  beforeEach(() => {
    getHarnessJob.mockReset();
    sendHarnessConversationMessage.mockReset();
    axios.get.mockReset();
    axios.post.mockReset();
  });

  it("attaches the nudge and Skip to its card and answers no", async () => {
    axios.post.mockResolvedValue({ data: nudge("n1", { state: "answered_no" }) });
    const { result } = renderNudges([card("n1")], [nudge("n1")]);
    await waitFor(() => expect(result.current.turns[0].nudge).toBeDefined());
    const turn = result.current.turns[0];
    expect(turn).toMatchObject({ role: "system", nudgeId: "n1", nudgesEnabled: true });
    expect(turn.nudge.facts.promptChanged).toBe(true);
    expect(result.current.nudgePending).toBe(true);
    await act(async () => turn.onSkipNudge());
    expect(axios.post).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/job-1/nudges/n1/answer/",
      { answer: "no" },
    );
    await waitFor(() => expect(result.current.turns[0].skipped).toBe(true));
    // Once its line took focus, a remounted card doesn't take it again.
    act(() => result.current.turns[0].onSkippedFocused());
    await waitFor(() => expect(result.current.turns[0].skipped).toBe(false));
  });

  it("asks for the list again once for a card whose nudge isn't listed", async () => {
    const { client, result, spy } = renderNudges([card("n9")], [nudge("n1")]);
    await waitFor(() => expect(nudgeCalls(spy)).toHaveLength(1));
    // The conversation moves on and the card is still unlisted: not asked again.
    const reply = { message_id: "a1", role: "assistant", kind: "message", state: "completed", content: "hi", created_at: "2026-09-22T10:00:02Z", sequence: 2 };
    act(() => client.setQueryData(["harness-job", "job-1"], jobWith(conversationWith({ messages: [card("n9"), reply] }))));
    await waitFor(() => expect(result.current.turns).toHaveLength(2));
    expect(nudgeCalls(spy)).toHaveLength(1);
    expect(result.current.turns.find((t) => t.nudgeId === "n9").nudge).toBeUndefined();
  });

  it("asks for the conversation again once for a nudge without its card", async () => {
    const { client, spy, result } = renderNudges([], [nudge("n1")]);
    await waitFor(() => expect(jobCalls(spy)).toHaveLength(1));
    // The conversation moves on, still without the card: not asked again.
    const reply = { message_id: "a1", role: "assistant", kind: "message", state: "completed", content: "hi", created_at: "2026-09-22T10:00:02Z", sequence: 2 };
    act(() => client.setQueryData(["harness-job", "job-1"], jobWith(conversationWith({ messages: [reply] }))));
    await waitFor(() => expect(result.current.turns).toHaveLength(1));
    expect(jobCalls(spy)).toHaveLength(1);
  });

  it("asks for the conversation and what follows the defaults once when a nudge's state moves", async () => {
    const { client, spy } = renderNudges([card("n1")], [nudge("n1")]);
    await waitFor(() =>
      expect(client.getQueryData(nudgesKey("job-1"))).toBeDefined(),
    );
    const calls = (...key) =>
      spy.mock.calls.filter(([f]) => f.queryKey.join() === key.join());
    // The versions key is the prefix of the drafts and run-plan keys.
    const followers = () => [
      calls("harness-environment", "job-1"),
      calls("harness-env-versions", "job-1"),
      calls("harness-scenarios", "job-1"),
    ];
    expect(jobCalls(spy)).toHaveLength(0);
    serveLists([nudge("n1", { state: "applied" })]);
    act(() =>
      client.setQueryData(nudgesKey("job-1"), [
        nudge("n1", { state: "applied" }),
      ]),
    );
    await waitFor(() => expect(jobCalls(spy)).toHaveLength(1));
    followers().forEach((one) => expect(one).toHaveLength(1));
    act(() =>
      client.setQueryData(nudgesKey("job-1"), [
        nudge("n1", { state: "applied", summary: "x" }),
      ]),
    );
    await waitFor(() =>
      expect(client.getQueryData(nudgesKey("job-1"))[0].summary).toBe("x"),
    );
    expect(jobCalls(spy)).toHaveLength(1);
    followers().forEach((one) => expect(one).toHaveLength(1));
  });

  const stopUrl = "/simulate/api/harness-environments/job-1/nudges/n1/stop/";
  const command = (state) => ({
    message_id: "cmd-1",
    role: "user",
    kind: "message",
    state,
    content: "",
    payload: {
      command_kind: "nudge",
      nudge_id: "n1",
      stage: "environment",
      trigger_label: "v3",
    },
    created_at: "2026-09-22T10:00:01Z",
    sequence: 2,
  });
  const userTurn = {
    message_id: "u1",
    role: "user",
    kind: "message",
    state: "delivered",
    content: "Add a rule",
    created_at: "2026-09-22T10:00:01Z",
    sequence: 2,
  };

  it("rebuilds through the answer endpoint and says the answer is a rebuild", async () => {
    let answer;
    axios.post.mockImplementation(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    const { result } = renderNudges([card("n1")], [nudge("n1")]);
    await waitFor(() => expect(result.current.turns[0].nudge).toBeDefined());
    await act(async () => result.current.turns[0].onRebuild());
    expect(axios.post).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/job-1/nudges/n1/answer/",
      { answer: "yes" },
    );
    await waitFor(() => expect(result.current.turns[0].answering).toBe(true));
    expect(result.current.turns[0].rebuilding).toBe(true);
    serveLists([nudge("n1", { state: "queued", can_stop: true })]);
    await act(async () =>
      answer({ data: nudge("n1", { state: "queued", can_stop: true }) }),
    );
    await waitFor(() =>
      expect(result.current.turns[0].nudge.state).toBe("queued"),
    );
    expect(result.current.rebuildHold).toBe(true);
    expect(result.current.canStop).toBe(true);
  });

  it("stops a rebuild from its card", async () => {
    const stopped = nudge("n1", {
      state: "failed",
      error: { code: "stopped_by_user" },
    });
    axios.post.mockResolvedValue({ data: stopped });
    const { result } = renderNudges(
      [card("n1")],
      [nudge("n1", { state: "running", can_stop: true })],
    );
    await waitFor(() => expect(result.current.turns[0].nudge).toBeDefined());
    serveLists([stopped]);
    await act(async () => result.current.turns[0].onStopNudge());
    expect(axios.post).toHaveBeenCalledWith(stopUrl, {});
    await waitFor(() =>
      expect(result.current.turns[0].nudge.state).toBe("failed"),
    );
    expect(result.current.turns[0].skipped).toBe(true);
    expect(result.current.rebuildHold).toBe(false);
  });

  it("stops a running rebuild from the composer instead of interrupting", async () => {
    axios.post.mockResolvedValue({ data: nudge("n1", { state: "failed" }) });
    const { result } = renderNudges(
      [card("n1"), command("delivered")],
      [nudge("n1", { state: "running" })],
    );
    await waitFor(() => expect(result.current.rebuildHold).toBe(true));
    expect(result.current.turns[1].steps[0]).toMatchObject({
      kind: "nudge-line",
    });
    await act(async () => result.current.stop());
    expect(axios.post).toHaveBeenCalledWith(stopUrl, {});
    expect(sendHarnessConversationMessage).not.toHaveBeenCalled();
  });

  it("stops a queued rebuild whose message was sent, from the composer", async () => {
    axios.post.mockResolvedValue({ data: nudge("n1", { state: "failed" }) });
    const { result } = renderNudges(
      [card("n1"), command("queued")],
      [nudge("n1", { state: "queued", answered_at: "2026-09-22T10:00:00Z" })],
    );
    await waitFor(() => expect(result.current.rebuildHold).toBe(true));
    await act(async () => result.current.stop());
    expect(axios.post).toHaveBeenCalledWith(stopUrl, {});
    expect(sendHarnessConversationMessage).not.toHaveBeenCalled();
  });

  it("interrupts the user's own turn while a rebuild waits behind it", async () => {
    sendHarnessConversationMessage.mockResolvedValue(conversationWith());
    const { result } = renderNudges(
      [card("n1"), userTurn],
      [nudge("n1", { state: "queued" })],
    );
    await waitFor(() => expect(result.current.rebuildHold).toBe(true));
    await act(async () => result.current.stop());
    expect(sendHarnessConversationMessage.mock.calls[0][1].kind).toBe(
      "interrupt",
    );
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("interrupts the user's turn while a rebuild queued again waits behind it", async () => {
    sendHarnessConversationMessage.mockResolvedValue(conversationWith());
    const later = {
      ...userTurn,
      created_at: "2026-09-22T10:00:02Z",
      sequence: 3,
    };
    const { result } = renderNudges(
      [card("n1"), command("failed"), later],
      [nudge("n1", { state: "queued", answered_at: "2026-09-22T10:00:03Z" })],
    );
    await waitFor(() => expect(result.current.rebuildHold).toBe(true));
    expect(result.current.inFlight).toBe(true);
    await act(async () => result.current.stop());
    expect(sendHarnessConversationMessage.mock.calls[0][1].kind).toBe(
      "interrupt",
    );
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("stops a rebuild waiting on an answer, not the paused question turn", async () => {
    axios.post.mockResolvedValue({ data: nudge("n1", { state: "failed" }) });
    const question = {
      message_id: "q1",
      role: "assistant",
      kind: "question",
      state: "completed",
      content: "How strict?",
      created_at: "2026-09-22T10:00:02Z",
      sequence: 3,
    };
    const { result } = renderNudges(
      [card("n1"), userTurn, question],
      [nudge("n1", { state: "queued", queued_behind_input: true })],
      VERSIONED,
      {},
      {
        state: "waiting_for_user",
        blocking_input: { message_id: "q1", kind: "question_requested" },
      },
    );
    await waitFor(() => expect(result.current.rebuildHold).toBe(true));
    // The paused turn's command stays delivered.
    expect(result.current.inFlight).toBe(true);
    expect(result.current.working).toBe(false);
    await act(async () => result.current.stop());
    expect(axios.post).toHaveBeenCalledWith(stopUrl, {});
    expect(sendHarnessConversationMessage).not.toHaveBeenCalled();
  });

  it("interrupts the answered question's turn while a rebuild waits behind it", async () => {
    sendHarnessConversationMessage.mockResolvedValue(conversationWith());
    const question = {
      message_id: "q1",
      role: "assistant",
      kind: "question",
      state: "completed",
      content: "How strict?",
      created_at: "2026-09-22T10:00:02Z",
      sequence: 3,
    };
    const reply = {
      ...userTurn,
      message_id: "u2",
      content: "Strict",
      reply_to: "q1",
      created_at: "2026-09-22T10:00:03Z",
      sequence: 4,
    };
    const { result } = renderNudges(
      [card("n1"), userTurn, question, reply],
      [nudge("n1", { state: "queued", queued_behind_input: true })],
      VERSIONED,
      {},
      {
        state: "responding",
        blocking_input: { message_id: "q1", kind: "question_requested" },
      },
    );
    await waitFor(() => expect(result.current.rebuildHold).toBe(true));
    expect(result.current.working).toBe(true);
    await act(async () => result.current.stop());
    expect(sendHarnessConversationMessage.mock.calls[0][1].kind).toBe(
      "interrupt",
    );
    expect(axios.post).not.toHaveBeenCalledWith(stopUrl, {});
  });

  it("offers Stop for a queued rebuild while the chat is idle", async () => {
    const { result } = renderNudges(
      [card("n1")],
      [nudge("n1", { state: "queued" })],
    );
    await waitFor(() => expect(result.current.rebuildHold).toBe(true));
    expect(result.current.inFlight).toBe(false);
    expect(result.current.canStop).toBe(true);
    // Queued is waiting, not working.
    expect(result.current.working).toBe(false);
  });

  it("works while a rebuild runs", async () => {
    const { result } = renderNudges(
      [card("n1"), command("delivered")],
      [nudge("n1", { state: "running" })],
    );
    await waitFor(() => expect(result.current.rebuildHold).toBe(true));
    expect(result.current.working).toBe(true);
  });

  it("says a Stop is on its way until it lands", async () => {
    let stopped;
    axios.post.mockImplementation(
      () =>
        new Promise((resolve) => {
          stopped = resolve;
        }),
    );
    const { result } = renderNudges(
      [card("n1"), command("delivered")],
      [nudge("n1", { state: "running" })],
    );
    await waitFor(() => expect(result.current.rebuildHold).toBe(true));
    expect(result.current.stopping).toBe(false);
    await act(async () => result.current.stop());
    await waitFor(() => expect(result.current.stopping).toBe(true));
    await act(async () => stopped({ data: nudge("n1", { state: "failed" }) }));
    await waitFor(() => expect(result.current.stopping).toBe(false));
  });

  it("gives the card its piece's open draft", async () => {
    const { result } = renderNudges([card("n1")], [nudge("n1")], VERSIONED, {
      environment: {
        id: "d1",
        stale: false,
        changes: { tools: 2 },
        last_edited_by: { name: "Asha" },
      },
      suite: { id: "d2", stale: false, changes: {} },
      set_aside: [],
    });
    await waitFor(() => expect(result.current.turns[0].draft).toBeTruthy());
    expect(result.current.turns[0].draft).toMatchObject({
      id: "d1",
      lastEditedBy: "Asha",
    });
  });

  it("gives the card no draft when its piece has none", async () => {
    const { result } = renderNudges([card("n1")], [nudge("n1")], VERSIONED, {
      environment: null,
      suite: { id: "d2", stale: false, changes: {} },
      set_aside: [],
    });
    await waitFor(() => expect(result.current.turns[0].nudge).toBeDefined());
    expect(result.current.turns[0].draft).toBeNull();
  });

  it("keeps the last list through a failed poll and drops it once versioning is off", async () => {
    const { client, result } = renderNudges([card("n1")], [nudge("n1")]);
    client.setQueryDefaults(nudgesKey("job-1"), { retryDelay: 0 });
    await waitFor(() => expect(result.current.turns[0].nudge).toBeDefined());
    const failedPoll = () =>
      act(() => client.refetchQueries({ queryKey: nudgesKey("job-1") }));

    axios.get.mockRejectedValue({ statusCode: 500 });
    await failedPoll();
    await waitFor(() =>
      expect(client.getQueryState(nudgesKey("job-1")).status).toBe("error"),
    );
    expect(result.current.turns[0].nudge.id).toBe("n1");
    expect(result.current.nudgePending).toBe(true);

    axios.get.mockRejectedValue({
      statusCode: 404,
      error: "versioning_disabled",
    });
    await failedPoll();
    await waitFor(() => expect(result.current.turns[0].nudge).toBeUndefined());
    expect(result.current.nudgePending).toBe(false);
  });

  it("drops the last list once the environment turns versioning off", async () => {
    const { result, rerender } = renderNudges([card("n1")], [nudge("n1")]);
    await waitFor(() => expect(result.current.turns[0].nudge).toBeDefined());

    rerender({ env: { ...VERSIONED, versioning: { enabled: false } } });

    expect(result.current.turns[0].nudge).toBeUndefined();
    expect(result.current.nudgePending).toBe(false);
  });

  it("isn't pending once the nudge is answered, and asks nothing while versioning is off", async () => {
    const answered = renderNudges([card("n1")], [nudge("n1", { state: "answered_no" })]);
    await waitFor(() => expect(answered.result.current.turns[0].nudge).toBeDefined());
    expect(answered.result.current.nudgePending).toBe(false);

    axios.get.mockClear();
    const off = renderNudges([card("n1")], [nudge("n1")], ENV);
    expect(axios.get).not.toHaveBeenCalled();
    expect(off.result.current.turns[0].nudge).toBeUndefined();
    expect(off.result.current.nudgePending).toBe(false);
  });
});
