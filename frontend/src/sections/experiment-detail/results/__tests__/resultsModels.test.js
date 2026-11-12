import { describe, expect, it } from "vitest";
import {
  activeMovement,
  aggregateFor,
  buildResultColumns,
  columnMenuEntries,
  DEFAULT_HIDDEN,
  metricGroups,
} from "../resultsColumns";
import {
  FILTER_ENTRIES,
  defaultForm,
  entryDisabledReason,
  formComplete,
  pillFromForm,
  runOptions,
} from "../filterForm";
import { parseSort, sortLabel, sortString, splitSort } from "../sortModel";
import {
  defaultDescription,
  dialogTitleFor,
  noOpReason,
  r6Body,
  sourceKind,
  sourceRunFor,
  splitChipLabel,
  splitMembership,
  successText,
} from "../addToDatasetModel";
import {
  parseRunsParam,
  patchSearch,
  readResultsState,
} from "../useResultsUrlState";
import { buildGroupMenu } from "../groupMenu";

const A = "run-a";
const B = "run-b";
const runs = [
  { run_id: A, label: "#3A", trials: 1, target_type: "agent" },
  { run_id: B, label: "#4A", trials: 3, target_type: "agent" },
];
const tc = {
  key: "eval:task_completion",
  name: "Task completion",
  kind: "eval",
  output_type: "pass_fail",
  higher_is_better: true,
  versions: { [A]: "v1", [B]: "v1" },
  runs: [A, B],
};
const summary = {
  runs,
  metrics: [tc],
  human_metrics: [],
  system_metrics: [
    {
      key: "ops:latency",
      name: "Latency",
      kind: "system",
      output_type: "ms",
      higher_is_better: false,
    },
    {
      key: "ops:tokens",
      name: "Tokens",
      kind: "system",
      output_type: "count",
      higher_is_better: false,
    },
    {
      key: "agent:steps",
      name: "Steps",
      kind: "agent",
      output_type: "count",
      higher_is_better: false,
    },
    {
      key: "agent:tool_errors",
      name: "Tool errors",
      kind: "agent",
      output_type: "count",
      higher_is_better: false,
    },
  ],
  aggregates: {
    "eval:task_completion": {
      [A]: { avg: 0.777, n: 238 },
      [B]: {
        avg: 0.805,
        n: 246,
        delta: 0.025,
        ci95: 0.034,
        significant: false,
        improved: 14,
        regressed: 7,
        unchanged: 215,
        not_comparable: 12,
        n_shared: 236,
      },
    },
  },
  preference: {
    [B]: {
      wins: 24,
      ties: 6,
      losses: 10,
      win_rate: 0.675,
      judge: { wins: 27, ties: 5, losses: 16 },
    },
  },
  metadata_keys: ["intent"],
};
const runsById = Object.fromEntries(runs.map((r) => [r.run_id, r]));

