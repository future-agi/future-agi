// Switching on `Hide rows with no output` sent its
// expression pill to the results endpoint, which accepts no such filter
// (dataset_results.py PILL_FIELDS) and answered 400 "filters: Unsupported
// filter type in this checkpoint.": the grid read failed and the app
// toasted the server's sentence. The pill never goes to the server now; the
// reads leave the rows where no run in view has an output out of what they
// load, and nothing toasts.
import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

const { get, enqueueSnackbar } = vi.hoisted(() => ({
  get: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));

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
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...a) => enqueueSnackbar(...a),
}));

import {
  fetchResultIds,
  runResultsParams,
  useRunResults,
} from "../run-results";
import {
  HIDE_EMPTY_EXPR,
  withHideEmpty,
} from "src/sections/common/filters/hideEmpty";
import { handleError } from "src/utils/queryErrorHandler";

const DATASET = "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3a";
const RUNS = ["run-a", "run-b"];
// The four rows of the view: e1 and e4 have an output from a run in view;
// e2 and e3 have none (missing, "", {} and [] are all empty).
const ROWS = [
  {
    example_id: "e1",
    row_order: 0,
    results: { "run-a": { output: "Paris" }, "run-b": { output: null } },
  },
  {
    example_id: "e2",
    row_order: 1,
    results: { "run-a": { output: null }, "run-b": { output: "" } },
  },
  {
    example_id: "e3",
    row_order: 2,
    results: { "run-a": { output: {} }, "run-b": { output: [] } },
  },
  {
    example_id: "e4",
    row_order: 3,
    results: { "run-a": { output: null }, "run-b": { output: { answer: 4 } } },
  },
];
// What the results endpoint accepts (dataset_results.py PILL_FIELDS).
const SERVER_TYPES = new Set([
  "rows",
  "meta",
  "split",
  "error",
  "flaky",
  "score",
  "fails",
  "latency",
  "cost",
]);
const REFUSED = {
  statusCode: 400,
  result: "filters: Unsupported filter type in this checkpoint.",
};
const envelope = (part) => ({
  data: {
    status: true,
    result: {
      total: ROWS.length,
      summary: { runs: [] },
      pagination: {
        page: 0,
        page_size: 1000,
        total: ROWS.length,
        has_more: false,
        next: null,
      },
      scope: { population: ROWS.length, complete: true, selection_id: "s1" },
      ...part,
    },
  },
});
// The endpoint as it answers today: an unknown filter type is a 400.
const server = (_url, { params }) => {
  const pills = params.filters ? JSON.parse(params.filters) : [];
  if (pills.some((p) => !SERVER_TYPES.has(p.type)))
    return Promise.reject(REFUSED);
  if (params.ids_only)
    return Promise.resolve(
      envelope({ example_ids: ROWS.map((r) => r.example_id) }),
    );
  return Promise.resolve(envelope({ rows: ROWS }));
};

let client;
function Wrapper({ children }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
Wrapper.propTypes = { children: PropTypes.node };

const hideOn = (pills = []) => withHideEmpty(pills, true);

beforeEach(() => {
  get.mockReset();
  get.mockImplementation(server);
  enqueueSnackbar.mockReset();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
    queryCache: new QueryCache({ onError: handleError }),
  });
});

describe("Hide rows with no output: the results reads", () => {
  it("never sends the switch's pill to the server, and keeps every other filter", () => {
    const split = { type: "split", values: ["smoke"] };
    expect(hideOn([split])).toContainEqual({
      type: "expression",
      expr: HIDE_EMPTY_EXPR,
    });
    expect(runResultsParams({ runIds: RUNS, pills: hideOn([split]) })).toEqual({
      runs: "run-a,run-b",
      filters: JSON.stringify([split]),
    });
    expect(
      runResultsParams({ runIds: RUNS, pills: hideOn() }).filters,
    ).toBeUndefined();
  });

  it("shows only the rows where a run in view has an output, without a toast", async () => {
    const { result } = renderHook(
      () => useRunResults(DATASET, { runIds: RUNS, pills: hideOn() }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data.rows.map((r) => r.example_id)).toEqual([
      "e1",
      "e4",
    ]);
    expect(result.current.data.total).toBe(2);
    expect(result.current.isError).toBe(false);
    for (const [, { params }] of get.mock.calls)
      expect(params.filters ?? "").not.toContain("expression");
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("shows every row with the switch off", async () => {
    const { result } = renderHook(
      () => useRunResults(DATASET, { runIds: RUNS, pills: [] }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data.rows.map((r) => r.example_id)).toEqual([
      "e1",
      "e2",
      "e3",
      "e4",
    ]);
    expect(result.current.data.total).toBe(4);
  });

  it("selects only the shown rows (select all matching)", async () => {
    await expect(
      fetchResultIds(DATASET, { runIds: RUNS, pills: hideOn() }),
    ).resolves.toEqual(["e1", "e4"]);
    await expect(
      fetchResultIds(DATASET, { runIds: RUNS, pills: [] }),
    ).resolves.toEqual(["e1", "e2", "e3", "e4"]);
  });
});
