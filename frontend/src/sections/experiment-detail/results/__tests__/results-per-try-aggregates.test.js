// The per-try aggregates of a column's Aggregate menu (Most common, Best,
// pass@k, pass^k, At least n of k) with the summary the results read serves.
// A run in view plays three tries per row (the metric's `trials_k`), and each
// run's summary entry carries `reducers`, keyed by the value the page sends.
// Where the read's published query contract lists them, each is on in the
// column menu, choosing one sets it, the page asks the read for it and reads
// its link as named, and each run's header shows the value the read serves,
// with its interval. Under Average and by default each header keeps the
// run's average. A per-try aggregate the read answers with no value shows
// unknown, never the average. Where the contract lists only the mean and the
// median, they stay in the menu, turned off with the shared line.
import * as zod from "zod";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { formatValue } from "src/sections/common/Metrics/metricFormat";

// null reads the published contract itself.
const contract = vi.hoisted(() => ({ agg: null }));

vi.mock("src/generated/api-contracts/api.zod", async (importOriginal) => {
  const actual = await importOriginal();
  const real = actual.DatasetResultsReadQueryParams;
  return {
    ...actual,
    DatasetResultsReadQueryParams: {
      ...real,
      shape: {
        ...real.shape,
        get agg() {
          return contract.agg ?? real.shape.agg;
        },
      },
    },
  };
});

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    develop: {
      datasetRuns: {
        results: (id) => `/model-hub/datasets/${id}/results/`,
        list: (id) => `/model-hub/datasets/${id}/runs/`,
      },
    },
  },
}));

import { runResultsParams } from "src/api/develop/run-results";
import { buildResultColumns } from "../resultsColumns";
import { buildGroupMenu } from "../groupMenu";
import { readResultsState } from "../useResultsUrlState";

// What the read accepts (DatasetResultsQuerySerializer.agg): the mean, the
// median, the per-try aggregates and at least n for n from 1 to 100.
const PER_TRY = ["mode", "max", "pass_at_k", "pass_hat_k", "at_least:2"];
const AGGS = ["mean", "median", "mode", "max", "pass_at_k", "pass_hat_k"];
const AT_LEAST = Array.from({ length: 100 }, (_, i) => `at_least:${i + 1}`);
const everyAggregate = () => zod.enum([...AGGS, ...AT_LEAST]).default("mean");
const meanAndMedian = () => zod.enum(["mean", "median"]).default("mean");

const A = "run-a";
const B = "run-b";
const RUNS = [
  { run_id: A, label: "#1A", trials: 3 },
  { run_id: B, label: "#1B", trials: 3 },
];
const runsById = Object.fromEntries(RUNS.map((r) => [r.run_id, r]));
const DEF = {
  key: "eval:correct",
  name: "Correct",
  output_type: "pass_fail",
  higher_is_better: true,
  runs: [A, B],
  versions: {},
  trials_k: 3,
};
const share = (value, ci95) => ({ value, n: 4, ci95 });
// Each run's entry as the read serves it: the assessed-example mean, the
// median, and the per-try aggregates with their intervals.
const entry = (value, reducers) => ({
  value,
  n: 4,
  rule: "assessed_example_mean",
  total: 4,
  median: value,
  reducers: {
    "at_least:1": reducers.pass_at_k,
    "at_least:3": share(reducers.pass_hat_k.value, [0, 0.5]),
    ...reducers,
  },
});
const SUMMARY = {
  runs: RUNS,
  metrics: [DEF],
  human_metrics: [],
  system_metrics: [],
  aggregates: {
    [DEF.key]: {
      [A]: entry(0.6, {
        mode: share(0.5, [0.15, 0.85]),
        max: share(1, [0.51, 1]),
        pass_at_k: share(0.75, [0.3, 0.95]),
        pass_hat_k: share(0.25, [0.05, 0.7]),
        "at_least:2": share(0.5, [0.15, 0.85]),
      }),
      [B]: entry(0.3, {
        mode: share(0.25, [0.05, 0.7]),
        max: share(0.5, [0.15, 0.85]),
        pass_at_k: share(0.5, [0.15, 0.85]),
        pass_hat_k: share(0, [0, 0.49]),
        "at_least:2": share(0.25, [0.05, 0.7]),
      }),
    },
  },
  examples_total: 4,
  examples_in_view: 4,
  baseline_run_id: A,
};

