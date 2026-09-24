import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock only the axios default instance; keep the real `endpoints` so the URL
// assertions below are genuine, not a tautology against our own mock.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn() } };
});

const axiosMod = await import("src/utils/axios");
const axios = axiosMod.default;
const { endpoints } = axiosMod;
const { mapExecutions } = await import("../runs");
const { buildRunIdentity, buildRunStats, useRunDetail, mapCallDetail, useCallDetail } =
  await import("../runDetail");
const { RUN_COLORS } = await import(
  "src/sections/simulate/environments/workspace/runs/runs.constants"
);

// One completed, one failed row, out of order so the ordinal reflects the sort.
const executionsPayload = () => ({
  results: [
    {
      id: "ex-old",
      status: "Failed",
      start_time: "2026-01-12T11:05:00.000Z",
      agent_version: "v1",
      total_chats: 10,
      success_rate: 60,
    },
    {
      id: "ex-new",
      status: "Completed",
      start_time: "2026-01-14T09:12:00.000Z",
      agent_version: "v2",
      total_chats: 12,
      success_rate: 100,
    },
  ],
  count: 2,
});

// A voice kpis payload — real run-level counts, a duration in seconds, one
// system metric (avg_score) and one eval metric (0–100 scale).
const kpisPayload = () => ({
  agent_type: "VOICE",
  total_calls: 12,
  failed_calls: 3,
  total_duration: 600,
  avg_score: 82,
  avg_csat_score: 4.2,
  avg_turn_count: 7,
  calls_connected_percentage: 90,
  task_completion: 75,
});

const perfPayload = () => ({
  test_run_performance_metrics: {
    pass_rate: 74,
    total_test_runs: 12,
    latest_fail_rate: 26,
  },
  top_performing_scenarios: [],
});

describe("buildRunIdentity", () => {
  it("stamps ordinal-keyed letter + colour from the mapped row", () => {
    const rows = mapExecutions(executionsPayload());
    const newest = rows.find((r) => r.executionId === "ex-new");

    const identity = buildRunIdentity(newest, "Refund Copilot");
    expect(identity.executionId).toBe("ex-new");
    expect(identity.ordinal).toBe(2);
    expect(identity.letter).toBe("2");
    expect(identity.color).toBe(RUN_COLORS[1]);
    expect(identity.name).toBe("Refund Copilot");
    expect(identity.agentVersion).toBe("v2");
    expect(identity.status).toBe("passed");
    // The row carries no end time — finishedAt is a documented gap.
    expect(identity.finishedAt).toBeNull();
  });

  it("returns null for a missing row", () => {
    expect(buildRunIdentity(null)).toBeNull();
  });
});

