import { describe, expect, it } from "vitest";
import { buildByDataset, byCellText, headlineKey } from "../byDatasetModel";
import { hopLabel, trendPoints } from "../workbenchSeries";
import {
  failedEvalNote,
  failedEvalSummary,
  spreadSentence,
} from "../workbenchStats";

const agent = (version) => ({
  kind: "agent",
  agent: { graph_id: "g1", name: "Support Agent", version },
});
const run = (id, label, version, ds, extra = {}) => ({
  run_id: id,
  label,
  name: `Support Agent ${version}`,
  status: "Completed",
  created_at: extra.created_at ?? "2026-10-10T00:00:00Z",
  trials: extra.trials ?? 1,
  metric_keys: [extra.key ?? "eval:correctness"],
  config: agent(version),
  metrics: {
    aggregates: {
      [extra.key ?? "eval:correctness"]: {
        avg: extra.avg,
        n: extra.n ?? 238,
        sum: extra.sum,
      },
    },
  },
  dataset_id: ds,
});

describe("By dataset", () => {
  const entries = [
    {
      dataset: { datasetId: "d1", name: "Golden set" },
      runs: [
        run("3a", "#3A", "v2", "d1", { avg: 0.803 }),
        run("4a", "#4A", "v3", "d1", {
          avg: 0.821,
          trials: 3,
          created_at: "2026-10-12T00:00:00Z",
        }),
      ],
    },
    {
      dataset: { datasetId: "d6", name: "Tasks" },
      runs: [
        run("2b", "#2B", "v3", "d6", {
          avg: 0.656,
          trials: 3,
          n: 30,
          key: "eval:task_success",
          created_at: "2026-10-15T00:00:00Z",
        }),
        run("1b", "#1B", "v3", "d6", {
          avg: 0.667,
          trials: 3,
          n: 24,
          key: "eval:task_success",
        }),
      ],
    },
    {
      dataset: { datasetId: "d7", name: "Sessions" },
      runs: [
        run("1a7", "#1A", "v2", "d7", {
          avg: 12 / 18,
          n: 18,
          sum: 12,
          key: "eval:resolution",
        }),
        run("1b7", "#1B", "v3", "d7", {
          avg: 13 / 18,
          n: 18,
          sum: 13,
          key: "eval:resolution",
        }),
      ],
    },
    { dataset: { datasetId: "d9", name: "Other" }, runs: [] },
  ];
  const key = "agent|g1|";

  it("joins one setup across datasets, columns by agent version", () => {
    const m = buildByDataset(entries, {
      configKey: key,
      axis: "agent_version",
      focusValue: "Support Agent v3",
    });
    expect(m.columns.map((c) => c.label)).toEqual([
      "Support Agent v2",
      "Support Agent v3",
    ]);
    expect(m.rows.map((r) => r.dataset.name)).toEqual([
      "Golden set",
      "Tasks",
      "Sessions",
    ]);
    expect(m.ranOn).toBe(3);
    const [v2, v3] = m.columns.map((c) => c.key);
    const golden = m.rows[0];
    expect(byCellText(golden.cells[v2], golden.cells)).toBe("80.3% · #3A");
    expect(byCellText(golden.cells[v3], golden.cells)).toBe(
      "82.1% · #4A, 3 trials",
    );
    const tasks = m.rows[1];
    expect(byCellText(tasks.cells[v3], tasks.cells)).toBe("65.6% · #2B");
    expect(tasks.cells[v3].others.map((r) => r.label)).toEqual(["#1B"]);
    expect(byCellText(tasks.cells[v2], tasks.cells)).toBe("—");
    const sessions = m.rows[2];
    expect(byCellText(sessions.cells[v2], sessions.cells)).toBe(
      "66.7% (12 of 18) · #1A",
    );
    expect(byCellText(sessions.cells[v3], sessions.cells)).toBe(
      "72.2% (13 of 18) · #1B",
    );
  });

  it("headline measure = the most common first metric of a dataset's runs", () => {
    expect(headlineKey(entries[1].runs)).toBe("eval:task_success");
    expect(headlineKey([])).toBeNull();
  });
});

