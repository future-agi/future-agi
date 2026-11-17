// The results grid asks its read only for what the read answers. The column
// menu's Average reaches the read as `mean`, the read's word for the plain
// average of the assessed values, and the grid loads its rows. Where this
// build's read does not answer Worse, Better or Changed, no read asks for
// them and a link that names one reads as All. Where it does not answer
// Median, N/A rate or a per-try reducer, no read asks for them and a link
// that names one reads as Average. Where the read's published query contract
// lists Worse, Better, Changed and the Median, the page sends them and reads
// their links as named. All, errors, failed and flaky go as before.
import React from "react";
import PropTypes from "prop-types";
import * as zod from "zod";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
const contract = vi.hoisted(() => ({ show: null, agg: null }));

vi.mock("src/generated/api-contracts/api.zod", async (importOriginal) => {
  const actual = await importOriginal();
  const real = actual.DatasetResultsReadQueryParams;
  return {
    ...actual,
    DatasetResultsReadQueryParams: {
      ...real,
      shape: {
        ...real.shape,
        get show() {
          return contract.show;
        },
        get agg() {
          return contract.agg;
        },
      },
    },
  };
});

vi.mock("src/utils/axios", () => ({
  default: { get },
  endpoints: {
    develop: {
      datasetRuns: {
        results: (id) => `/model-hub/datasets/${id}/results/`,
        list: (id) => `/model-hub/datasets/${id}/runs/`,
      },
    },
  },
}));

import { runResultsParams, useRunResults } from "../run-results";
import { readResultsState } from "src/sections/experiment-detail/results/useResultsUrlState";

const DATASET = "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3a";
const RUNS = ["run-a", "run-b"];
const BASE = "run-a";
const NOT_ANSWERED = ["regressed", "improved", "changed"];
const AGGS_NOT_ANSWERED = [
  "median",
  "na_rate",
  "mode",
  "max",
  "pass_at_k",
  "pass_hat_k",
  "at_least:2",
];

// What the results read accepts (model_hub/serializers/dataset_results.py,
// DatasetResultsQuerySerializer: show, sort and agg choices), without and
// with Worse, Better, Changed and the Median. The read answers what its
// contract lists.
const SHOWS = ["all", "errors", "failed", "flaky"];
const SORTS = ["order", "-order", "input", "-input", "regressed"];
const AGGS = ["mean"];
const MOVEMENT = ["regressed", "improved", "changed"];
const MEDIAN = "median";
const PER_TRY = AGGS_NOT_ANSWERED.filter(
  (agg) => !["median", "na_rate"].includes(agg),
);
const contractOf = ({ shows = SHOWS, aggs = AGGS } = {}) => {
  contract.show = zod.enum(shows).default("all");
  contract.agg = zod.enum(aggs).default("mean");
};
const listed = (field) => contract[field].removeDefault().options;

const score = (value) => ({
  value,
  n: 1,
  status: "assessed",
  rule: "assessed_trial_mean",
});
const ROWS = [
  {
    example_id: "e1",
    row_order: 0,
    results: {
      "run-a": { output: "Paris", scores: { "eval:correctness": score(1) } },
      "run-b": { output: "Lyon", scores: { "eval:correctness": score(0) } },
    },
  },
  {
    example_id: "e2",
    row_order: 1,
    results: {
      "run-a": { output: "4", scores: { "eval:correctness": score(0.5) } },
      "run-b": { output: "4", scores: { "eval:correctness": score(1) } },
    },
  },
];
// The summary as the read serves it: one assessed-example mean per run.
const SUMMARY = {
  runs: [],
  aggregates: {
    "eval:correctness": {
      "run-a": { value: 0.75, n: 2, rule: "assessed_example_mean", total: 2 },
      "run-b": { value: 0.5, n: 2, rule: "assessed_example_mean", total: 2 },
    },
  },
};
const refused = (field) => ({
  statusCode: 400,
  result: `${field}: not a valid choice.`,
});
const envelope = () => ({
  data: {
    status: true,
    result: {
      total: ROWS.length,
      rows: ROWS,
      summary: SUMMARY,
      pagination: {
        page: 0,
        page_size: 1000,
        total: ROWS.length,
        has_more: false,
        next: null,
      },
      scope: { population: ROWS.length, complete: true, selection_id: "s1" },
    },
  },
});
// The read as it answers today: a value outside its choices is a 400.
const server = (_url, { params }) => {
  if (params.show !== undefined && !listed("show").includes(params.show))
    return Promise.reject(refused("show"));
  if (params.sort !== undefined && !SORTS.includes(params.sort))
    return Promise.reject(refused("sort"));
  if (params.agg !== undefined && !listed("agg").includes(params.agg))
    return Promise.reject(refused("agg"));
  return Promise.resolve(envelope());
};