const headerOf = (cols, runId) =>
  cols
    .find((c) => c.groupId === DEF.key)
    .children.find((c) => c.cellRendererParams.runId === runId)
    .headerComponentParams;

const columns = (agg) =>
  buildResultColumns({ summary: SUMMARY, baselineId: A, runsById, agg });

const menu = (setAgg) =>
  buildGroupMenu(
    { key: DEF.key, kind: "eval", def: DEF },
    {
      agg: "run",
      setAgg,
      firstCandidate: B,
      multiRun: true,
      showReasons: false,
      toggleReasons: () => {},
      hide: () => {},
      sortBy: () => {},
    },
  );

// The menu's Aggregate items: after the heading, before the sort items.
const aggregateItems = (items) => {
  const end = items.findIndex((i) => i.label === "Sort by biggest regression");
  return items.slice(1, end);
};
const PER_TRY_LABELS = {
  mode: "Most common",
  max: "Best",
  pass_at_k: "pass@3",
  pass_hat_k: "pass^3",
  "at_least:2": "At least 2 of 3",
};

beforeEach(() => {
  setLaunchOverrideForTest({ launch: true });
  contract.agg = everyAggregate();
});
afterEach(() => {
  setLaunchOverrideForTest(null);
  contract.agg = null;
});

describe("The per-try aggregates where the read's contract lists them", () => {
  it("are on in the column menu, and choosing one sets it", () => {
    const setAgg = vi.fn();
    const items = aggregateItems(menu(setAgg));
    expect(items.map((i) => i.label)).toEqual([
      "Average",
      "Median",
      ...Object.values(PER_TRY_LABELS),
    ]);
    for (const [agg, label] of Object.entries(PER_TRY_LABELS)) {
      const item = items.find((i) => i.label === label);
      expect(item.disabled).toBeFalsy();
      expect(item.secondary).toBeUndefined();
      item.onClick();
      expect(setAgg).toHaveBeenLastCalledWith(agg);
    }
  });

  it.each(PER_TRY)("sends agg=%s and reads its link as is", (agg) => {
    expect(runResultsParams({ runIds: [A, B], agg }).agg).toBe(agg);
    expect(readResultsState(new URLSearchParams({ agg })).agg).toBe(agg);
  });

  it("reads a link to at least n beyond what the read answers as Average", () => {
    const agg = "at_least:101";
    expect(runResultsParams({ runIds: [A, B], agg }).agg).toBeUndefined();
    expect(readResultsState(new URLSearchParams({ agg })).agg).toBe("avg");
  });

  it.each(PER_TRY)(
    "shows each run's %s from the read in its header, with its interval",
    (agg) => {
      const cols = columns(agg);
      for (const runId of [A, B]) {
        const served = SUMMARY.aggregates[DEF.key][runId].reducers[agg];
        const header = headerOf(cols, runId);
        expect(header.aggregate).toEqual({
          value: served.value,
          agg: "pass_rate",
          ci95: served.ci95,
          reducer: agg,
        });
        expect(
          formatValue(DEF, header.aggregate.value, {
            agg: header.aggregate.agg,
          }),
        ).toBe(formatValue(DEF, served.value, { agg: "pass_rate" }));
      }
    },
  );

  it.each([
    ["Average", "avg"],
    ["the default", "run"],
  ])("leave each run's average under %s", (_, agg) => {
    const cols = columns(agg);
    expect(headerOf(cols, A).aggregate.value).toBe(0.6);
    expect(headerOf(cols, B).aggregate.value).toBe(0.3);
  });
});

