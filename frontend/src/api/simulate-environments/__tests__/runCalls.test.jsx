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
const { mapCallRow, buildTraceColumns, useRunCalls } = await import("../runCalls");

// A real-shaped executions payload: two evaluation columns (one Pass/Fail, one
// score), two completed calls and one failed call.
const columnOrder = () => [
  { id: "call_details", type: "call_details", column_name: "Call Details" },
  { id: "overall_score", type: "overall_score", column_name: "CSAT" },
  { id: "eval-1", type: "evaluation", column_name: "Refund correctness", eval_config: { output: "Pass/Fail" } },
  { id: "eval-2", type: "evaluation", column_name: "Tone", eval_config: { output: "score" } },
];

const payload = () => ({
  column_order: columnOrder(),
  results: [
    {
      id: "c1", status: "completed", overall_score: 8.2, turn_count: 5,
      avg_agent_latency: 320, duration: 42.5, simulation_call_type: "voice", provider: "vapi",
      scenario: "Refund a double charge", customer_name: "Impatient caller",
      eval_metrics: {
        "eval-1": { name: "Refund correctness", value: "Passed", type: "Pass/Fail", reason: "matched policy" },
        "eval-2": { name: "Tone", value: 0.9, type: "score" },
      },
    },
    {
      id: "c2", status: "completed", overall_score: 3.1, turn_count: 14,
      avg_agent_latency: 610, duration: 88,
      scenario: "Escalate to a human", customer_name: "Angry caller",
      eval_metrics: {
        "eval-1": { name: "Refund correctness", value: "Failed", type: "Pass/Fail" },
        "eval-2": { name: "Tone", value: 0.4, type: "score" },
      },
    },
    { id: "c3", status: "failed", scenario: "Handle a timeout", customer_name: "Caller", eval_metrics: {} },
  ],
  count: 3,
});

describe("mapCallRow", () => {
  const evalCols = columnOrder().filter((c) => c.type === "evaluation");

  it("maps a passing completed call: real metrics, CSAT on the 0–10 scale, ms duration", () => {
    const t = mapCallRow(payload().results[0], evalCols);
    expect(t.id).toBe("c1");
    expect(t.scenario).toBe("Refund a double charge");
    expect(t.persona).toBe("Impatient caller");
    expect(t.status).toBe("passed");
    expect(t.critical).toBe(false);
    expect(t.csat).toBe(8.2);
    expect(t.turns).toBe(5);
    expect(t.latencyMs).toBe(320);
    expect(t.durationMs).toBe(42500);
    expect(t.tokens).toBeNull();
    // Routing hints carried onto the task for the call drawer.
    expect(t.simulationCallType).toBe("voice");
    expect(t.provider).toBe("vapi");
    // Pass/Fail → 1 / passed; score 0.9 stays 0–1 and passes the 0.5 threshold.
    expect(t.evalResults).toHaveLength(2);
    const e1 = t.evalResults.find((e) => e.id === "eval-1");
    const e2 = t.evalResults.find((e) => e.id === "eval-2");
    expect(e1).toMatchObject({ score: 1, passed: true, reason: "matched policy" });
    expect(e2).toMatchObject({ score: 0.9, passed: true });
  });

  it("marks a completed call failed when any eval failed", () => {
    const t = mapCallRow(payload().results[1], evalCols);
    expect(t.status).toBe("failed");
    expect(t.evalResults.find((e) => e.id === "eval-1").passed).toBe(false);
    expect(t.csat).toBe(3.1);
  });

  it("maps a call that never ran to error, with no eval cells", () => {
    const t = mapCallRow(payload().results[2], evalCols);
    expect(t.status).toBe("error");
    expect(t.evalResults).toHaveLength(0);
    expect(t.csat).toBeNull();
    expect(t.durationMs).toBeNull();
  });
});

describe("buildTraceColumns", () => {
  it("emits the system columns plus one column per real eval", () => {
    const cols = buildTraceColumns(columnOrder());
    const keys = cols.map((c) => c.key);
    expect(keys).toEqual(
      expect.arrayContaining(["callDetails", "persona", "scenario", "csat", "turns", "latency", "tokens", "eval-1", "eval-2"]),
    );
    const evalCols = cols.filter((c) => c.group === "Evaluations");
    expect(evalCols).toHaveLength(2);
    expect(evalCols[0]).toMatchObject({ key: "eval-1", label: "Refund correctness", defaultOn: true });
  });
});

const makeWrapper = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
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
    const { result } = renderHook(() => useRunCalls("ex1"), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.tasks).toHaveLength(3);
    expect(result.current.count).toBe(3);
    expect(result.current.columns.filter((c) => c.group === "Evaluations")).toHaveLength(2);
    expect(axios.get).toHaveBeenCalledWith(
      endpoints.testExecutions.list("ex1"),
      expect.objectContaining({ params: expect.objectContaining({ limit: 100 }) }),
    );
  });
});