describe("resultsColumns", () => {
  it("builds Row/Input pinned, Output per run, eval group baseline first", () => {
    const cols = buildResultColumns({
      summary,
      baselineId: A,
      runsById,
      pills: [],
    });
    expect(cols[0].colId).toBe("row");
    expect(cols.find((c) => c.colId === "input").pinned).toBe("left");
    const output = cols.find((c) => c.groupId === "output");
    expect(output.children.map((c) => c.cellRendererParams.runId)).toEqual([
      A,
      B,
    ]);
    const group = cols.find((c) => c.groupId === "eval:task_completion");
    const [base, cand] = group.children;
    expect(base.headerComponentParams.isBaseline).toBe(true);
    expect(base.headerComponentParams.delta).toBeUndefined();
    expect(cand.headerComponentParams.delta.delta).toBe(0.025);
    expect(cand.headerComponentParams.movement.regressed).toBe(7);
    // Tokens and metadata start hidden; only agent:steps of the agent family shows.
    expect(cols.some((c) => c.groupId === "ops:tokens")).toBe(false);
    expect(cols.some((c) => c.groupId === "agent:tool_errors")).toBe(false);
    expect(cols.some((c) => c.groupId === "agent:steps")).toBe(true);
    expect(cols.some((c) => c.colId === "meta:intent")).toBe(false);
    expect(cols.some((c) => c.groupId === "preference")).toBe(true);
  });

  it("a single run has no deltas, no Baseline badge and no Preference", () => {
    const one = { ...summary, runs: [runs[1]], preference: {} };
    const cols = buildResultColumns({
      summary: one,
      baselineId: B,
      runsById,
      pills: [],
    });
    const group = cols.find((c) => c.groupId === "eval:task_completion");
    expect(group.children).toHaveLength(1);
    expect(group.children[0].headerComponentParams.isBaseline).toBe(false);
    expect(group.children[0].headerComponentParams.movement).toBeUndefined();
    expect(cols.some((c) => c.groupId === "preference")).toBe(false);
  });

  it("marks the active movement direction from the pills", () => {
    const pills = [
      { type: "movement", metric: tc.key, dir: "regressed", run: B },
    ];
    expect(activeMovement(pills, tc.key, B)).toBe("regressed");
    expect(activeMovement(pills, tc.key, A)).toBe(null);
  });

  it("aggregate follows the Aggregate mode; cost sums, latency is p50", () => {
    expect(
      aggregateFor(tc, { avg: 0.8, median: 1, pass_rate: 0.8 }, "median"),
    ).toEqual({ value: 1, agg: "median" });
    expect(
      aggregateFor({ key: "ops:cost" }, { sum: 1.2, avg: 0.005 }, "avg"),
    ).toEqual({ value: 0.005, agg: "avg" });
    expect(
      aggregateFor({ key: "ops:latency" }, { avg: 3, p50: 2.9 }, "median").agg,
    ).toBe("p50");
  });

  it("Columns… lists Example then per-run groups", () => {
    const { example, perRun } = columnMenuEntries(summary);
    expect(example.map((e) => e.key)).toContain("expected");
    expect(perRun[0].key).toBe("output");
    expect(perRun.map((p) => p.key)).toContain("preference");
    expect(metricGroups(summary).map((g) => g.key)).toContain("ops:tokens");
    expect(DEFAULT_HIDDEN).toContain("ops:tokens");
  });
});

describe("filterForm", () => {
  const view = {
    runs,
    runCount: 2,
    baselineId: A,
    metrics: [
      tc,
      {
        key: "eval:groundedness",
        name: "Groundedness",
        output_type: "score",
        pass_threshold: 0.7,
      },
    ],
    humanMetrics: [],
    splits: [{ name: "hard" }],
    metadataKeys: ["intent"],
    hasTrials: true,
    hasPreference: true,
    hasAgent: true,
    candidateId: B,
  };
  const entry = (id) => FILTER_ENTRIES.find((e) => e.id === id);

  it("regressed defaults to any candidate and makes a movement pill", () => {
    const form = defaultForm(entry("regressed"), view);
    expect(form.run).toBe("any_candidate");
    expect(pillFromForm(entry("regressed"), { ...form, run: B })).toEqual({
      type: "movement",
      metric: tc.key,
      dir: "regressed",
      run: B,
    });
  });

  it("score threshold picks a score metric and its pass mark", () => {
    const form = defaultForm(entry("score"), view);
    expect(form.metric).toBe("eval:groundedness");
    expect(form.value).toBe(0.7);
    expect(pillFromForm(entry("score"), form)).toMatchObject({
      type: "score",
      op: "lt",
      value: 0.7,
    });
  });

  it("latency is entered in seconds and sent in ms", () => {
    const pill = pillFromForm(entry("ops"), {
      opsKind: "latency",
      op: "gt",
      value: "3",
      run: B,
    });
    expect(pill).toEqual({ type: "latency", op: "gt", value: 3000, run: B });
  });

  it("disables compare-only entries with one run", () => {
    expect(
      entryDisabledReason(entry("improved"), { ...view, runCount: 1 }),
    ).toMatch(/second run/);
    expect(entryDisabledReason(entry("human"), view)).toMatch(
      /No human labels/,
    );
    expect(formComplete(entry("split"), { splits: [] })).toBe(false);
    expect(formComplete(entry("split"), { splits: ["hard"] })).toBe(true);
    expect(
      runOptions(runs, A, { candidatesOnly: true }).map((o) => o.value),
    ).toEqual(["any_candidate", B]);
  });
});

