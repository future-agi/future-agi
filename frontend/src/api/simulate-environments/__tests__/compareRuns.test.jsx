import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
}));

const { default: axios } = await import("src/utils/axios");
const {
  compareRunsKey,
  compareRunsParams,
  mapCompareResponse,
  useCompareRuns,
} = await import("../compareRuns");
const { compareRunsKeyPrefix } = await import("../runEvals");
const { mapCallRow } = await import("../runCalls");
const { compareRunsResponse, COMPARE_EVAL_COLUMNS, RUN } = await import(
  "../_fixtures/compareRuns"
);

let client;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
wrapper.propTypes = { children: PropTypes.node };

const PATH = "/simulate/api/harness-environments/env-1/compare-runs/";
const RUN_IDS = [RUN.a, RUN.b, RUN.c];

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retryDelay: 0 } },
  });
  axios.get.mockReset();
});

describe("compareRunsParams", () => {
  it("sends the runs in order, the filters as JSON and the default grouping", () => {
    expect(compareRunsParams(RUN_IDS)).toEqual({
      runs: RUN_IDS.join(","),
      page: 1,
      page_size: 25,
      search: "",
      filters: "{}",
      group_by: "goal",
      ordering: "movement",
    });
  });

  it("leaves group_by out for no grouping, like the calls table", () => {
    const params = compareRunsParams(RUN_IDS, {
      groupBy: "",
      page: 3,
      pageSize: 50,
      search: "refund",
      filters: { status: ["failed"] },
    });
    expect(params).not.toHaveProperty("group_by");
    expect(params).toMatchObject({
      page: 3,
      page_size: 50,
      search: "refund",
      filters: '{"status":["failed"]}',
    });
  });
});

describe("compareRunsKey", () => {
  it("sits under the prefix the run page invalidates", () => {
    expect(compareRunsKey("env-1", RUN_IDS).slice(0, 1)).toEqual(
      compareRunsKeyPrefix,
    );
  });

  it("changes with every request param", () => {
    const base = compareRunsKey("env-1", RUN_IDS);
    expect(compareRunsKey("env-1", RUN_IDS, { page: 2 })).not.toEqual(base);
    expect(compareRunsKey("env-1", [RUN.b, RUN.a])).not.toEqual(
      compareRunsKey("env-1", [RUN.a, RUN.b]),
    );
    expect(compareRunsKey("env-1", RUN_IDS, { groupBy: "status" })).not.toEqual(
      base,
    );
  });
});

describe("mapCompareResponse", () => {
  const data = mapCompareResponse(compareRunsResponse());

  it("reads each run's whole-run pass rate from its execution summary", () => {
    expect(
      data.runs.map((r) => [r.id, r.ordinal, r.passRate, r.isBaseline]),
    ).toEqual([
      [RUN.a, 1, 33, true],
      [RUN.b, 2, 100, false],
      [RUN.c, 3, 100, false],
    ]);
    expect(data.runs[0]).toMatchObject({
      agentLabel: "v2",
      environmentLabel: "v3",
    });
    expect(data.baselineId).toBe(RUN.a);
  });

  it("maps every call with the run detail's mapCallRow", () => {
    const raw = compareRunsResponse().rows[0].calls[RUN.a][1];
    expect(data.rows[0].calls[RUN.a][1]).toEqual(
      mapCallRow(raw, COMPARE_EVAL_COLUMNS),
    );
    expect(data.rows[0].calls[RUN.a].map((t) => t.trialIndex)).toEqual([1, 2]);
  });

  it("keeps an empty list for a run that did not run the scenario", () => {
    expect(data.rows[0].calls[RUN.c]).toEqual([]);
  });

  it("maps each group's per-run figures like the calls table's groups", () => {
    const refund = data.groups[0];
    expect(refund.rowIds).toEqual(["refund-double-charge:aaa"]);
    expect(refund.runs[RUN.a]).toMatchObject({
      count: 2,
      passed: 1,
      passRate: 50,
      agg: { latency: 420, tokens: 4000, evals: { "eval-2": { scored: 2 } } },
    });
    expect(refund.runs[RUN.c]).toBeUndefined();
  });

  it("builds the run detail's columns from the evaluation union", () => {
    expect(
      data.columns.filter((c) => c.group === "Evaluations").map((c) => c.key),
    ).toEqual(["eval-1", "eval-2"]);
    expect(data.changedCount).toBe(2);
    expect(data.count).toBe(2);
  });
});

describe("useCompareRuns", () => {
  it("asks for the runs on the compare-runs endpoint and maps the answer", async () => {
    axios.get.mockResolvedValue({ data: compareRunsResponse() });
    const { result } = renderHook(
      () => useCompareRuns("env-1", RUN_IDS, { search: "x" }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledWith(PATH, {
      params: expect.objectContaining({
        runs: RUN_IDS.join(","),
        search: "x",
      }),
    });
    expect(result.current.data.rows).toHaveLength(2);
  });

  it("waits for an environment and two runs", () => {
    renderHook(() => useCompareRuns(undefined, RUN_IDS), { wrapper });
    renderHook(() => useCompareRuns("env-1", [RUN.a]), { wrapper });
    expect(axios.get).not.toHaveBeenCalled();
  });

  it("does not retry a refused comparison", async () => {
    axios.get.mockRejectedValue({ statusCode: 400 });
    const { result } = renderHook(() => useCompareRuns("env-1", RUN_IDS), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(axios.get).toHaveBeenCalledTimes(1);
  });

  it("retries a server failure", async () => {
    axios.get
      .mockRejectedValueOnce({ statusCode: 502 })
      .mockResolvedValue({ data: compareRunsResponse() });
    const { result } = renderHook(() => useCompareRuns("env-1", RUN_IDS), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledTimes(2);
  });
});
