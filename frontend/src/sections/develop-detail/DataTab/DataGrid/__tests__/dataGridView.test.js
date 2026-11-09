import { describe, expect, it } from "vitest";
import {
  columnGroups,
  columnWidthFor,
  shortTextWidth,
  effectiveSplitIds,
  inferRole,
  NO_SPLIT,
  partitionInboundFilters,
  rowHeightFor,
  selectionLabel,
  sourceLabel,
  splitButtonLabel,
  splitCheckState,
  splitIdsFromNames,
  splitNamesParam,
  splitPillLabel,
  statsLine,
  statusBarText,
  systemFilters,
} from "../dataGridView";

const splits = [
  { id: "S1", name: "smoke", color: "info" },
  { id: "S2", name: "test", color: "secondary" },
  { id: "S3", name: "hard", color: "error" },
];
const byId = Object.fromEntries(splits.map((s) => [s.id, s]));

describe("density and widths (G4)", () => {
  it("maps densities to row heights; Full text (and R2 wrap) is auto", () => {
    expect(rowHeightFor("compact")).toBe(36);
    expect(rowHeightFor("comfortable")).toBe(76);
    expect(rowHeightFor("tall")).toBe(136);
    expect(rowHeightFor("full")).toBeNull();
    expect(rowHeightFor("wrap")).toBeNull();
    expect(rowHeightFor("nonsense")).toBe(36);
  });

  it("sizes columns by kind instead of a flat 300", () => {
    expect(columnWidthFor({ data_type: "text", origin_type: "OTHERS" })).toBe(
      240,
    );
    expect(columnWidthFor({ data_type: "json", origin_type: "OTHERS" })).toBe(
      240,
    );
    expect(columnWidthFor({ name: "id", data_type: "text" })).toBe(120);
    expect(
      columnWidthFor({
        name: "notes",
        data_type: "text",
        stats: { empty_count: 37, total: 40 },
      }),
    ).toBe(96);
    expect(columnWidthFor({ data_type: "float", origin_type: "OTHERS" })).toBe(
      110,
    );
    expect(columnWidthFor({ data_type: "datetime" })).toBe(150);
    expect(columnWidthFor({ data_type: "image" })).toBe(120);
    expect(columnWidthFor({ data_type: "audio" })).toBe(220);
    expect(
      columnWidthFor({ data_type: "boolean", origin_type: "evaluation" }),
    ).toBe(160);
  });
});

describe("roles (G3)", () => {
  it("infers roles from the name table", () => {
    expect(inferRole("customer_message")).toBe("input");
    expect(inferRole("question")).toBe("input");
    expect(inferRole("expected_reply")).toBe("expected");
    expect(inferRole("ground truth")).toBe("expected");
    expect(inferRole("ticket_id")).toBe("metadata");
    expect(inferRole("difficulty")).toBe("metadata");
    expect(inferRole("customer_tier")).toBe("metadata");
    expect(inferRole("anything_else")).toBe("input");
    expect(inferRole("reference_answer")).toBe("expected");
    expect(inferRole("expected_tool_calls")).toBe("expected");
    expect(inferRole("")).toBe("input");
  });

  it("groups columns for the chooser, dropping reason columns and empty groups", () => {
    const groups = columnGroups([
      { id: "a", origin_type: "OTHERS", role: "input" },
      { id: "b", origin_type: "OTHERS", role: "expected" },
      { id: "c", origin_type: "OTHERS", role: null },
      { id: "d", origin_type: "run_prompt" },
      { id: "e", origin_type: "evaluation" },
      { id: "e-r", origin_type: "evaluation_reason" },
    ]);
    expect(groups.map((g) => [g.key, g.ids])).toEqual([
      ["input", ["a"]],
      ["expected", ["b"]],
      ["output", ["d"]],
      ["eval", ["e"]],
      ["none", ["c"]],
    ]);
  });
});

