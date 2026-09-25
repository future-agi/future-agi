import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock only the axios default instance; keep the real `endpoints` so the URL
// assertion is genuine.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn() } };
});

const axiosMod = await import("src/utils/axios");
const axios = axiosMod.default;
const { endpoints } = axiosMod;
const {
  mapCallRow,
  buildTraceColumns,
  useRunCalls,
  runCallsQueryOptions,
  isEvalScoring,
  isCsatScoring,
  isCallInProgress,
  PENDING_VALUE_STALE_MS,
} = await import("../runCalls");

// A real-shaped executions payload: two evaluation columns (one Pass/Fail, one
// score), two completed calls and one failed call.
const columnOrder = () => [
  { id: "eval-1", name: "Refund correctness" },
  { id: "eval-2", name: "Tone" },
];

const payload = () => ({
  evaluation_columns: columnOrder(),
  groups: [
    {
      key: "server-only-group",
      label: "Server-computed group",
      result_ids: ["c2", "c1"],
      total: 2,
      measured: 2,
      outcomes: { passed: 1, failed: 1, error: 0, inconclusive: 0 },
      aggregates: {
        csat: 5.65,
        turns: 9.5,
        latency_ms: 465,
        tokens: 450,
        evaluations: { "eval-1": { scored: 8, score_sum: 6 } },
      },
    },
  ],
  results: [
    {
      id: "c1",
      outcome: "passed",
      csat: 8.2,
      turn_count: 5,
      latency_ms: 320,
      duration_seconds: 42.5,
      modality: "voice",
      provider: "vapi",
      goal: "Refund a double charge",
      scenario: "Routine refund",
      scenario_details: "Customer requests a refund for a duplicate charge.",
      ideal_outcome: "Refund is created after account verification",
      conversation_branch: "duplicate-charge-refund",
      persona: "Impatient caller",
      persona_details: {
        name: "Impatient caller",
        voice: "US female",
        age: "34",
        traits: ["impatient", "in a hurry"],
      },
      sub_goals: ["identity_verified", "refund_created"],
      tokens: 450,
      evaluations: [
        {
          id: "eval-1",
          name: "Refund correctness",
          value: "Passed",
          score: 1,
          passed: true,
          reason: "matched policy",
        },
        { id: "eval-2", name: "Tone", value: 0.9, score: 0.9, passed: null },
      ],
    },
    {
      id: "c2",
      outcome: "failed",
      csat: 3.1,
      turn_count: 14,
      latency_ms: 610,
      duration_seconds: 88,
      goal: "Escalate to a human",
      persona: "Angry caller",
      evaluations: [
        {
          id: "eval-1",
          name: "Refund correctness",
          value: "Failed",
          score: 0,
          passed: false,
        },
        { id: "eval-2", name: "Tone", value: 0.4, score: 0.4, passed: null },
      ],
    },
    {
      id: "c3",
      outcome: "error",
      goal: "Handle a timeout",
      persona: "Caller",
      evaluations: [],
    },
  ],
  count: 3,
});

