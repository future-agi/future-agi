import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { useWorkspaceChat } from "../workspaceChat";
import { clearScenarioSelection, publishScenarioSelection } from "src/sections/simulate/environments/buildEnvironment/console/scenarioSelectionBus";

vi.mock("src/api/harness/harness", () => ({
  getHarnessJob: vi.fn(),
  sendHarnessConversationMessage: vi.fn(),
  harnessIdempotencyKey: () => "req-test",
}));

import {
  getHarnessJob,
  sendHarnessConversationMessage,
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

  it("carries selected scenario ids and clears the selection on send", async () => {
    sendHarnessConversationMessage.mockResolvedValue(conversationWith());
    publishScenarioSelection({ ids: ["s1", "s2"], rows: [{ name: "a" }, { name: "b" }] });
    const { result } = renderChat(conversationWith());

    await act(async () => {
      result.current.send("make them impatient");
    });

    expect(sendHarnessConversationMessage.mock.calls[0][1].payload.scenario_ids).toEqual(["s1", "s2"]);
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