describe("Trend hops", () => {
  const base = {
    run_id: "b",
    label: "#2B",
    config: agent("v3"),
    created_at: "2026-10-15T00:00:00Z",
  };
  const f = {
    run_id: "f",
    label: "#2F",
    config: agent("v3.1 (draft)"),
    parent_run_id: "b",
    iteration: {
      root_run_id: "b",
      n: 1,
      scope: "rows",
      rows_rerun: 6,
      rows_carried: 24,
    },
  };
  const g = {
    run_id: "g",
    label: "#2G",
    config: agent("v3.1 (draft)"),
    parent_run_id: "b",
    iteration: {
      root_run_id: "b",
      n: 2,
      scope: "all",
      rows_rerun: 30,
      rows_carried: 0,
    },
  };

  it("labels iteration hops by rows rerun", () => {
    expect(hopLabel(base, f).hop).toBe("6 rows rerun");
    expect(hopLabel(f, g).hop).toBe("all 30 rows");
    expect(hopLabel(null, f).hop).toBeNull();
  });

  it("labels config hops from configDiff", () => {
    const a = { run_id: "a", label: "#3A", config: agent("v2") };
    expect(hopLabel(a, { ...base, label: "#4A" }).hop).toMatch(/v2 → v3/);
  });

  it("trend points keep column order and totals", () => {
    const pivot = {
      columns: [
        { key: "b", label: "#2B", runIds: ["b"] },
        { key: "f", label: "#2F", runIds: ["f"] },
      ],
      colTotals: { b: { value: 0.656 }, f: { value: 0.856 } },
    };
    const pts = trendPoints(pivot, {
      axis: "iteration",
      runsById: { b: base, f },
    });
    expect(pts.map((p) => [p.label, p.cell.value, p.hop])).toEqual([
      ["#2B", 0.656, null],
      ["#2F", 0.856, "6 rows rerun"],
    ]);
  });
});

describe("Sentences", () => {
  it("spread sentence names the widest group and the baseline's weakest", () => {
    const cols = [
      { key: "a", label: "#2A" },
      { key: "b", label: "#2B" },
    ];
    const pivot = {
      failedEval: false,
      rowKeys: ["refund", "multi_request"],
      columns: cols,
      cells: {
        refund: { a: { value: 0.61 }, b: { value: 0.39 } },
        multi_request: { a: { value: 0.2 }, b: { value: 0.93 } },
      },
    };
    expect(
      spreadSentence(
        pivot,
        { kind: "rate" },
        { baseCol: "a", baseLabel: "#2A" },
      ),
    ).toBe(
      "Biggest spread: multi_request (20% to 93%). #2A's weakest group is multi_request (20%).",
    );
  });

  it("failed evals: first failed Check per failing attempt, errors left out", () => {
    const matrix = {
      rows: [
        {
          row_id: "r1",
          cells: {
            b: {
              trials: [0, 0, 0],
              failed_evals: [["order_found"], ["order_found"], ["order_found"]],
            },
          },
        },
        {
          row_id: "r2",
          cells: {
            a: {
              trials: [0, "error", 1],
              failed_evals: [
                ["reply_rubric", "refund_once"],
                ["order_found"],
                [],
              ],
            },
          },
        },
      ],
    };
    const out = failedEvalSummary(matrix, {
      isCheck: (k) => k !== "reply_rubric",
      labels: { a: "#2A", b: "#2B" },
    });
    expect(out.map((o) => [o.key, o.attempts, o.rowIds.length])).toEqual([
      ["order_found", 3, 1],
      ["refund_once", 1, 1],
    ]);
    expect(failedEvalNote(out[0], true)).toBe("3 attempts, all #2B");
    expect(failedEvalNote(out[1], false)).toBe("1, all #2A");
  });
});