describe("sortModel", () => {
  it("round-trips sort strings whose metric key has a colon", () => {
    const s = sortString("regression", { metric: "eval:correctness", run: B });
    expect(s).toBe(`delta:eval:correctness:${B}:asc`);
    expect(splitSort(s)).toEqual({
      kind: "delta",
      metric: "eval:correctness",
      run: B,
      dir: "asc",
    });
    expect(parseSort(s)).toEqual({
      entryId: "regression",
      metric: "eval:correctness",
      run: B,
    });
    expect(parseSort(`latency:${B}`)).toEqual({ entryId: "latency", run: B });
    expect(parseSort("nonsense")).toEqual({ entryId: "order" });
  });
  it("labels the button", () => {
    expect(sortLabel("order")).toBe("Sort");
    expect(
      sortLabel(`delta:eval:task_completion:${B}:asc`, {
        metricsByKey: { [tc.key]: tc },
        runsById,
      }),
    ).toBe("Sort: Biggest regression · Task completion · #4A");
  });
});

describe("addToDatasetModel", () => {
  const pills = [
    { type: "movement", metric: tc.key, dir: "regressed", run: B },
  ];
  it("takes the run and kind from the movement pill", () => {
    expect(sourceRunFor({ pills, runs, baselineId: A })).toBe(B);
    expect(sourceKind(pills, B)).toBe("regressions");
    expect(
      defaultDescription({
        count: 7,
        runLabel: "#4A",
        kind: "regressions",
        splitNames: ["hard"],
        target: "same",
      }),
    ).toBe("Tagged 7 rows as hard from #4A regressions");
    // iterate r1 G3 (P7): picked results read `review`; nothing picked →
    // no note that claims a change.
    expect(
      defaultDescription({
        count: 3,
        runLabel: "#2G",
        kind: "results",
        splitNames: ["hard"],
        target: "same",
      }),
    ).toBe("Tagged 3 rows as hard from #2G review");
    expect(
      defaultDescription({
        count: 3,
        runLabel: "#2G",
        kind: "results",
        splitNames: [],
        target: "same",
      }),
    ).toBe("");
    expect(dialogTitleFor("same", 3)).toBe("Tag 3 rows with a split");
    expect(dialogTitleFor("other", 3)).toBe("Add to dataset");
  });
  it("says a no-op before the click (iterate r2 G3 P6)", () => {
    const splits = [
      { id: "H", name: "hard" },
      { id: "R", name: "regression" },
    ];
    const rows = [
      { split_ids: ["H"] },
      { split_ids: ["H"] },
      { split_ids: ["H", "R"] },
    ];
    const m = splitMembership(rows, splits);
    expect(m).toEqual({ H: 3, R: 1 });
    expect(splitChipLabel("hard", m.H, 3)).toBe("3 of 3 already in hard");
    expect(splitChipLabel("smoke", 0, 3)).toBe("smoke");
    const base = {
      count: 3,
      newSplits: [],
      updateExpected: false,
      membership: m,
    };
    expect(noOpReason({ ...base, splitIds: ["H"], splitNames: ["hard"] })).toBe(
      "All 3 rows are already tagged hard — pick another split",
    );
    expect(
      noOpReason({ ...base, splitIds: ["R"], splitNames: ["regression"] }),
    ).toBeNull();
    expect(
      noOpReason({
        ...base,
        splitIds: ["H"],
        splitNames: ["hard"],
        updateExpected: true,
      }),
    ).toBeNull();
    expect(
      successText(
        { split_changes: 0, rows_updated: 0, version: { number: 7 } },
        { splitNames: [], target: "same" },
      ),
    ).toBe("Saved v7 · no rows tagged");
    expect(
      r6Body({
        datasetId: "DS",
        runId: B,
        exampleIds: ["r"],
        target: "same",
        splitIds: [],
        newSplits: [],
        description: "note",
        saveVersion: true,
      }).save_version,
    ).toBe(true);
  });
  it("words the toast like the spec", () => {
    expect(
      successText(
        { split_changes: 6, already_in_split: 1, version: { number: 7 } },
        { splitNames: ["hard"], target: "same" },
      ),
    ).toBe("Saved v7 · 6 rows tagged hard (1 was already in it)");
    expect(
      successText(
        { rows_added: 2, version: { number: 1 } },
        { target: "new", datasetName: "Misses" },
      ),
    ).toBe("2 rows added to Misses · v1");
  });
  it("builds the R6 body per target", () => {
    const same = r6Body({
      datasetId: "DS",
      runId: B,
      exampleIds: ["r"],
      target: "same",
      splitIds: ["S"],
      newSplits: ["x"],
      updateExpected: true,
      expectedFrom: "keep",
      description: "d",
    });
    expect(same).toMatchObject({
      mode: "same_dataset",
      split_ids: ["S"],
      new_splits: [{ name: "x" }],
      expected_from: "output",
    });
    const other = r6Body({
      datasetId: "DS",
      runId: B,
      exampleIds: ["r"],
      target: "other",
      splitIds: ["S"],
      newSplits: [],
      updateExpected: true,
      expectedFrom: "none",
      description: "d",
    });
    expect(other).toMatchObject({
      mode: "copy",
      split_ids: [],
      expected_from: "none",
    });
  });
});

