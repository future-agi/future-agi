// Pure leaderboard rules: column options across datasets,
// sort normalisation, ranking with no-value runs left out, definition
// errors, the left-out line and the board link; no backend route is available.
import { describe, expect, it } from "vitest";
import {
  boardCountsText,
  columnOptions,
  definitionErrors,
  isFinishedRun,
  leaderboardHref,
  leftOutText,
  rulePlaceholder,
  normalizeSort,
  rankRows,
} from "../leaderboardModel";

const def = (key, extra = {}) => ({
  key,
  name: key.split(":")[1],
  kind: "eval",
  ...extra,
});

describe("w5-c leaderboards: columns and sort", () => {
  it("unions metric defs across datasets, first-seen order, evals and system numbers only", () => {
    const cols = columnOptions([
      [
        def("eval:correctness"),
        def("eval:tone"),
        { key: "human:q", kind: "human" },
      ],
      [
        def("eval:tone"),
        {
          key: "ops:cost",
          name: "Cost",
          kind: "system",
          higher_is_better: false,
        },
      ],
    ]);
    expect(cols.map((c) => c.key)).toEqual([
      "eval:correctness",
      "eval:tone",
      "ops:cost",
    ]);
    expect(cols.map((c) => c.datasets)).toEqual([1, 2, 1]);
    expect(cols[0].label).toBe("correctness");
  });

  it("sorts by the first column, best first by the metric's direction", () => {
    const columns = [{ key: "ops:cost" }, { key: "eval:tone" }];
    const byKey = {
      "ops:cost": { higher_is_better: false },
      "eval:tone": { higher_is_better: true },
    };
    expect(normalizeSort(null, columns, byKey)).toEqual({
      column: "ops:cost",
      dir: "asc",
    });
    expect(normalizeSort({ column: "eval:tone" }, columns, byKey)).toEqual({
      column: "eval:tone",
      dir: "desc",
    });
    expect(
      normalizeSort({ column: "gone", dir: "asc" }, columns, byKey),
    ).toEqual({ column: "ops:cost", dir: "asc" });
    expect(normalizeSort(null, [], {})).toEqual({ column: null, dir: "desc" });
  });
});

describe("w5-c leaderboards: ranking", () => {
  const rows = [
    { run_id: "a", label: "#1A", dataset: "GS", values: { k: 0.5 } },
    { run_id: "b", label: "#2A", dataset: "GS", values: { k: 0.9 } },
    { run_id: "c", label: "#1A", dataset: "TS", values: { k: null } },
    { run_id: "d", label: "#3A", dataset: "GS", values: { k: 0.9 } },
    { run_id: "e", label: "#1B", dataset: "TS", values: {} },
  ];
  it("ranks by value, ties by dataset then label, no-value rows left out", () => {
    const res = rankRows(rows, { column: "k", dir: "desc" });
    expect(res.rows.map((r) => [r.rank, r.run_id])).toEqual([
      [1, "b"],
      [2, "d"],
      [3, "a"],
    ]);
    expect(res.noValue.map((r) => r.run_id)).toEqual(["c", "e"]);
    expect(rankRows(rows, { column: "k", dir: "asc" }).rows[0].run_id).toBe(
      "a",
    );
  });

  it("finished = Completed or PartialCompleted", () => {
    expect(isFinishedRun({ status: "Completed" })).toBe(true);
    expect(isFinishedRun({ status: "partial" })).toBe(true);
    expect(isFinishedRun({ status: "Running" })).toBe(false);
    expect(isFinishedRun({ status: "Failed" })).toBe(false);
  });
});

describe("w5-c leaderboards: definitions and copy", () => {
  it("names every missing field, and unknown datasets and columns", () => {
    expect(Object.keys(definitionErrors({}))).toEqual([
      "name",
      "dataset_ids",
      "columns",
    ]);
    const errs = definitionErrors(
      {
        name: "B",
        dataset_ids: ["x", "y"],
        columns: [{ key: "eval:a" }, { key: "eval:z" }],
        sort: { column: "eval:q" },
      },
      { datasetIds: new Set(["x"]), columnKeys: new Set(["eval:a"]) },
    );
    expect(errs.dataset_ids).toBe("Unknown dataset: y");
    expect(errs.columns).toBe("These datasets have no eval:z.");
    expect(errs.sort).toBe("Sort by one of the columns.");
    expect(
      definitionErrors({
        name: "B",
        dataset_ids: ["x"],
        columns: [{ key: "eval:a" }],
        sort: { column: "eval:a" },
      }),
    ).toEqual({});
  });

  it("says what was left out, and counts runs and datasets", () => {
    expect(leftOutText({ no_value: 0, not_finished: 0 }, "Tone")).toBeNull();
    expect(leftOutText({ no_value: 2, not_finished: 1 }, "Tone")).toBe(
      "2 matching runs with no Tone value, listed unranked · 1 not finished yet, left out",
    );
    expect(leftOutText({ no_value: 1 }, "Tone")).toBe(
      "1 matching run with no Tone value, listed unranked",
    );
    expect(leftOutText({ not_finished: 3 }, "Tone")).toBe(
      "3 not finished yet, left out",
    );
    expect(boardCountsText({ rows: 1, datasets: 2 })).toBe(
      "1 run · 2 datasets",
    );
  });

  it("the rule placeholder names a tag the workspace has (wlb:editor)", () => {
    expect(rulePlaceholder([{ name: "baseline" }, { name: "x" }])).toBe(
      'tags contains "baseline"',
    );
    expect(rulePlaceholder([])).toBe("cost_usd < 1");
    expect(rulePlaceholder(undefined)).toBe("cost_usd < 1");
  });

  it("offers no API call: the real API serves no leaderboard route", async () => {
    const model = await import("../leaderboardModel");
    expect(model.leaderboardApiCall).toBeUndefined();
    expect(leaderboardHref("lb-1")).toBe(
      "/dashboard/develop/leaderboards/lb-1",
    );
  });
});
