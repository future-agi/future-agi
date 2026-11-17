// The results grid's column headers and column menu with the summary the
// results read serves. Under Average (and by default) each run's header shows
// the run's average. Where this build's read does not answer Median, N/A rate
// or a per-try reducer, each stays in the column menu, turned off with the
// shared line, and choosing it sets nothing; Average stays on. Where the
// read's published query contract lists the Median, Median works and each
// run's header shows the median the read serves. Where the read answers
// them all, the menu works as before.
import * as zod from "zod";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { formatValue } from "src/sections/common/Metrics/metricFormat";
import { buildResultColumns } from "../resultsColumns";
import { buildGroupMenu } from "../groupMenu";

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
          return contract.agg;
        },
      },
    },
  };
});

// The read's aggregate choices without and with the Median.
const meanOnly = () => zod.enum(["mean"]).default("mean");
const withMedian = () => zod.enum(["mean", "median"]).default("mean");

const A = "run-a";
const B = "run-b";
const RUNS = [
  { run_id: A, label: "#1A" },
  { run_id: B, label: "#1B" },
];
const runsById = Object.fromEntries(RUNS.map((r) => [r.run_id, r]));
const DEF = {
  key: "eval:correctness",
  name: "Correctness",
  output_type: "score",
  higher_is_better: true,
  runs: [A, B],
  versions: {},
  answers_na: true,
  trials_k: 3,
};
// The summary as the read serves it: each run's assessed-example mean, and
// the median of the same row values.
const SUMMARY = {
  runs: RUNS,
  metrics: [DEF],
  human_metrics: [],
  system_metrics: [],
  aggregates: {
    [DEF.key]: {
      // Rows 0.65, 0.8, 0.8 and 0.4, 0.4, 0.7.
      [A]: {
        value: 0.75,
        n: 3,
        rule: "assessed_example_mean",
        total: 3,
        median: 0.8,
      },
      [B]: {
        value: 0.5,
        n: 3,
        rule: "assessed_example_mean",
        total: 3,
        median: 0.4,
      },
    },
  },
  examples_total: 3,
  examples_in_view: 3,
  baseline_run_id: A,
};

const headerOf = (cols, runId) =>
  cols
    .find((c) => c.groupId === DEF.key)
    .children.find((c) => c.cellRendererParams.runId === runId)
    .headerComponentParams;

const shownIn = (header) =>
  formatValue(DEF, header.aggregate?.value, { agg: header.aggregate?.agg });

beforeEach(() => {
  contract.agg = meanOnly();
});
afterEach(() => setLaunchOverrideForTest(null));

describe("A run's column header with the read's summary", () => {
  it.each([
    ["Average", "avg"],
    ["the default", "run"],
  ])("shows each run's average under %s", (_, agg) => {
    const cols = buildResultColumns({
      summary: SUMMARY,
      baselineId: A,
      runsById,
      agg,
    });
    for (const [runId, value] of [
      [A, 0.75],
      [B, 0.5],
    ]) {
      const shown = shownIn(headerOf(cols, runId));
      expect(shown).not.toBe(formatValue(DEF, null));
      expect(shown).toBe(formatValue(DEF, value, { agg: "avg" }));
    }
  });
});

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
  const list = items.slice(1, end);
  return {
    average: list.find((i) => i.label === "Average"),
    others: list.filter((i) => i.label !== "Average"),
  };
};

describe("Median, N/A rate and the per-try reducers where this build's read does not answer them", () => {
  it("stay in the column menu, turned off with the shared line", () => {
    setLaunchOverrideForTest({ launch: true });
    const setAgg = vi.fn();
    const { others } = aggregateItems(menu(setAgg));
    expect(others.map((i) => i.label)).toEqual(
      expect.arrayContaining(["Median", "N/A rate"]),
    );
    // Median, N/A rate and five per-try reducers at three tries per row.
    expect(others).toHaveLength(7);
    for (const item of others) {
      expect(item.disabled).toBe(true);
      expect(item.secondary).toBe(NOT_IN_RELEASE);
      item.onClick();
    }
    expect(setAgg).not.toHaveBeenCalled();
  });

  it("keep Average on", () => {
    setLaunchOverrideForTest({ launch: true });
    const setAgg = vi.fn();
    const { average } = aggregateItems(menu(setAgg));
    expect(average.disabled).toBeFalsy();
    expect(average.secondary).toBeUndefined();
    average.onClick();
    expect(setAgg).toHaveBeenCalledWith("avg");
  });
});

describe("Median, N/A rate and the per-try reducers where the read answers them", () => {
  it("work as before", () => {
    const setAgg = vi.fn();
    const { others } = aggregateItems(menu(setAgg));
    expect(others).toHaveLength(7);
    for (const item of others) {
      expect(item.disabled).toBeFalsy();
      expect(item.secondary).toBeUndefined();
    }
    others.find((i) => i.label === "Median").onClick();
    expect(setAgg).toHaveBeenCalledWith("median");
  });
});

describe("The Median where the read's contract lists it", () => {
  beforeEach(() => {
    setLaunchOverrideForTest({ launch: true });
    contract.agg = withMedian();
  });

  it("is on in the column menu, and the reducers the read does not answer stay off", () => {
    const setAgg = vi.fn();
    const { others } = aggregateItems(menu(setAgg));
    const median = others.find((i) => i.label === "Median");
    expect(median.disabled).toBeFalsy();
    expect(median.secondary).toBeUndefined();
    median.onClick();
    expect(setAgg).toHaveBeenCalledWith("median");
    const rest = others.filter((i) => i !== median);
    expect(rest).toHaveLength(6);
    for (const item of rest) {
      expect(item.disabled).toBe(true);
      expect(item.secondary).toBe(NOT_IN_RELEASE);
    }
  });

  it("shows each run's median in its header", () => {
    const cols = buildResultColumns({
      summary: SUMMARY,
      baselineId: A,
      runsById,
      agg: "median",
    });
    for (const [runId, value] of [
      [A, 0.8],
      [B, 0.4],
    ])
      expect(shownIn(headerOf(cols, runId))).toBe(
        formatValue(DEF, value, { agg: "median" }),
      );
  });
});