let client;
function Wrapper({ children }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
Wrapper.propTypes = { children: PropTypes.node };

const sent = () => get.mock.calls.map(([, { params }]) => params);

beforeEach(() => {
  contractOf();
  get.mockReset();
  get.mockImplementation(server);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => {
  setLaunchOverrideForTest(null);
  client.clear();
});

describe("Average in a column's menu", () => {
  it("asks the read for its mean", () => {
    expect(runResultsParams({ runIds: RUNS, agg: "avg" }).agg).toBe("mean");
  });

  it("loads the grid's rows and their values", async () => {
    const { result } = renderHook(
      () =>
        useRunResults(DATASET, {
          runIds: RUNS,
          baselineId: BASE,
          sort: "regressed",
          agg: "avg",
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.isError).toBe(false);
    expect(result.current.data.rows.map((r) => r.example_id)).toEqual([
      "e1",
      "e2",
    ]);
    expect(
      result.current.data.rows[1].results["run-a"].scores["eval:correctness"]
        .value,
    ).toBe(0.5);
    expect(
      result.current.data.summary.aggregates["eval:correctness"]["run-a"].value,
    ).toBe(0.75);
    expect(sent().length).toBeGreaterThan(0);
    for (const params of sent()) expect(params.agg).toBe("mean");
  });

  it("sends no aggregate where the page names none", () => {
    expect(runResultsParams({ runIds: RUNS }).agg).toBeUndefined();
  });
});

describe("Worse, Better and Changed where this build's read does not answer them", () => {
  beforeEach(() => setLaunchOverrideForTest({ launch: true }));

  it.each(NOT_ANSWERED)("sends no show=%s", (show) => {
    expect(
      runResultsParams({ runIds: RUNS, baselineId: BASE, show }).show,
    ).toBeUndefined();
  });

  it("loads every row and asks no read for those shows", async () => {
    const { result } = renderHook(
      () =>
        useRunResults(DATASET, {
          runIds: RUNS,
          baselineId: BASE,
          show: "regressed",
          sort: "regressed",
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data.rows).toHaveLength(ROWS.length);
    expect(sent().length).toBeGreaterThan(0);
    for (const params of sent())
      expect(NOT_ANSWERED).not.toContain(params.show);
  });

  it.each(["worse", "regressed", "improved", "changed"])(
    "reads a show=%s link as All",
    (value) => {
      expect(readResultsState(new URLSearchParams({ show: value })).show).toBe(
        "all",
      );
    },
  );
});

describe("All, errors, failed and flaky (as before)", () => {
  beforeEach(() => setLaunchOverrideForTest({ launch: true }));

  it.each(["errors", "failed", "flaky"])("sends show=%s", (show) => {
    expect(
      runResultsParams({ runIds: RUNS, baselineId: BASE, show }).show,
    ).toBe(show);
  });

  it("sends no show for All", () => {
    expect(
      runResultsParams({ runIds: RUNS, baselineId: BASE, show: "all" }).show,
    ).toBeUndefined();
  });

  it("loads the rows the read answers for errors", async () => {
    const { result } = renderHook(
      () =>
        useRunResults(DATASET, {
          runIds: RUNS,
          baselineId: BASE,
          show: "errors",
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    for (const params of sent()) expect(params.show).toBe("errors");
  });
});

describe("Worse, Better and Changed where the read answers every show", () => {
  it("sends them and reads their links as before", () => {
    for (const show of NOT_ANSWERED)
      expect(runResultsParams({ runIds: RUNS, show }).show).toBe(show);
    expect(readResultsState(new URLSearchParams({ show: "worse" })).show).toBe(
      "regressed",
    );
  });
});

describe("Median, N/A rate and the per-try reducers where this build's read does not answer them", () => {
  beforeEach(() => setLaunchOverrideForTest({ launch: true }));

  it.each(AGGS_NOT_ANSWERED)("sends no agg=%s", (agg) => {
    expect(runResultsParams({ runIds: RUNS, agg }).agg).toBeUndefined();
  });

  it("loads every row and asks no read for them", async () => {
    const { result } = renderHook(
      () =>
        useRunResults(DATASET, {
          runIds: RUNS,
          baselineId: BASE,
          sort: "regressed",
          agg: "median",
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data.rows).toHaveLength(ROWS.length);
    expect(sent().length).toBeGreaterThan(0);
    for (const params of sent()) expect(params.agg).toBeUndefined();
  });

  it.each(AGGS_NOT_ANSWERED)("reads an agg=%s link as Average", (agg) => {
    expect(readResultsState(new URLSearchParams({ agg })).agg).toBe("avg");
  });

  it("sends Average as mean and reads its link as before", () => {
    expect(runResultsParams({ runIds: RUNS, agg: "avg" }).agg).toBe("mean");
    expect(readResultsState(new URLSearchParams({ agg: "avg" })).agg).toBe(
      "avg",
    );
  });

  it("reads a link with no aggregate as each run's own and sends none", () => {
    expect(readResultsState(new URLSearchParams()).agg).toBe("run");
    expect(runResultsParams({ runIds: RUNS }).agg).toBeUndefined();
  });
});

describe("Median, N/A rate and the per-try reducers where the read answers them", () => {
  it.each(AGGS_NOT_ANSWERED)("sends agg=%s and reads its link as is", (agg) => {
    expect(runResultsParams({ runIds: RUNS, agg }).agg).toBe(agg);
    expect(readResultsState(new URLSearchParams({ agg })).agg).toBe(agg);
  });
});

describe("Worse, Better, Changed and the Median where the read's contract lists them", () => {
  beforeEach(() => {
    setLaunchOverrideForTest({ launch: true });
    contractOf({ shows: [...SHOWS, ...MOVEMENT], aggs: [...AGGS, MEDIAN] });
  });

  it.each(MOVEMENT)("sends show=%s and reads its link as is", (show) => {
    expect(
      runResultsParams({ runIds: RUNS, baselineId: BASE, show }).show,
    ).toBe(show);
    expect(readResultsState(new URLSearchParams({ show })).show).toBe(show);
  });

  it("reads a show=worse link as Worse", () => {
    expect(readResultsState(new URLSearchParams({ show: "worse" })).show).toBe(
      "regressed",
    );
  });

  it("sends agg=median and reads its link as is", () => {
    expect(runResultsParams({ runIds: RUNS, agg: MEDIAN }).agg).toBe(MEDIAN);
    expect(readResultsState(new URLSearchParams({ agg: MEDIAN })).agg).toBe(
      MEDIAN,
    );
  });

  it("loads the rows the read answers for Worse under the Median", async () => {
    const { result } = renderHook(
      () =>
        useRunResults(DATASET, {
          runIds: RUNS,
          baselineId: BASE,
          show: "regressed",
          sort: "regressed",
          agg: MEDIAN,
        }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(sent().length).toBeGreaterThan(0);
    for (const params of sent())
      expect([params.show, params.agg]).toEqual(["regressed", MEDIAN]);
  });

  it.each(["na_rate", ...PER_TRY])(
    "still sends no agg=%s, which the contract does not list",
    (agg) => {
      expect(runResultsParams({ runIds: RUNS, agg }).agg).toBeUndefined();
      expect(readResultsState(new URLSearchParams({ agg })).agg).toBe("avg");
    },
  );
});