describe("mapCallRow", () => {
  const evalCols = columnOrder();

  it("renders a live choices verdict by its label and inner score", () => {
    const row = {
      id: "c9",
      evaluations: [{ id: "e-choice", name: "clarification", type: "choices", score: null,
        value: { score: 1.0, choice: "always" } }],
    };
    const [cell] = mapCallRow(row, [{ id: "e-choice", name: "clarification" }]).evalResults;
    expect(cell.label).toBe("always");
    expect(cell.score).toBe(1);
  });

  it("keeps the server's verdict on a choice and the score of a label-less object", () => {
    const row = {
      id: "c9",
      evaluations: [
        { id: "e-choice", type: "choices", score: null, passed: true, value: { score: 1.0, choice: "always" } },
        { id: "e-score", type: "score", score: null, value: { score: 0.4 } },
      ],
    };
    const [choice, scored] = mapCallRow(row, [{ id: "e-choice" }, { id: "e-score" }]).evalResults;
    expect(choice.passed).toBe(true);
    expect(choice.label).toBe("always");
    expect(scored.score).toBe(0.4);
  });

  it("maps a passing completed call: real metrics, CSAT on the 0–10 scale, ms duration", () => {
    const t = mapCallRow(payload().results[0], evalCols);
    expect(t.id).toBe("c1");
    expect(t.scenario).toBe("Routine refund");
    expect(t.persona).toBe("Impatient caller");
    expect(t.personaDetails).toEqual({
      name: "Impatient caller",
      voice: "US female",
      age: "34",
      traits: ["impatient", "in a hurry"],
    });
    expect(t.goal).toBe("Refund a double charge");
    expect(t.subGoals).toEqual(["identity_verified", "refund_created"]);
    expect(t.scenario).toBe("Routine refund");
    expect(t.scenarioDetails).toBe(
      "Customer requests a refund for a duplicate charge.",
    );
    expect(t.idealOutcome).toBe("Refund is created after account verification");
    expect(t.conversationBranch).toBe("duplicate-charge-refund");
    expect(t.status).toBe("passed");
    expect(t.critical).toBe(false);
    expect(t.csat).toBe(8.2);
    expect(t.turns).toBe(5);
    expect(t.latencyMs).toBe(320);
    expect(t.durationMs).toBe(42500);
    expect(t.tokens).toBe(450);
    // Routing hints carried onto the task for the call drawer.
    expect(t.simulationCallType).toBe("voice");
    expect(t.provider).toBe("vapi");
    // Pass/Fail → 1 / passed; score 0.9 stays 0–1 and passes the 0.5 threshold.
    expect(t.evalResults).toHaveLength(2);
    const e1 = t.evalResults.find((e) => e.id === "eval-1");
    const e2 = t.evalResults.find((e) => e.id === "eval-2");
    expect(e1).toMatchObject({
      score: 1,
      passed: true,
      reason: "matched policy",
    });
    expect(e2).toMatchObject({ score: 0.9, passed: null });
  });

  it("marks a completed call failed when any eval failed", () => {
    const t = mapCallRow(payload().results[1], evalCols);
    expect(t.status).toBe("failed");
    expect(t.evalResults.find((e) => e.id === "eval-1").passed).toBe(false);
    expect(t.csat).toBe(3.1);
  });

  it("uses the sealed trial outcome while retaining completed transport status", () => {
    const task = mapCallRow(
      {
        id: "call-error",
        outcome: "error",
        harness_outcome_status: "error",
        execution_status: "completed",
        evaluations: [],
      },
      [],
    );

    expect(task.status).toBe("error");
    expect(task.harnessOutcomeStatus).toBe("error");
    expect(task.executionStatus).toBe("completed");
  });

  it("maps a call that never ran to error, with no eval cells", () => {
    const t = mapCallRow(payload().results[2], evalCols);
    expect(t.status).toBe("error");
    expect(t.evalResults).toHaveLength(0);
    expect(t.csat).toBeNull();
    expect(t.durationMs).toBeNull();
  });

  it("carries a removed eval's marker", () => {
    const row = {
      id: "c4",
      status: "completed",
      eval_metrics: {
        "cfg-gone": { name: "no_misselling", value: "Failed", type: "Pass/Fail", removed: true },
      },
    };
    const t = mapCallRow(row, [{ id: "cfg-gone", type: "evaluation" }]);
    expect(t.evalResults).toHaveLength(1);
    expect(t.evalResults[0].removed).toBe(true);
    // A live eval's cell carries no such key (falsy, never merely absent).
    const live = mapCallRow(payload().results[0], evalCols);
    expect(live.evalResults.every((e) => e.removed === false)).toBe(true);
  });

  it("surfaces source scenario and trial identity for repeated executions", () => {
    const t = mapCallRow(
      {
        id: "c-trial",
        status: "completed",
        scenario: "Shared suite",
        source_scenario_key: "refund-double-charge",
        trial_index: 2,
        eval_metrics: {},
      },
      evalCols,
    );

    expect(t.scenario).toBe("refund-double-charge · Trial 2");
    expect(t.sourceScenario).toBe("refund-double-charge");
    expect(t.trialIndex).toBe(2);
  });
});

// Cells whose value is still coming. Timestamps are relative to an injected
// `now` for the pure predicates, and to the real clock for polling.
const NOW = Date.parse("2026-09-25T12:00:00Z");
const ago = (ms, now = NOW) => new Date(now - ms).toISOString();
const FRESH = 30 * 1000;
const STALE = 11 * 60 * 1000;

