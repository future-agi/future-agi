// The Experiments list on a dataset whose runs sit in several comparison
// groups, with the pinned baseline in one of them. The results API stand-in
// keeps the server's rules (results_selection.py, results_catalog.py): one
// read answers runs of ONE comparison group, and adds the pinned baseline
// unless the read says baseline=none; anything else is refused with 409.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import axios from "src/utils/axios";

vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "default" }),
}));

const hook = await import("../useLeaderboardNumbers");
const useLeaderboardNumbers = hook.default;
const { notReadReason, successFrom } = await import("../leaderboardVersions");

const PINNED = "a2";
// run id -> [comparison group, tries per row as [passed, cost_usd, wall_ms]]
const RUNS = {
  a1: [
    "group-a",
    [
      [true, 0.01, 1000],
      [true, 0.02, 3000],
      [false, 0.03, 2000],
    ],
  ],
  a2: [
    "group-a",
    [
      [true, 0.01, 900],
      [true, 0.01, 1100],
      [true, 0.01, 1000],
    ],
  ],
  b1: [
    "group-b",
    [
      [false, 0.02, 500],
      [true, 0.02, 500],
      ["error", 0.02, 700],
    ],
  ],
  c1: [
    "group-c",
    [
      [true, null, 400],
      [false, null, 600],
    ],
  ],
  d1: [
    "group-d",
    [
      [true, 0.01, 100],
      [false, 0.01, 100],
      [true, 0.01, 100],
      [true, 0.01, 100],
      [false, 0.01, 100],
      [false, 0.01, 100],
    ],
    2,
  ],
};
const EXPECTED = { a1: [2, 3], a2: [3, 3], b1: [1, 3], c1: [1, 2], d1: [3, 6] };

const run = (id, group, extra = {}) => ({
  run_id: id,
  label: `#${id}`,
  status: "Completed",
  listed: true,
  comparison_group: group,
  progress: { done: 3, total: 3 },
  ...extra,
});
const runs = [
  ...Object.entries(RUNS).map(([id, [group]]) => run(id, group)),
  // No immutable plan: no comparison group, never readable as a matrix.
  run("old", null, { success_rate: { passed: 1, total: 2 } }),
];

// The matrix of `ids`, one row per try group, as results_matrix.py shapes it.
function matrixOf(ids) {
  const rows = new Map();
  ids.forEach((id) => {
    const [, tries, k = 1] = RUNS[id];
    for (let i = 0; i < tries.length; i += k) {
      const rowId = `${RUNS[id][0]}-row-${i / k}`;
      const part = tries.slice(i, i + k);
      if (!rows.has(rowId)) rows.set(rowId, { row_id: rowId, cells: {} });
      rows.get(rowId).cells[id] = {
        trials: part.map(([p]) => p),
        trace_summary: part.map(([p]) => ({
          status: p === "error" ? "error" : "completed",
        })),
        cost_usd: part.map(([, usd]) => usd),
        wall_ms: part.map(([, , ms]) => ms),
      };
    }
  });
  return {
    rows: [...rows.values()],
    columns: Object.fromEntries(ids.map((id) => [id, { trials: 1 }])),
    total: rows.size,
    scope: { complete: true, selection_id: ids.join(",") },
    pagination: {
      page: 0,
      page_size: 500,
      has_more: false,
      next: null,
    },
  };
}

let failGroup = null;
function serve(url, config = {}) {
  if (!String(url).includes("/results/"))
    return Promise.resolve({ data: { result: {} } });
  const params = config.params ?? {};
  const ids = String(params.runs).split(",");
  const read =
    params.baseline === "none" ? ids : [...new Set([...ids, PINNED])];
  const groups = new Set(read.map((id) => RUNS[id]?.[0]));
  if (groups.size !== 1 || groups.has(undefined))
    return Promise.reject({
      statusCode: 409,
      result:
        "Runs require the same immutable version, trial plan and evaluator bindings; mixed comparison is not supported.",
    });
  if (failGroup && groups.has(failGroup))
    return Promise.reject({ statusCode: 503, result: "Try again" });
  return Promise.resolve({ data: { status: true, result: matrixOf(ids) } });
}