describe("A per-try aggregate the read answers with no value", () => {
  // Three tries per row. The one row in view has one try assessed a pass
  // and two not finished, so its average is 1, while pass^3 has no row to
  // count: the read answers it with no value.
  const unknown = { value: null, n: 0, ci95: null };
  const ofOne = (value, ci95) => ({ value, n: 1, ci95 });
  const oneRow = {
    value: 1,
    n: 1,
    rule: "assessed_example_mean",
    total: 1,
    median: 1,
    reducers: {
      mode: ofOne(1, [0.21, 1]),
      max: ofOne(1, [0.21, 1]),
      pass_at_k: ofOne(1, [0.21, 1]),
      pass_hat_k: unknown,
      "at_least:1": ofOne(1, [0.21, 1]),
      "at_least:2": ofOne(0, [0, 0.79]),
      "at_least:3": ofOne(0, [0, 0.79]),
    },
  };
  const UNFINISHED = {
    ...SUMMARY,
    aggregates: { [DEF.key]: { [A]: oneRow, [B]: oneRow } },
    examples_total: 1,
    examples_in_view: 1,
  };
  const unfinishedColumns = (agg) =>
    buildResultColumns({ summary: UNFINISHED, baselineId: A, runsById, agg });

  it("shows pass^3 as unknown in each run's header, not the average", () => {
    const cols = unfinishedColumns("pass_hat_k");
    for (const runId of [A, B]) {
      const header = headerOf(cols, runId);
      expect(header.aggregate).toEqual({
        value: null,
        agg: "pass_rate",
        ci95: null,
        reducer: "pass_hat_k",
      });
      const shown = formatValue(DEF, header.aggregate.value, {
        agg: header.aggregate.agg,
      });
      expect(shown).toBe(formatValue(DEF, null));
      expect(shown).not.toBe(formatValue(DEF, 1, { agg: "pass_rate" }));
      expect(header.delta).toBeUndefined();
    }
  });

  it("keeps the average under Average", () => {
    const header = headerOf(unfinishedColumns("avg"), A);
    expect(header.aggregate.value).toBe(1);
    expect(header.aggregate.agg).toBe("avg");
  });

  it("shows at least n beyond the run's tries as unknown, not the average", () => {
    for (const runId of [A, B]) {
      const header = headerOf(unfinishedColumns("at_least:4"), runId);
      expect(header.aggregate).toEqual({
        value: null,
        agg: "pass_rate",
        ci95: null,
        reducer: "at_least:4",
      });
    }
  });
});

describe("The per-try aggregates where the read's contract lists only the mean and the median", () => {
  beforeEach(() => {
    contract.agg = meanAndMedian();
  });

  it("stay in the column menu, turned off with the shared line", () => {
    const setAgg = vi.fn();
    const items = aggregateItems(menu(setAgg));
    for (const label of Object.values(PER_TRY_LABELS)) {
      const item = items.find((i) => i.label === label);
      expect(item.disabled).toBe(true);
      expect(item.secondary).toBe(NOT_IN_RELEASE);
      item.onClick();
    }
    expect(setAgg).not.toHaveBeenCalled();
    for (const agg of PER_TRY)
      expect(runResultsParams({ runIds: [A, B], agg }).agg).toBeUndefined();
  });
});

describe("The per-try aggregates with the published contract", () => {
  beforeEach(() => {
    contract.agg = null;
  });

  it("are on in the column menu and asked of the read", () => {
    const items = aggregateItems(menu(vi.fn()));
    for (const label of Object.values(PER_TRY_LABELS)) {
      const item = items.find((i) => i.label === label);
      expect(item.disabled).toBeFalsy();
      expect(item.secondary).toBeUndefined();
    }
    for (const agg of PER_TRY)
      expect(runResultsParams({ runIds: [A, B], agg }).agg).toBe(agg);
  });
});