describe("useResultsUrlState helpers", () => {
  it("reads defaults and caps runs at 8", () => {
    const s = readResultsState(
      new URLSearchParams("runs=a,b,a,c,d,e,f,g,h,i&diff=nope&trial=3"),
    );
    expect(s.runIds).toHaveLength(8);
    expect(s.diff).toBe("off");
    expect(s.trial).toBe(3);
    expect(s.dtab).toBe("compare");
    expect(parseRunsParam("")).toEqual([]);
  });
  it("patches a query string, dropping empty values", () => {
    const next = patchSearch("?a=1&b=2", { a: null, c: "x", d: "" });
    expect(next.toString()).toBe("b=2&c=x");
  });
});

describe("groupMenu", () => {
  it("offers aggregate, sort, reasons, direction and hide for an eval", () => {
    const items = buildGroupMenu(
      { key: tc.key, kind: "eval", def: tc },
      {
        agg: "avg",
        setAgg: () => {},
        firstCandidate: B,
        multiRun: true,
        showReasons: false,
        toggleReasons: () => {},
        hide: () => {},
        sortBy: () => {},
      },
    );
    const labels = items.map((i) => i.label);
    // L7c g6 (agg:basic): `Pass rate` is not offered (one pass rate).
    expect(labels).not.toContain("Pass rate");
    expect(labels).toEqual(
      expect.arrayContaining([
        "Average",
        "Median",
        "Sort by biggest regression",
        "Show reasons",
        "Higher is better",
        "Hide column",
      ]),
    );
    expect(items.find((i) => i.label === "Average").selected).toBe(true);
    expect(items.find((i) => i.label === "Higher is better").disabled).toBe(
      true,
    );
  });
});

describe("iterate r1 G3 (P8) frozen score columns", () => {
  const summaryOf = (runs, evals) => ({
    runs: Array.from({ length: runs }, (_, i) => ({ run_id: `r${i}` })),
    metrics: Array.from({ length: evals }, (_, i) => ({
      key: `eval:e${i}`,
      name: `E${i}`,
      kind: "eval",
      output_type: "Pass/Fail",
    })),
  });
  it("freezes every eval when it fits, else the primary, else none", async () => {
    const { scorePinFor } = await import("../resultsColumns");
    const s = summaryOf(2, 2);
    expect(scorePinFor(summaryOf(1, 1), { gridWidth: 1680 })).toBe("all");
    // iterate r2 G3 (P9): every eval frozen only within 40 % of the grid
    // (row + input + scores): 2 runs × 2 evals at 1680 → the primary only.
    expect(scorePinFor(s, { gridWidth: 1680, primaryKey: "eval:e0" })).toBe(
      "primary",
    );
    expect(scorePinFor(s, { gridWidth: 1200, primaryKey: "eval:e0" })).toBe(
      "primary",
    );
    expect(
      scorePinFor(summaryOf(6, 2), { gridWidth: 1040, primaryKey: "eval:e0" }),
    ).toBeNull();
    expect(scorePinFor(summaryOf(2, 0), { gridWidth: 1680 })).toBeNull();
  });
});