const wrapper = ({ children }) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {children}
  </QueryClientProvider>
);
const view = {
  datasetId: "ds-1",
  allRuns: runs,
  baselineRun: runs.find((r) => r.run_id === PINNED),
};
const matrixCalls = (spy) =>
  spy.mock.calls
    .filter(([url]) => String(url).includes("/results/"))
    .map(([, config]) => config.params);

let get;
beforeEach(() => {
  failGroup = null;
  get = vi.spyOn(axios, "get").mockImplementation(serve);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Experiments list numbers across comparison groups", () => {
  it("every run's success comes back, one read per group with no baseline added", async () => {
    const { result } = renderHook(() => useLeaderboardNumbers(view), {
      wrapper,
    });
    await waitFor(() => expect(result.current.matrix).not.toBeNull());
    Object.entries(EXPECTED).forEach(([id, [passed, total]]) => {
      const s = successFrom(
        result.current,
        runs.find((r) => r.run_id === id),
      );
      expect(s).toMatchObject({ passed, total });
    });
    // The run with no plan is not read: no number, and a reason instead of
    // its own summary fields.
    const old = runs.find((r) => r.run_id === "old");
    expect(successFrom(result.current, old)).toBeNull();
    expect(notReadReason(result.current, old)).toBe(
      "Not read: this run has no recorded plan.",
    );
    expect(result.current.matrixError).toBe(false);
    const sent = matrixCalls(get);
    expect(sent).toHaveLength(4);
    sent.forEach((params) => {
      expect(params.baseline).toBe("none");
      const ids = params.runs.split(",");
      expect(ids.length).toBeLessThanOrEqual(8);
      expect(new Set(ids.map((id) => RUNS[id][0])).size).toBe(1);
      expect(ids).not.toContain("old");
    });
  });

  it("each read run also has its recorded per-task and time numbers", async () => {
    const { result } = renderHook(() => useLeaderboardNumbers(view), {
      wrapper,
    });
    await waitFor(() => expect(result.current.matrix).not.toBeNull());
    const a1 = result.current.byRun.a1.recorded;
    expect(a1.perTask.usd).toBeCloseTo(0.02, 10);
    expect(a1.time.ms).toBe(2000);
    // Two tries per row: three tasks, six tries.
    const d1 = result.current.byRun.d1.recorded;
    expect(d1.tasks).toBe(3);
    expect(d1.tries).toBe(6);
    expect(d1.perTask.usd).toBeCloseTo(0.02, 10);
    // Nothing recorded per try: no number.
    expect(result.current.byRun.c1.recorded.perTask.usd).toBeNull();
    expect(result.current.byRun.c1.recorded.time.ms).toBe(500);
    // The run that was never read has no recorded numbers at all.
    expect(result.current.byRun.old?.recorded).toBeUndefined();
  });

  it("a group whose read fails leaves the other groups' numbers on screen", async () => {
    failGroup = "group-d";
    const { result } = renderHook(() => useLeaderboardNumbers(view), {
      wrapper,
    });
    await waitFor(() => expect(result.current.matrix).not.toBeNull());
    await waitFor(() => expect(result.current.matrixError).toBe(true));
    expect(
      successFrom(
        result.current,
        runs.find((r) => r.run_id === "b1"),
      ),
    ).toMatchObject({ passed: 1, total: 3 });
    expect(result.current.byRun.d1?.recorded).toBeUndefined();
    // The failed group's run says its read failed, with no number.
    const d1 = runs.find((r) => r.run_id === "d1");
    expect(successFrom(result.current, d1)).toBeNull();
    expect(notReadReason(result.current, d1)).toBe(
      "Could not read this run's results. Try again.",
    );
  });

  it("a group of more than eight runs is read eight at a time", () => {
    const many = Array.from({ length: 11 }, (_, i) =>
      run(`m${String(i).padStart(2, "0")}`, "group-m"),
    );
    const reads = hook.matrixReadsOf([...many, run("x", "group-x")]);
    expect(reads.map((ids) => ids.length)).toEqual([8, 3, 1]);
    expect(reads.flat()).toHaveLength(12);
  });
});