describe("header stats line (G5)", () => {
  it("pass/fail: pass rate, clickable fails, errors", () => {
    const line = statsLine({
      kind: "pass_fail",
      pass_rate: 0.808,
      fail_count: 47,
      error_count: 3,
      total: 248,
      completed: 245,
    });
    expect(line.parts.map((p) => p.text)).toEqual([
      "81% pass",
      "47 fail",
      "3 err",
    ]);
    expect(line.parts[1]).toMatchObject({ tone: "error", action: "failed" });
  });

  it("score: average and rows below threshold", () => {
    const line = statsLine({
      kind: "score",
      avg: 0.7898,
      threshold: 0.7,
      below_threshold: 38,
    });
    expect(line.parts.map((p) => p.text)).toEqual(["avg 0.79", "38 < 0.7"]);
    expect(line.parts[1].action).toBe("below");
  });

  it("run prompt output: latency, cost, errors", () => {
    const line = statsLine({
      kind: "output",
      latency_p50_ms: 1241,
      cost_total: 0.31,
      error_count: 3,
    });
    expect(line.parts.map((p) => p.text)).toEqual(["1.2 s", "$0.31", "3 err"]);
  });

  it("expected column: empty count is an is_null action", () => {
    const line = statsLine({ kind: "expected", empty_count: 4, total: 248 });
    expect(line.parts).toEqual([
      { text: "4 empty", tone: "warning", action: "empty" },
    ]);
  });

  it("running columns show progress", () => {
    const line = statsLine(
      { kind: "output", completed: 120, total: 248 },
      { status: "Running" },
    );
    expect(line.parts[0].text).toBe("Running · 120/248");
    expect(line.progress).toBeCloseTo(120 / 248);
  });

  it("returns null without stats", () => {
    expect(statsLine(null)).toBeNull();
  });
});

describe("system filters (G6, §6)", () => {
  it("builds the contract shapes", () => {
    expect(
      systemFilters({ failedOnly: true, splitIds: ["S3", NO_SPLIT] }),
    ).toEqual([
      {
        column_id: "system:row_failed",
        filter_config: {
          filter_type: "boolean",
          filter_op: "equals",
          filter_value: true,
        },
      },
      {
        column_id: "system:splits",
        filter_config: {
          filter_type: "categorical",
          filter_op: "in",
          filter_value: ["S3", "__none__"],
        },
      },
    ]);
    expect(systemFilters({})).toEqual([]);
  });

  it("splits an inbound drill-down into toggles and column filters", () => {
    const parts = partitionInboundFilters([
      {
        column_id: "system:row_failed",
        filter_config: { filter_op: "equals", filter_value: true },
      },
      {
        column_id: "system:splits",
        filter_config: { filter_op: "in", filter_value: ["S3"] },
      },
      {
        column_id: "col-g",
        filter_config: {
          filter_type: "number",
          filter_op: "less_than",
          filter_value: 0.7,
        },
      },
    ]);
    expect(parts.failedOnly).toBe(true);
    expect(parts.splitIds).toEqual(["S3"]);
    expect(parts.columnFilters).toHaveLength(1);
  });

  it("labels the split button and pill", () => {
    expect(splitButtonLabel([], byId)).toBe("All splits");
    expect(splitButtonLabel(["S3", "S1"], byId)).toBe("hard, smoke");
    expect(splitButtonLabel(["S1", "S2", "S3"], byId)).toBe("3 splits");
    expect(splitButtonLabel([NO_SPLIT], byId)).toBe("No split");
    expect(splitPillLabel(["S3", "S1"], byId)).toBe("Split: hard, smoke");
  });

  it("round-trips split names in the URL", () => {
    expect(splitIdsFromNames("hard,smoke", splits)).toEqual(["S3", "S1"]);
    expect(splitIdsFromNames("none", splits)).toEqual([NO_SPLIT]);
    expect(splitIdsFromNames("missing", splits)).toEqual([]);
    expect(splitNamesParam(["S3", NO_SPLIT], byId)).toBe("hard,none");
    expect(splitNamesParam([], byId)).toBeNull();
  });
});