describe("buildRunStats", () => {
  it("derives counts from kpis and the pass rate from the performance summary", () => {
    const rows = mapExecutions(executionsPayload());
    const row = rows.find((r) => r.executionId === "ex-new");

    const stats = buildRunStats(kpisPayload(), perfPayload(), row);
    expect(stats.total).toBe(12);
    expect(stats.failed).toBe(3);
    expect(stats.passed).toBe(9);
    // Pass rate comes from the performance summary, not the derived 75%.
    expect(stats.passRate).toBe(74);
    expect(stats.durationS).toBe(600);
    expect(stats.avgDurationMs).toBe(50000);
    expect(stats.avgScore).toBe(82);
    expect(stats.csat).toBe(4.2);
    expect(stats.avgTurnCount).toBe(7);
    expect(stats.connectedPct).toBe(90);
    // The eval metric surfaces as a per-eval column score (0–100).
    expect(stats.scores.task_completion).toBe(75);
    // Documented gaps.
    expect(stats.tokens).toBeNull();
    expect(stats.cost).toBeNull();
    expect(stats.failedCritical).toBe(0);
  });

  it("falls back to the executions row counts and derives the pass rate when kpis/perf are absent", () => {
    const rows = mapExecutions(executionsPayload());
    const row = rows.find((r) => r.executionId === "ex-old");

    const stats = buildRunStats(null, null, row);
    // Row: total 10, success_rate 60 → passed 6, failed 4.
    expect(stats.total).toBe(10);
    expect(stats.passed).toBe(6);
    expect(stats.failed).toBe(4);
    expect(stats.passRate).toBe(60);
    expect(stats.durationS).toBeNull();
    expect(stats.avgDurationMs).toBeNull();
  });

  // `completed` (status = completed) is a different number from `total`
  // (every status). It must never fall back to `total` — a caller that needs
  // "the run's completed calls" and gets the wrong number is worse than one
  // told the number isn't known yet. The KPI body carries `completed_calls`
  // for both modalities, not the chat-only `connected_calls` borrow.
  it("reads `completed` from kpis.completed_calls on a chat run", () => {
    const stats = buildRunStats(
      { agent_type: "text", completed_calls: 12, connected_calls: 12, total_calls: 16 },
      null,
      null,
    );
    expect(stats.completed).toBe(12);
    expect(stats.total).toBe(16);
  });

  // Voice's `connected_calls` is `connected_voice_calls`
  // (`duration_seconds > 0`), a different filter and a different number —
  // reading it would be wrong. The fixture makes the two differ on purpose,
  // so borrowing it again fails this case.
  it("reads `completed` from kpis.completed_calls on a voice run too, never from connected_calls", () => {
    const stats = buildRunStats(
      { agent_type: "voice", completed_calls: 12, connected_calls: 9, total_calls: 16 },
      null,
      null,
    );
    expect(stats.completed).toBe(12);
    expect(stats.total).toBe(16);
  });

  it("leaves `completed` null (never 0, never `total`) while kpis hasn't loaded yet", () => {
    const rows = mapExecutions(executionsPayload());
    const row = rows.find((r) => r.executionId === "ex-old");
    const stats = buildRunStats(null, null, row);
    expect(stats.completed).toBeNull();
    expect(stats.completed).not.toBe(stats.total);
  });

  // A KPI body that has loaded may still have no `completed_calls` (an older
  // backend). That must read as "not known yet", not a zero and not `total`.
  it("leaves `completed` null when a loaded kpis payload carries no completed_calls", () => {
    const stats = buildRunStats(
      { agent_type: "voice", connected_calls: 9, total_calls: 16 },
      null,
      null,
    );
    expect(stats.completed).toBeNull();
    expect(stats.completed).not.toBe(stats.total);
  });

  // `completed_calls` is a call count, not a verdict — `DETAILS_KEYS`
  // (common.js) must file it as a run detail, never an eval metric, or every
  // run grows a fake "Completed calls" eval.
  it("never files completed_calls as an eval score (it is a call count, not a verdict)", () => {
    const stats = buildRunStats(
      { agent_type: "text", completed_calls: 12, total_calls: 16, task_success: 49 },
      null,
      null,
    );
    expect(stats.completed).toBe(12);
    expect(stats.scores).toEqual({ task_success: 49 });
    expect(stats.scores).not.toHaveProperty("completed_calls");
  });

  it("never files completed_calls as an eval score on a voice run either", () => {
    const stats = buildRunStats(
      { agent_type: "voice", completed_calls: 12, total_calls: 16, task_success: 49 },
      null,
      null,
    );
    expect(stats.completed).toBe(12);
    expect(stats.scores).toEqual({ task_success: 49 });
    expect(stats.scores).not.toHaveProperty("completed_calls");
  });
});

// A real-shaped voice call-detail payload: a two-turn transcript, conversation
// metrics, a recording, and three evals (pass / fail-score / pending).
const callDetailPayload = () => ({
  id: "call-1",
  simulation_call_type: "voice",
  call_type: "Inbound",
  phone_number: "+15551234567",
  provider: "vapi",
  duration: 42.5,
  transcript: [
    { speaker_role: "assistant", content: "Hi, how can I help you today?", start_time_seconds: 0 },
    { speaker_role: "user", content: "I want a refund please", start_time_seconds: 3.2 },
  ],
  turn_count: 4,
  avg_agent_latency: 320,
  agent_talk_percentage: 60,
  total_tokens: null,
  cost_cents: 150,
  call_summary: "Customer asked for a refund.",
  recordings: { combined: "https://cdn.example.com/rec.mp3" },
  eval_metrics: {
    "eval-1": { id: "eval-1", name: "Refund correctness", value: "Passed", type: "Pass/Fail", reason: "policy match", status: "completed" },
    "eval-2": { id: "eval-2", name: "Tone", value: 0.4, type: "score", reason: "curt", status: "completed" },
    "eval-3": {},
  },
});

