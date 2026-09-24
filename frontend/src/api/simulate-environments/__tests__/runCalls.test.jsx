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
const { mapCallRow, buildTraceColumns, useRunCalls } = await import(
  "../runCalls"
);

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
        evaluations: {},
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