describe("split assignment (§6)", () => {
  it("is checked, indeterminate or empty across the selection", () => {
    expect(splitCheckState([["S3"], ["S3", "S1"]], "S3")).toBe("all");
    expect(splitCheckState([["S3"], ["S1"]], "S3")).toBe("some");
    expect(splitCheckState([["S1"]], "S3")).toBe("none");
    expect(splitCheckState([], "S3")).toBe("none");
  });

  it("applies pending set_splits ops", () => {
    const ops = [
      { op: "set_splits", row_ids: ["r1"], add: ["S3"], remove: ["S2"] },
      { op: "set_splits", row_ids: ["r2"], add: ["S1"], remove: [] },
    ];
    expect(effectiveSplitIds(["S2"], ops, "r1")).toEqual(["S3"]);
    expect(effectiveSplitIds(["S2"], ops, "r3")).toEqual(["S2"]);
  });
});

describe("labels (§7, G11, §8.3)", () => {
  it("counts the selection", () => {
    expect(selectionLabel({ selectAll: false, count: 3, total: 248 })).toBe(
      "3 selected",
    );
    expect(
      selectionLabel({ selectAll: true, count: 71, total: 71, filtered: true }),
    ).toBe("All 71 matching rows selected");
    expect(selectionLabel({ selectAll: true, count: 248, total: 248 })).toBe(
      "All 248 selected",
    );
    expect(
      selectionLabel({ selectAll: true, count: 70, total: 71, filtered: true }),
    ).toBe("70 selected");
  });

  it("writes the status bar", () => {
    expect(
      statusBarText({ shown: 71, total: 248, selected: 3, version: 6 }),
    ).toBe("71 of 248 rows (filtered) · 3 selected · v6");
    expect(statusBarText({ shown: 248, total: 248, version: 6 })).toBe(
      "248 rows · v6",
    );
    expect(
      statusBarText({ shown: 248, total: 248, version: 6, added: 1 }),
    ).toBe("248 rows + 1 new, unsaved · v6");
  });

  it("names row sources", () => {
    expect(sourceLabel({ type: "trace" }).label).toBe("Trace");
    expect(
      sourceLabel({ type: "upload", file_name: "refunds-oct.csv" }).label,
    ).toBe("refunds-oct.csv");
    expect(sourceLabel({ type: "experiment", run_label: "#4A" }).label).toBe(
      "Experiment #4A",
    );
    expect(sourceLabel(null).label).toBe("—");
  });
});

describe("short text fits its content (IT-R4 G5 P5)", () => {
  it("sizes a categorical text column to its longest common value", () => {
    const task = {
      name: "task",
      data_type: "text",
      stats: {
        total: 40,
        empty_count: 0,
        top_choices: [
          { value: "tool_use", count: 16 },
          { value: "vision", count: 10 },
          { value: "audio", count: 8 },
          { value: "qa", count: 6 },
        ],
      },
    };
    expect(columnWidthFor(task)).toBe(108);
    expect(shortTextWidth(task)).toBe(108);
  });

  it("keeps 240 when the common values do not describe the column", () => {
    expect(
      columnWidthFor({
        name: "question",
        data_type: "text",
        stats: {
          total: 40,
          empty_count: 0,
          top_choices: [{ value: "Where is my order?", count: 2 }],
        },
      }),
    ).toBe(240);
  });
});

describe("Run Prompt column width (options-truth L2 G7)", () => {
  it("opens at the text width even while every cell is still empty", () => {
    expect(
      columnWidthFor({
        name: "reply_v2",
        data_type: "text",
        origin_type: "run_prompt",
        stats: { total: 248, empty_count: 248 },
      }),
    ).toBeGreaterThanOrEqual(200);
  });
});
