import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useWorkspaceChat } from "../workspaceChat";
import { listScenarios } from "../scenarios";
import {
  getHarnessJob,
  harnessIdempotencyKey,
  sendHarnessConversationMessage,
} from "src/api/harness/harness";
import {
  clearScenarioSelection,
  getScenarioSelection,
  publishScenarioSelection,
} from "src/sections/simulate/environments/buildEnvironment/console/scenarioSelectionBus";

vi.mock("src/api/harness/harness", () => ({
  getHarnessJob: vi.fn(),
  sendHarnessConversationMessage: vi.fn(),
  harnessIdempotencyKey: vi.fn(),
}));
vi.mock("../scenarios", () => ({ listScenarios: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

const conversation = {
  conversation_id: "conversation-1",
  state: "warm_idle",
  runtime: { available: true },
  messages: [],
  events: [],
};
const job = { job: { job_id: "job-1" }, status: { stage: "completed" }, conversation };
const text = "Change the selected scenarios to use Spanish";
const allMatching = () => ({
  ids: [],
  rows: [],
  count: 40,
  all: true,
  jobId: "job-1",
  predicate: {
    search: "refund",
    filters: { "persona.languages": ["English"], status_not: ["draft"] },
    marked: ["excluded"],
  },
});
const errors = (result) => result.current.turns
  .flatMap((turn) => turn.steps || [])
  .filter((step) => step.kind === "error");

function renderChat() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(["harness-job", "job-1"], job);
  // eslint-disable-next-line react/prop-types
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useWorkspaceChat({ id: "job-1" }, { source: "harness" }), {
    wrapper: Wrapper,
  });
}

beforeEach(() => {
  clearScenarioSelection();
  listScenarios.mockReset();
  getHarnessJob.mockReset().mockResolvedValue(job);
  sendHarnessConversationMessage.mockReset().mockResolvedValue(conversation);
  harnessIdempotencyKey.mockReset().mockReturnValue("request-1");
});
afterEach(() => clearScenarioSelection());

describe("W10-SIMULATE-OTHER-L03 — builder selection scope", () => {
  it("resolves all filtered pages minus exclusions and retries the exact resolved command", async () => {
    const firstPage = Array.from({ length: 25 }, (_, i) => ({ id: `s-${i}` }));
    const secondPage = Array.from({ length: 15 }, (_, i) => ({ id: `s-${i + 25}` }));
    const expectedIds = [...firstPage, ...secondPage].map((row) => row.id);
    listScenarios
      .mockResolvedValueOnce({ results: [...firstPage, { id: "excluded" }], total_pages: 2 })
      .mockResolvedValueOnce({ results: secondPage, total_pages: 2 });
    sendHarnessConversationMessage
      .mockRejectedValueOnce({ message: "Try again", retryable: true })
      .mockResolvedValueOnce(conversation);
    publishScenarioSelection(allMatching());
    const { result } = renderChat();

    await act(async () => result.current.send(text));

    expect(listScenarios).toHaveBeenCalledTimes(2);
    [1, 2].forEach((page) => expect(listScenarios).toHaveBeenNthCalledWith(page, "job-1", {
      page,
      limit: 100,
      search: "refund",
      group_by: "",
      "persona.languages": ["English"],
      status_not: ["draft"],
    }));
    await waitFor(() => expect(errors(result)[0]?.onRetry).toBeInstanceOf(Function));
    const firstCommand = sendHarnessConversationMessage.mock.calls[0][1];
    expect(firstCommand.payload.scenario_ids).toEqual(expectedIds);
    expect(getScenarioSelection().count).toBe(0);

    // Changes after dispatch must not widen a retry or trigger another resolve.
    publishScenarioSelection({ ids: ["different-selection"] });
    harnessIdempotencyKey.mockReturnValue("request-2");
    await act(async () => errors(result)[0].onRetry());
    await waitFor(() => expect(sendHarnessConversationMessage).toHaveBeenCalledTimes(2));
    expect(sendHarnessConversationMessage.mock.calls[1][1]).toEqual(firstCommand);
    expect(firstCommand.client_request_id).toBe("request-1");
    expect(listScenarios).toHaveBeenCalledTimes(2);
  });

  it.each(["request failure", "empty match"])("does not send an unscoped edit on %s", async (failure) => {
    if (failure === "request failure") listScenarios.mockRejectedValue(new Error("offline"));
    else listScenarios.mockResolvedValue({ results: [], total_pages: 1 });
    publishScenarioSelection(allMatching());
    const selected = getScenarioSelection();
    const { result } = renderChat();

    await act(async () => result.current.send(text));

    expect(sendHarnessConversationMessage).not.toHaveBeenCalled();
    expect(errors(result)[0]?.text).toMatch(/selected scenarios/i);
    expect(errors(result)[0]?.onRetry).toBeUndefined();
    expect(getScenarioSelection()).toBe(selected);
    expect(result.current.running).toBe(false);
    expect(result.current.turns.some((turn) => turn.text === text)).toBe(true);
  });

  it("refuses a partial match when a later page fails", async () => {
    listScenarios
      .mockResolvedValueOnce({ results: [{ id: "s-1" }], total_pages: 2 })
      .mockRejectedValueOnce(new Error("page 2 unavailable"));
    publishScenarioSelection(allMatching());
    const { result } = renderChat();

    await act(async () => result.current.send(text));

    expect(sendHarnessConversationMessage).not.toHaveBeenCalled();
    expect(errors(result)[0]?.text).toMatch(/selected scenarios/i);
    expect(getScenarioSelection().all).toBe(true);
  });

  it.each([
    ["missing predicate", { ...allMatching(), predicate: undefined }],
    ["different environment", { ...allMatching(), jobId: "job-other" }],
  ])("refuses all-matching with %s", async (_label, selection) => {
    publishScenarioSelection(selection);
    const { result } = renderChat();

    await act(async () => result.current.send(text));

    expect(listScenarios).not.toHaveBeenCalled();
    expect(sendHarnessConversationMessage).not.toHaveBeenCalled();
    expect(errors(result)[0]?.text).toMatch(/selected scenarios/i);
  });

  it("stays busy while resolving and does not clear a newer selection", async () => {
    let finishPage;
    listScenarios.mockReturnValue(new Promise((resolve) => { finishPage = resolve; }));
    publishScenarioSelection(allMatching());
    const { result } = renderChat();
    let sending;

    act(() => { sending = result.current.send(text); });

    expect(result.current.running).toBe(true);
    expect(sendHarnessConversationMessage).not.toHaveBeenCalled();
    publishScenarioSelection({ ids: ["new-selection"] });
    await act(async () => {
      finishPage({ results: [{ id: "s-1" }], total_pages: 1 });
      await sending;
    });

    expect(sendHarnessConversationMessage.mock.calls[0][1].payload.scenario_ids).toEqual(["s-1"]);
    expect(getScenarioSelection().ids).toEqual(["new-selection"]);
  });

  it("snapshots the predicate on the bus instead of sharing mutable filter arrays", () => {
    const selection = allMatching();
    publishScenarioSelection(selection);
    selection.predicate.filters["persona.languages"].push("French");
    selection.predicate.marked.push("new-exclusion");
    expect(getScenarioSelection().predicate).toEqual(allMatching().predicate);
  });

  it.each([
    ["explicit rows", { ids: ["s-1", "s-2"] }, ["s-1", "s-2"]],
    ["no selection", { ids: [] }, undefined],
  ])("preserves %s without resolving pages", async (_label, selection, ids) => {
    publishScenarioSelection(selection);
    const { result } = renderChat();
    await act(async () => result.current.send(text));
    expect(listScenarios).not.toHaveBeenCalled();
    expect(sendHarnessConversationMessage.mock.calls[0][1].payload.scenario_ids).toEqual(ids);
  });
});
