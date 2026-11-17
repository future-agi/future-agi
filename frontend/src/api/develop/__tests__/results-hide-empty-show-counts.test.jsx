// `Hide rows with no output` is the page's own filter: it never goes to the
// results read, and the reads leave those rows out of what they load. The
// read counts Worse, Better and Changed over the rows it answers, the hidden
// ones too, so while the switch is on those counts are not passed on (a
// Worse that reads 1 could open an empty grid). The shows themselves still
// go to the read, and with the switch off the counts come through as given.
import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

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
import {
  rowsWithOutput,
  withHideEmpty,
} from "src/sections/common/filters/hideEmpty";

const DATASET = "0b5b7b9e-2222-4c8f-9a8e-0e2f8d1d2c3a";
const RUNS = ["run-a", "run-b"];
// e1 has an output; e2, the one row where run-b got worse, has none.
const ROWS = [
  {
    example_id: "e1",
    row_order: 0,
    results: { "run-a": { output: "Paris" }, "run-b": { output: "Paris" } },
  },
  {
    example_id: "e2",
    row_order: 1,
    results: { "run-a": { output: null }, "run-b": { output: "" } },
  },
];
// What the read counted over both rows.
const COUNTED = { regressed: 1, improved: 0, changed: 1 };
const SUMMARY = {
  runs: [],
  examples_total: 2,
  baseline_run_id: "run-a",
  show_counts: COUNTED,
};
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

let client;
function Wrapper({ children }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
Wrapper.propTypes = { children: PropTypes.node };

const hideOn = (pills = []) => withHideEmpty(pills, true);
const view = (extra = {}) => ({
  runIds: RUNS,
  baselineId: "run-a",
  pills: [],
  ...extra,
});

async function loaded(v) {
  const { result } = renderHook(() => useRunResults(DATASET, v), {
    wrapper: Wrapper,
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  return result.current.data;
}

beforeEach(() => {
  get.mockReset();
  get.mockImplementation(() => Promise.resolve(envelope()));
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

describe("Worse, Better and Changed counts with Hide rows with no output", () => {
  it("are not passed on while the switch is on", async () => {
    const data = await loaded(view({ pills: hideOn() }));
    expect(data.rows.map((r) => r.example_id)).toEqual(["e1"]);
    expect(data.total).toBe(1);
    expect(data.summary.show_counts).toBeNull();
    // The rest of the summary stays as the read gave it.
    expect(data.summary.baseline_run_id).toBe("run-a");
    expect(data.summary.examples_total).toBe(2);
  });

  it("come through as the read gave them with the switch off", async () => {
    const data = await loaded(view());
    expect(data.summary.show_counts).toEqual(COUNTED);
    expect(data.total).toBe(2);
  });

  it("leave Worse to the read while the switch is on", async () => {
    const v = view({ pills: hideOn(), show: "regressed" });
    expect(runResultsParams(v).show).toBe("regressed");
    const data = await loaded(v);
    expect(get.mock.calls.at(-1)[1].params.show).toBe("regressed");
    expect(data.rows.map((r) => r.example_id)).toEqual(["e1"]);
    expect(data.summary.show_counts).toBeNull();
  });
});

describe("A results answer without its rows with no output", () => {
  it("drops the read's Worse, Better and Changed counts and keeps the rest", () => {
    const answer = { total: 2, rows: ROWS, summary: SUMMARY };
    const kept = rowsWithOutput(answer, RUNS);
    expect(kept.rows.map((r) => r.example_id)).toEqual(["e1"]);
    expect(kept.total).toBe(1);
    expect(kept.summary).toEqual({ ...SUMMARY, show_counts: null });
    // The read's own answer is left as it was.
    expect(answer.summary.show_counts).toEqual(COUNTED);
  });

  it("adds no summary to an answer that has none", () => {
    const kept = rowsWithOutput({ rows: ROWS }, RUNS);
    expect(Object.keys(kept).sort()).toEqual(["rows", "total"]);
  });
});