describe("pending values", () => {
  it("caps a pending value at ten minutes", () => {
    expect(PENDING_VALUE_STALE_MS).toBe(10 * 60 * 1000);
  });

  describe("isEvalScoring", () => {
    it("is true while a fresh call's evals are started and not completed", () => {
      expect(
        isEvalScoring({ eval_started: true, completed_at: ago(FRESH) }, NOW),
      ).toBe(true);
    });

    it("is false once scoring completes", () => {
      const row = {
        eval_started: true,
        eval_completed: true,
        completed_at: ago(FRESH),
      };
      expect(isEvalScoring(row, NOW)).toBe(false);
    });

    it("is false when the flags are absent", () => {
      expect(isEvalScoring({ completed_at: ago(FRESH) }, NOW)).toBe(false);
    });

    it("is false once the call is past the cap", () => {
      expect(
        isEvalScoring({ eval_started: true, completed_at: ago(STALE) }, NOW),
      ).toBe(false);
    });

    it("is false with no timestamp", () => {
      expect(isEvalScoring({ eval_started: true }, NOW)).toBe(false);
    });

    it("falls back to started_at", () => {
      expect(
        isEvalScoring({ eval_started: true, started_at: ago(FRESH) }, NOW),
      ).toBe(true);
    });
  });

  describe("isCsatScoring", () => {
    it.each(["pending", "running"])("is true while CSAT is %s", (status) => {
      expect(
        isCsatScoring({ csat_status: status, completed_at: ago(FRESH) }, NOW),
      ).toBe(true);
    });

    it.each(["completed", "failed", undefined, null])(
      "is false when CSAT is %s",
      (status) => {
        expect(
          isCsatScoring({ csat_status: status, completed_at: ago(FRESH) }, NOW),
        ).toBe(false);
      },
    );

    it("is false once the call is past the cap", () => {
      expect(
        isCsatScoring(
          { csat_status: "running", completed_at: ago(STALE) },
          NOW,
        ),
      ).toBe(false);
    });
  });

  describe("isCallInProgress", () => {
    it.each(["pending", "queued", "ongoing", "analyzing"])(
      "is true for a %s call while the run is active",
      (status) => {
        expect(isCallInProgress({ execution_status: status }, true)).toBe(true);
      },
    );

    it.each(["completed", "failed", "cancelled"])(
      "is false for a %s call",
      (status) => {
        expect(isCallInProgress({ execution_status: status }, true)).toBe(
          false,
        );
      },
    );

    it("is false once the run is no longer active", () => {
      expect(isCallInProgress({ execution_status: "ongoing" }, false)).toBe(
        false,
      );
    });
  });

  describe("mapCallRow", () => {
    it("marks which values are still coming", () => {
      const row = {
        id: "c1",
        execution_status: "ongoing",
        eval_started: true,
        csat_status: "running",
        completed_at: ago(FRESH),
      };
      expect(
        mapCallRow(row, [], { now: NOW, runActive: true }).pending,
      ).toEqual({ evals: true, csat: true, metrics: true });
    });

    it("loads a running call's evals, and CSAT only for a voice call", () => {
      const running = { id: "c1", execution_status: "pending" };
      const voice = mapCallRow({ ...running, modality: "voice" }, [], {
        now: NOW,
        runActive: true,
      }).pending;
      const chat = mapCallRow({ ...running, modality: "text" }, [], {
        now: NOW,
        runActive: true,
      }).pending;
      expect(voice).toEqual({ evals: true, csat: true, metrics: true });
      expect(chat).toEqual({ evals: true, csat: false, metrics: true });
    });

    it("does not load a stalled call once its run has ended", () => {
      const row = { id: "c1", execution_status: "pending", modality: "voice" };
      expect(
        mapCallRow(row, [], { now: NOW, runActive: false }).pending,
      ).toEqual({ evals: false, csat: false, metrics: false });
    });

    it("marks a failed or errored eval as errored, keeping its reason", () => {
      const cols = [{ id: "e1" }, { id: "e2" }, { id: "e3" }];
      const row = {
        id: "c1",
        evaluations: [
          { id: "e1", status: "Failed", score: null, reason: "Judge timed out" },
          { id: "e2", status: "error", score: null, reason: "" },
          { id: "e3", status: "completed", score: 0.9, passed: true },
        ],
      };
      const [failed, errored, scored] = mapCallRow(row, cols).evalResults;
      expect(failed).toMatchObject({ errored: true, reason: "Judge timed out" });
      expect(errored.errored).toBe(true);
      expect(scored.errored).toBe(false);
    });

    it("marks a failed CSAT with its error, only when there is no score", () => {
      const failed = mapCallRow({
        id: "c1",
        csat_status: "failed",
        csat_error: "CSAT scorer returned no result",
      });
      expect(failed).toMatchObject({
        csatFailed: true,
        csatError: "CSAT scorer returned no result",
      });
      expect(
        mapCallRow({ id: "c2", csat_status: "failed", csat: 7 }).csatFailed,
      ).toBe(false);
      expect(mapCallRow({ id: "c3", csat_status: "completed" }).csatFailed).toBe(
        false,
      );
    });

    it("marks nothing pending once the run was stopped", () => {
      const row = {
        id: "c1",
        execution_status: "completed",
        eval_started: true,
        csat_status: "pending",
        completed_at: ago(FRESH),
      };
      expect(
        mapCallRow(row, [], { now: NOW, runStopped: true }).pending,
      ).toEqual({ evals: false, csat: false, metrics: false });
    });

    it("marks nothing pending by default", () => {
      expect(mapCallRow(payload().results[0], columnOrder()).pending).toEqual({
        evals: false,
        csat: false,
        metrics: false,
      });
    });
  });
});

