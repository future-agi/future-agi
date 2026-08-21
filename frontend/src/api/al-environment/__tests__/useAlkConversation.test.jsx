import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAlkConversation } from "../useAlkConversation";
import { streamHarness } from "../streamHarness";

vi.mock("../streamHarness", () => ({ streamHarness: vi.fn() }));

let queryClient;
const wrapper = ({ children }) => (
  <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
);

/** Drive the mocked transport, handing the caller's onEvent a scripted stream. */
const streamsBack = (events) =>
  streamHarness.mockImplementation(async ({ onEvent }) => {
    events.forEach(onEvent);
    return { lastStatus: null };
  });

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => {
  vi.clearAllMocks();
});

describe("useAlkConversation", () => {
  it("shows what you said straight away, before the harness answers", async () => {
    streamsBack([]);
    const { result } = renderHook(() => useAlkConversation(), { wrapper });
    await act(async () => {
      await result.current.say("build the world");
    });
    expect(result.current.live[0]).toMatchObject({ role: "you", text: "build the world" });
  });

  it("appends prose and tool activity in the order it arrives", async () => {
    streamsBack([
      { kind: "text", text: "reading the agent" },
      { kind: "tool", tool: "save_world", detail: { tables: 2 } },
      { kind: "result", tool: "save_world", text: "ok" },
      { kind: "done" },
    ]);
    const { result } = renderHook(() => useAlkConversation(), { wrapper });
    await act(async () => {
      await result.current.say("go");
    });
    const rendered = result.current.live.slice(1);
    expect(rendered.map((m) => m.tool || m.role)).toEqual(["tester", "save_world", "save_world"]);
  });

  it("joins streamed prose into one message instead of one per chunk", async () => {
    streamsBack([
      { kind: "text", text: "Read it. " },
      { kind: "text", text: "Four tools, " },
      { kind: "text", text: "all on DriveThruTools." },
    ]);
    const { result } = renderHook(() => useAlkConversation(), { wrapper });
    await act(async () => {
      await result.current.say("go");
    });
    const prose = result.current.live.slice(1);
    expect(prose).toHaveLength(1);
    expect(prose[0].text).toBe("Read it. Four tools, all on DriveThruTools.");
  });

  it("starts a fresh paragraph after a tool call", async () => {
    streamsBack([
      { kind: "text", text: "first" },
      { kind: "tool", tool: "save_world", detail: {} },
      { kind: "text", text: "second" },
    ]);
    const { result } = renderHook(() => useAlkConversation(), { wrapper });
    await act(async () => {
      await result.current.say("go");
    });
    const said = result.current.live.slice(1).map((m) => m.text || m.tool);
    expect(said).toEqual(["first", "save_world", "second"]);
  });

  it("leaves control events out of the transcript", async () => {
    streamsBack([
      { kind: "status", detail: { busy: false } },
      { kind: "done" },
      { kind: "text", text: "visible" },
    ]);
    const { result } = renderHook(() => useAlkConversation(), { wrapper });
    await act(async () => {
      await result.current.say("go");
    });
    expect(result.current.live.slice(1)).toHaveLength(1);
  });

  it("labels a run's exchanges with who spoke", async () => {
    streamsBack([
      { kind: "exchange", text: "a latte please", detail: { speaker: "customer" } },
      { kind: "result_card", text: "passed 3/3", detail: { scenario: "plain_order", passed: true } },
    ]);
    const { result } = renderHook(() => useAlkConversation(), { wrapper });
    await act(async () => {
      await result.current.runScenarios("");
    });
    expect(result.current.live.map((m) => m.role)).toEqual(["customer", "verdict"]);
  });

  it("reports a refusal instead of throwing at the caller", async () => {
    streamHarness.mockRejectedValue(new Error("still working on the build stage — one moment"));
    const { result } = renderHook(() => useAlkConversation(), { wrapper });
    await act(async () => {
      await result.current.say("go");
    });
    await waitFor(() => expect(result.current.error).toMatch(/still working on the build stage/));
    expect(result.current.streaming).toBe(false);
  });

  it("says nothing when the turn was stopped on purpose", async () => {
    const aborted = new Error("BodyStreamBuffer was aborted");
    aborted.name = "AbortError";
    streamHarness.mockRejectedValue(aborted);
    const { result } = renderHook(() => useAlkConversation(), { wrapper });
    await act(async () => {
      await result.current.say("go");
    });
    expect(result.current.error).toBe("");
    expect(result.current.live.filter((m) => m.role === "error")).toHaveLength(0);
  });

  it("puts a real failure in the thread as well as on the hook", async () => {
    streamHarness.mockRejectedValue(new Error("still working on the build stage"));
    const { result } = renderHook(() => useAlkConversation(), { wrapper });
    await act(async () => {
      await result.current.say("go");
    });
    expect(result.current.live.at(-1)).toMatchObject({ role: "error" });
  });

  it("reports a stage that failed, which the transport cannot tell you about", async () => {
    streamsBack([
      { kind: "text", text: "reading" },
      { kind: "done", detail: { outcome: "failed", error: "the model refused to continue" } },
    ]);
    const { result } = renderHook(() => useAlkConversation(), { wrapper });
    await act(async () => {
      await result.current.say("go");
    });
    expect(result.current.live.at(-1)).toMatchObject({
      role: "error",
      text: "the model refused to continue",
    });
  });

  it("says nothing extra when a stage ends normally", async () => {
    streamsBack([{ kind: "done", detail: { outcome: "ok", turns: 3 } }]);
    const { result } = renderHook(() => useAlkConversation(), { wrapper });
    await act(async () => {
      await result.current.say("go");
    });
    expect(result.current.live.filter((m) => m.role === "error")).toHaveLength(0);
  });

  it("refreshes the tabs as each artifact lands, not just at the end", async () => {
    streamsBack([{ kind: "artifact", text: "contract.json", detail: { path: "contract.json" } }]);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useAlkConversation(), { wrapper });
    await act(async () => {
      await result.current.say("go");
    });
    const contractRefreshes = invalidate.mock.calls.filter(
      (c) => c[0].queryKey.join(".") === "alk.contract"
    );
    // Once for the artifact, once for the completed turn.
    expect(contractRefreshes.length).toBeGreaterThanOrEqual(2);
  });

  it("resyncs the cached status and tabs once a stream finishes", async () => {
    streamsBack([{ kind: "done" }]);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useAlkConversation(), { wrapper });
    await act(async () => {
      await result.current.say("go");
    });
    const keys = invalidate.mock.calls.map((c) => c[0].queryKey.join("."));
    expect(keys).toContain("alk.status");
    expect(keys).toContain("alk.scenarios");
    // A turn can build a world, which is what puts a session in the environments list.
    expect(keys).toContain("alk.environments");
  });
});
