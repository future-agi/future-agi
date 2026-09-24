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