describe("buildTraceColumns", () => {
  it("emits the system columns plus one column per real eval", () => {
    const cols = buildTraceColumns(columnOrder());
    const keys = cols.map((c) => c.key);
    expect(keys).toEqual(
      expect.arrayContaining([
        "callDetails",
        "persona",
        "scenario",
        "idealOutcome",
        "conversationBranch",
        "csat",
        "turns",
        "latency",
        "tokens",
        "eval-1",
        "eval-2",
      ]),
    );
    const evalCols = cols.filter((c) => c.group === "Evaluations");
    expect(evalCols).toHaveLength(2);
    expect(evalCols[0]).toMatchObject({
      key: "eval-1",
      label: "Refund correctness",
      defaultOn: true,
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

describe("useRunCalls", () => {
  beforeEach(() => {
    axios.get.mockReset();
    axios.get.mockResolvedValue({ data: payload() });
  });

  it("reads the real executions list and adapts it to tasks + columns", async () => {
    const { result } = renderHook(() => useRunCalls("ex1"), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.error).toBeNull();
    expect(result.current.tasks).toHaveLength(3);
    expect(result.current.count).toBe(3);
    expect(
      result.current.columns.filter((c) => c.group === "Evaluations"),
    ).toHaveLength(2);
    expect(result.current.groups).toHaveLength(1);
    expect(result.current.groups[0].label).toBe("Server-computed group");
    expect(result.current.groups[0].agg).toMatchObject({
      evals: { "eval-1": { scored: 8, scoreSum: 6 } },
    });
    expect(result.current.groups[0].rows.map((row) => row.id)).toEqual([
      "c2",
      "c1",
    ]);
    expect(axios.get).toHaveBeenCalledWith(
      endpoints.runResultsV3.calls("ex1"),
      expect.objectContaining({
        params: expect.objectContaining({ page_size: 100 }),
      }),
    );
  });

  it("returns a stable empty result while the first request is pending", () => {
    axios.get.mockImplementation(() => new Promise(() => {}));
    const { result, unmount } = renderHook(() => useRunCalls("ex1"), {
      wrapper: makeWrapper(),
    });

    expect(result.current).toMatchObject({
      tasks: [],
      columns: [],
      count: 0,
      groups: [],
      facets: {},
      summary: null,
      totalPages: 1,
      isLoading: true,
    });
    unmount();
  });

  it("polls active execution results and stops polling when the Run is terminal", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const Wrapper = ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    Wrapper.propTypes = { children: PropTypes.node };
    axios.get.mockResolvedValue({
      data: { ...payload(), execution: { status: "cancelling" } },
    });

    const { unmount } = renderHook(() => useRunCalls("ex1"), {
      wrapper: Wrapper,
    });
    await waitFor(() =>
      expect(
        queryClient
          .getQueryCache()
          .findAll({ queryKey: ["simulation-run-results-v3", "ex1"] })[0]?.state
          .data?.execution?.status,
      ).toBe("cancelling"),
    );
    const query = queryClient
      .getQueryCache()
      .findAll({ queryKey: ["simulation-run-results-v3", "ex1"] })[0];

    expect(query.options.refetchInterval(query)).toBe(3000);
    queryClient.setQueryData(query.queryKey, {
      ...payload(),
      execution: { status: "completed" },
    });
    expect(query.options.refetchInterval(query)).toBe(false);
    unmount();
  });
});

describe("useRunCalls polling for values still coming", () => {
  const pollFor = async (data) => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const Wrapper = ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    Wrapper.propTypes = { children: PropTypes.node };
    axios.get.mockReset();
    axios.get.mockResolvedValue({ data });
    const { unmount } = renderHook(() => useRunCalls("ex1"), {
      wrapper: Wrapper,
    });
    await waitFor(() =>
      expect(
        queryClient
          .getQueryCache()
          .findAll({ queryKey: ["simulation-run-results-v3", "ex1"] })[0]?.state
          .data,
      ).toBeTruthy(),
    );
    const query = queryClient
      .getQueryCache()
      .findAll({ queryKey: ["simulation-run-results-v3", "ex1"] })[0];
    const interval = query.options.refetchInterval(query);
    unmount();
    return interval;
  };
  const completedRun = (rowOverrides) => {
    const data = { ...payload(), execution: { status: "completed" } };
    data.results[0] = { ...data.results[0], ...rowOverrides };
    return data;
  };

  it.each(["cancelled", "failed"])(
    "stops polling a %s run even if a call's scoring never finished",
    async (status) => {
      const data = completedRun({
        eval_started: true,
        csat_status: "pending",
        completed_at: ago(FRESH, Date.now()),
      });
      data.execution.status = status;
      expect(await pollFor(data)).toBe(false);
    },
  );

  it("keeps polling a completed run while a call's evals are scoring", async () => {
    expect(
      await pollFor(
        completedRun({
          eval_started: true,
          completed_at: ago(FRESH, Date.now()),
        }),
      ),
    ).toBe(3000);
  });

  it("keeps polling a completed run while a call's CSAT is scoring", async () => {
    expect(
      await pollFor(
        completedRun({
          csat_status: "pending",
          completed_at: ago(FRESH, Date.now()),
        }),
      ),
    ).toBe(3000);
  });

  it("stops polling once the scoring call is past the cap", async () => {
    expect(
      await pollFor(
        completedRun({
          eval_started: true,
          csat_status: "running",
          completed_at: ago(STALE, Date.now()),
        }),
      ),
    ).toBe(false);
  });

  it("stops polling when nothing is still coming", async () => {
    expect(
      await pollFor(
        completedRun({
          eval_started: true,
          eval_completed: true,
          csat_status: "completed",
          completed_at: ago(FRESH, Date.now()),
        }),
      ),
    ).toBe(false);
  });

  it("polls an active run regardless", async () => {
    expect(
      await pollFor({ ...payload(), execution: { status: "running" } }),
    ).toBe(3000);
  });

  it("marks an active run's in-progress calls pending", async () => {
    axios.get.mockReset();
    const data = { ...payload(), execution: { status: "running" } };
    data.results[2] = { ...data.results[2], execution_status: "ongoing" };
    axios.get.mockResolvedValue({ data });
    const { result } = renderHook(() => useRunCalls("ex1"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.tasks[2].pending.metrics).toBe(true);
    expect(result.current.tasks[0].pending.metrics).toBe(false);
  });
});

describe("runCallsQueryOptions", () => {
  beforeEach(() => {
    axios.get.mockReset();
    axios.get.mockResolvedValue({ data: payload() });
  });

  it("is the one key + request the table hook and page fetches share", async () => {
    const options = runCallsQueryOptions("ex1", {
      page: 2,
      limit: 50,
      search: "",
      filters: { goal: ["Refunds"] },
      groupBy: "goal",
    });

    expect(options.queryKey).toEqual([
      "simulation-run-results-v3",
      "ex1",
      2,
      50,
      "",
      { goal: ["Refunds"] },
      "goal",
    ]);
    await options.queryFn();
    expect(axios.get).toHaveBeenCalledWith(endpoints.runResultsV3.calls("ex1"), {
      params: {
        page: 2,
        page_size: 50,
        search: "",
        filters: JSON.stringify({ goal: ["Refunds"] }),
        group_by: "goal",
      },
    });
  });

  it("lets a reader switch the table hook off", () => {
    renderHook(() => useRunCalls("ex1", { enabled: false }), {
      wrapper: makeWrapper(),
    });
    expect(axios.get).not.toHaveBeenCalled();
  });
});