describe("mapCallDetail", () => {
  it("maps a real voice call-detail payload to the CallDetail view-model", () => {
    const d = mapCallDetail(callDetailPayload());
    expect(d.id).toBe("call-1");
    expect(d.type).toBe("voice");
    expect(d.direction).toBe("Inbound");
    expect(d.phone).toBe("+15551234567");
    expect(d.provider).toBe("vapi");
    expect(d.durationS).toBe(42.5);

    // Transcript: roles normalised (assistant→agent, user→customer), timestamps kept.
    expect(d.turns).toHaveLength(2);
    expect(d.turns[0]).toMatchObject({ role: "agent", text: "Hi, how can I help you today?", at: 0 });
    expect(d.turns[1]).toMatchObject({ role: "customer", at: 3.2 });

    // Stats from the real conversation metrics; words derived from the transcript.
    expect(d.stats.turnCount).toBe(4);
    expect(d.stats.latencyMs).toBe(320);
    expect(d.stats.aiPct).toBe(60);
    expect(d.stats.userPct).toBe(40);
    expect(d.stats.words).toBe(12);
    // No backend field for either — documented gaps.
    expect(d.stats.silenceS).toBeNull();
    expect(d.stats.ttfwMs).toBeNull();

    expect(d.tokens).toBeNull();
    expect(d.cost).toBe(1.5);
    expect(d.summary).toBe("Customer asked for a refund.");
    expect(d.recordings.combined).toBe("https://cdn.example.com/rec.mp3");

    // Evals: pass/fail verdict + thresholded score; the pending `{}` is dropped.
    expect(d.evalResults).toHaveLength(2);
    const e1 = d.evalResults.find((e) => e.id === "eval-1");
    const e2 = d.evalResults.find((e) => e.id === "eval-2");
    expect(e1).toMatchObject({ score: 1, passed: true, reason: "policy match" });
    expect(e2).toMatchObject({ score: 0.4, passed: false });
  });

  it("routes a text sim to the chat type and reads chat-message roles + tool calls", () => {
    const d = mapCallDetail({
      id: "chat-1",
      simulation_call_type: "text",
      transcript: [
        { role: "assistant", content: "Refund issued.", tool_calls: [{ function: { name: "issue_refund" } }] },
        { role: "user", content: "thanks" },
      ],
      recordings: {},
      eval_metrics: {},
    });
    expect(d.type).toBe("chat");
    expect(d.turns[0]).toMatchObject({ role: "agent", text: "Refund issued." });
    expect(d.turns[0].toolCalls).toHaveLength(1);
    expect(d.stats.toolCalls).toBe(1);
    expect(d.recordings).toEqual({});
  });

  it("returns null for a missing payload", () => {
    expect(mapCallDetail(null)).toBeNull();
  });

  it("keeps a removed eval's verdict and carries its marker", () => {
    const d = mapCallDetail({
      id: "call-2",
      simulation_call_type: "text",
      transcript: [],
      recordings: {},
      eval_metrics: {
        "cfg-live": { name: "Tone", value: "Passed", type: "Pass/Fail", reason: "fine" },
        "cfg-gone": {
          name: "no_misselling",
          value: "Failed",
          type: "Pass/Fail",
          reason: "oversold",
          removed: true,
        },
      },
    });

    expect(d.evalResults).toHaveLength(2);
    expect(d.evalResults.find((e) => e.id === "cfg-gone")).toMatchObject({
      name: "no_misselling",
      passed: false,
      removed: true,
    });
    // A live eval's verdict carries no marker.
    expect(d.evalResults.find((e) => e.id === "cfg-live").removed).toBe(false);
  });

  it("drops a removed eval's stored row when it carries no value, but keeps one that does", () => {
    // "removed" marks a VERDICT — a pending, skipped or errored row is a
    // stored row, not a verdict, and is never rendered, live or removed.
    // `norm.kind === "empty"` already enforces this; this test pins it.
    const d = mapCallDetail({
      id: "call-3",
      simulation_call_type: "text",
      transcript: [],
      recordings: {},
      eval_metrics: {
        // Removed AND pending — serialises as `{}` plus the marker. Not a
        // verdict, so it must not appear at all.
        "cfg-gone-empty": { removed: true },
        // Removed AND holds a value — a real verdict: still shown, still
        // marked.
        "cfg-gone-valued": {
          name: "no_misselling",
          value: "Failed",
          type: "Pass/Fail",
          reason: "oversold",
          removed: true,
        },
      },
    });

    expect(d.evalResults).toHaveLength(1);
    expect(d.evalResults.find((e) => e.id === "cfg-gone-empty")).toBeUndefined();
    expect(d.evalResults.find((e) => e.id === "cfg-gone-valued")).toMatchObject({
      name: "no_misselling",
      passed: false,
      removed: true,
    });
  });
});

const makeWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

describe("useCallDetail", () => {
  beforeEach(() => {
    axios.get.mockReset();
    axios.get.mockResolvedValue({ data: callDetailPayload() });
  });

  it("reads the real call-executions detail endpoint and maps it", async () => {
    const { result } = renderHook(() => useCallDetail("call-1"), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.callDetail.id).toBe("call-1");
    expect(result.current.callDetail.type).toBe("voice");
    expect(axios.get).toHaveBeenCalledWith(
      endpoints.runTests.callExecutionDetail("call-1"),
    );
  });

  it("stays idle with no id", () => {
    const { result } = renderHook(() => useCallDetail(null), { wrapper: makeWrapper() });
    expect(result.current.callDetail).toBeNull();
    expect(result.current.isLoading).toBe(false);
    expect(axios.get).not.toHaveBeenCalled();
  });
});

describe("useRunDetail", () => {
  beforeEach(() => {
    axios.get.mockReset();
    axios.get.mockImplementation((url) => {
      if (url === endpoints.runTests.detailExecutions("rt1")) {
        return Promise.resolve({ data: executionsPayload() });
      }
      if (url === endpoints.testExecutions.kpis("ex-new")) {
        return Promise.resolve({ data: kpisPayload() });
      }
      if (url === endpoints.testExecutions.executionPerformanceSummary("ex-new")) {
        return Promise.resolve({ data: perfPayload() });
      }
      return Promise.resolve({ data: {} });
    });
  });

  it("resolves the matching row and merges the real kpis + performance summary", async () => {
    const { result } = renderHook(
      () => useRunDetail("rt1", "ex-new", { envName: "Refund Copilot" }),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.identity.ordinal).toBe(2);
    expect(result.current.identity.name).toBe("Refund Copilot");
    expect(result.current.stats.total).toBe(12);
    expect(result.current.stats.passRate).toBe(74);
    expect(axios.get).toHaveBeenCalledWith(
      endpoints.testExecutions.kpis("ex-new"),
    );
  });

  // `useKpis` and `useRunsSummary` share the query key
  // `["test-execution-detail", "KPIS", id]` and must cache the same shape
  // (the plain body), or whichever one mounts second reads the wrong shape
  // off the shared cache entry. Seed the cache the way `useRunsSummary` does
  // and confirm `useRunDetail` still gets a number.
  it("still reads the KPI body when the Runs summary primed the same cache key first", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(
      ["test-execution-detail", "KPIS", "ex-new"],
      { agent_type: "text", total_calls: 16, completed_calls: 12 },
    );
    const Wrapper = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    Wrapper.propTypes = { children: PropTypes.node };

    const { result } = renderHook(
      () => useRunDetail("rt1", "ex-new", { envName: "Refund Copilot" }),
      { wrapper: Wrapper },
    );

    await waitFor(() => expect(result.current.stats.completed).toBe(12));
  });
});
