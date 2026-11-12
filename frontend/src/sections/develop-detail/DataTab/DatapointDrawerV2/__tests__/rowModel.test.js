import { describe, expect, it } from "vitest";
import {
  buildRowModel,
  defaultExpectedFor,
  effectiveSplitIds,
  evalNumber,
  evalVerdict,
  humanVerdict,
  outputFooter,
  rowAsJson,
  rowHasPending,
  rowNumberOf,
  shortId,
  versionPillText,
  withEdits,
} from "../rowModel";
import { validateEdit } from "../editValue";
import {
  gridNavQuery,
  headerPosition,
  sortFromColumnState,
} from "../gridNavQuery";
import {
  compareRunsHref,
  runEvalVerdict,
  runResultHref,
  runsFlags,
  runsSummaryText,
} from "../runsModel";

const COLS = [
  {
    id: "c1",
    name: "customer_message",
    origin_type: "OTHERS",
    data_type: "text",
    role: "input",
  },
  {
    id: "c3",
    name: "expected_reply",
    origin_type: "OTHERS",
    data_type: "text",
    role: "expected",
  },
  {
    id: "c4",
    name: "expected_tool_calls",
    origin_type: "OTHERS",
    data_type: "json",
    role: "expected",
  },
  {
    id: "c5",
    name: "intent",
    origin_type: "OTHERS",
    data_type: "text",
    role: "metadata",
  },
  { id: "c9", name: "notes", origin_type: "OTHERS", data_type: "text" },
  {
    id: "ca",
    name: "Draft reply",
    origin_type: "run_prompt",
    data_type: "text",
    role: "output",
  },
  {
    id: "cb",
    name: "Correctness",
    origin_type: "evaluation",
    data_type: "boolean",
    source_id: "e1",
    role: "eval",
  },
  {
    id: "cb-r",
    name: "Correctness-reason",
    origin_type: "evaluation_reason",
    source_id: "e1",
  },
  {
    id: "cc",
    name: "Groundedness",
    origin_type: "evaluation",
    data_type: "float",
    source_id: "e2",
    role: "eval",
  },
  {
    id: "cd",
    name: "Length",
    origin_type: "evaluation",
    data_type: "float",
    source_id: "e3",
    role: "eval",
  },
  {
    id: "ce",
    name: "Correct?",
    origin_type: "annotation_label",
    data_type: "text",
    role: "human",
  },
];
const EVALS = [
  {
    id: "e1",
    output_type: "pass_fail",
    mapping: { output: "ca", expected: "c3" },
  },
  {
    id: "e2",
    output_type: "percentage",
    pass_threshold: 0.7,
    mapping: { output: "ca" },
  },
  {
    id: "e3",
    output_type: "score",
    higher_is_better: false,
    pass_threshold: 0.5,
    mapping: { text: "c1" },
  },
];
const ROW = {
  row_id: "r-12",
  order: 11,
  c1: { cell_value: "I was charged twice" },
  c3: { cell_value: "Refunds take 5–7 days" },
  c4: { cell_value: "[]" },
  c5: { cell_value: "billing" },
  c9: { cell_value: "" },
  ca: {
    cell_value: "Refunded both charges, 3–5 days",
    status: "pass",
    value_infos: {
      metadata: {
        response_time: 793,
        usage: { total_tokens: 341 },
        cost: { total_cost: 0.000075 },
      },
    },
  },
  cb: {
    cell_value: "Failed",
    status: "pass",
    value_infos: { reason: "Refunds both charges." },
  },
  cc: { cell_value: "0.41", status: "pass" },
  cd: { cell_value: "0.2", status: "pass" },
  ce: { cell_value: '{"value":"down"}' },
  row_meta: { metadata: { ticket_id: "T-1" }, split_ids: ["s1"] },
};

describe("buildRowModel", () => {
  const m = buildRowModel(COLS, ROW, EVALS);
  it("groups user columns by role and sets role-less ones apart", () => {
    expect(m.input.map((f) => f.column.name)).toEqual(["customer_message"]);
    expect(m.expected.map((f) => f.column.name)).toEqual([
      "expected_reply",
      "expected_tool_calls",
    ]);
    expect(m.metadata.map((f) => f.column.name)).toEqual(["intent"]);
    expect(m.noRole.map((f) => f.column.name)).toEqual(["notes"]);
    expect(m.human.map((f) => f.column.name)).toEqual(["Correct?"]);
  });
  it("ties evals to the output their mapping reads; the rest stay loose", () => {
    expect(m.outputs[0].evals.map((e) => e.column.name)).toEqual([
      "Correctness",
      "Groundedness",
    ]);
    expect(m.evals.map((e) => e.column.name)).toEqual(["Length"]);
  });
  it("verdicts follow direction and threshold (D4)", () => {
    const [corr, ground] = m.outputs[0].evals;
    expect(corr.verdict).toMatchObject({ status: "fail", display: "Fail" });
    expect(corr.reason).toBe("Refunds both charges.");
    expect(ground.verdict).toMatchObject({ status: "fail", display: "0.41" });
    expect(m.evals[0].verdict.status).toBe("pass"); // lower is better, 0.2 <= 0.5
  });
});

describe("eval helpers", () => {
  it("evalNumber reads Passed/Failed and numbers", () => {
    expect(evalNumber("Passed")).toBe(1);
    expect(evalNumber("Failed")).toBe(0);
    expect(evalNumber("0.72")).toBe(0.72);
    expect(evalNumber("")).toBeNull();
  });
  it("evalVerdict handles running, error and empty cells", () => {
    expect(
      evalVerdict({ status: "running" }, { kind: "pass_fail" }).display,
    ).toBe("Running…");
    expect(evalVerdict({ status: "error" }, { kind: "score" }).status).toBe(
      "error",
    );
    expect(evalVerdict(null, { kind: "score" }).display).toBe("Not scored");
    expect(evalVerdict({ cell_value: "0.8" }, { kind: "score" }).status).toBe(
      "neutral",
    );
  });
  it("humanVerdict shows thumbs and stars", () => {
    expect(humanVerdict({ cell_value: '{"value":"down"}' })).toMatchObject({
      status: "fail",
      display: "👎",
    });
    expect(humanVerdict({ cell_value: '{"rating":4}' }).display).toBe("4 ★");
    expect(humanVerdict({ cell_value: "" }).display).toBe("Not labelled");
  });
});

describe("row helpers", () => {
  it("formats the output footer like 793 ms · 341 tok · $0.000075", () => {
    expect(outputFooter(ROW.ca)).toEqual(["793 ms", "341 tok", "$0.000075"]);
    expect(outputFooter({})).toEqual([]);
  });
  it("numbers, ids and the version pill", () => {
    expect(rowNumberOf(ROW)).toBe(12);
    expect(shortId("b1000000-0000-4000-8000-000000000012")).toBe("00000012");
    expect(
      versionPillText({ added_in_version: 1, last_changed_in_version: 3 }),
    ).toBe("Added v1 · edited v3");
    expect(
      versionPillText({ added_in_version: 6, last_changed_in_version: 6 }),
    ).toBe("Added v6");
    expect(versionPillText({})).toBeNull();
  });
  it("defaultExpectedFor picks a same-type Expected field", () => {
    const m = buildRowModel(COLS, ROW, EVALS);
    expect(defaultExpectedFor(m.outputs[0], m.expected).column.name).toBe(
      "expected_reply",
    );
    expect(defaultExpectedFor(m.outputs[0], [])).toBeNull();
  });
  it("withEdits overlays pending values and row metadata", () => {
    const m = withEdits(
      buildRowModel(COLS, ROW, EVALS),
      {
        c3: { before: "Refunds take 5–7 days", after: "New reply" },
        __meta: { after: { a: 1 } },
      },
      ROW.row_meta.metadata,
      "__meta",
    );
    expect(m.expected[0]).toMatchObject({
      value: "New reply",
      original: "Refunds take 5–7 days",
      edited: true,
    });
    expect(m.input[0]).toMatchObject({
      value: "I was charged twice",
      edited: false,
    });
    expect(m.rowMetadata).toEqual({
      value: { a: 1 },
      original: { ticket_id: "T-1" },
      edited: true,
    });
    expect(m.human[0].verdict.display).toBe("👎");
  });
  it("effective splits and pending state come from the change set", () => {
    const ops = [
      { op: "set_splits", row_ids: ["r-12"], add: ["s2"], remove: ["s1"] },
      { op: "set_splits", row_ids: ["r-99"], add: ["s3"], remove: [] },
    ];
    expect(effectiveSplitIds(["s1"], ops, "r-12")).toEqual(["s2"]);
    expect(rowHasPending(ops, "r-12")).toBe(true);
    expect(rowHasPending([{ op: "update_cell", row_id: "r-1" }], "r-12")).toBe(
      false,
    ); // A pending new row is pending by itself (the Unsaved pill).
    expect(
      rowHasPending([{ op: "add_row", temp_id: "new-1", cells: {} }], "new-1"),
    ).toBe(true);
  });
  it("rowAsJson parses JSON strings and keeps row metadata", () => {
    const j = rowAsJson(COLS, ROW);
    expect(j.row).toBe(12);
    expect(j.values.expected_tool_calls).toEqual([]);
    expect(j.values).not.toHaveProperty("Correctness-reason");
    expect(j.metadata).toEqual({ ticket_id: "T-1" });
  });
});

describe("validateEdit", () => {
  it("blocks invalid JSON with a message", () => {
    const r = validateEdit("{bad", "json");
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/^Invalid JSON: /);
    expect(validateEdit('{ "a": 1 }', "json")).toEqual({
      ok: true,
      value: '{"a":1}',
    });
  });
  it("coerces numbers and booleans, keeps text", () => {
    expect(validateEdit("3", "integer")).toEqual({ ok: true, value: 3 });
    expect(validateEdit("3.5", "integer").ok).toBe(false);
    expect(validateEdit("x", "float").ok).toBe(false);
    expect(validateEdit("true", "boolean")).toEqual({
      ok: true,
      value: "true",
    });
    expect(validateEdit("Hello\nthere", "text")).toEqual({
      ok: true,
      value: "Hello\nthere",
    });
  });
});

describe("gridNavQuery", () => {
  it("builds the grid's sort and marks a narrowed set", () => {
    expect(
      sortFromColumnState([{ colId: "a", sort: "desc" }, { colId: "b" }]),
    ).toEqual([{ column_id: "a", type: "descending" }]);
    const q = gridNavQuery({ search: " refund ", columnState: [] });
    expect(q.narrowed).toBe(true);
    expect(q.search).toEqual({
      key: "refund",
      type: ["text", "image", "audio"],
    });
    expect(gridNavQuery({}).narrowed).toBe(false);
  });
  it("headerPosition is 1-based and null outside the set", () => {
    expect(
      headerPosition(
        { position: { index: 11, total: 71 } },
        { narrowed: true },
      ),
    ).toEqual({
      index: 12,
      total: 71,
      filtered: true,
    });
    expect(
      headerPosition(
        { position: { index: null, total: 71 } },
        { narrowed: true },
      ).index,
    ).toBeNull();
    expect(headerPosition(null, {})).toBeNull();
  });
});

describe("runsModel", () => {
  const corr = {
    key: "eval:correctness",
    name: "Correctness",
    output_type: "pass_fail",
    higher_is_better: true,
  };
  const ground = {
    key: "eval:groundedness",
    output_type: "score",
    higher_is_better: true,
    pass_threshold: 0.7,
  };
  it("runEvalVerdict shows Pass/Fail, scores and trial means", () => {
    expect(runEvalVerdict({ value: 0 }, corr)).toMatchObject({
      status: "fail",
      display: "Fail",
    });
    expect(runEvalVerdict({ value: 2 / 3 }, corr, 3)).toMatchObject({
      status: "pass",
      display: "0.67 · 3 trials",
    });
    expect(runEvalVerdict({ value: 0.57 }, ground)).toMatchObject({
      status: "fail",
      display: "0.57",
    });
    expect(runEvalVerdict(null, corr).display).toBe("Not scored");
  });
  it("summary text and flags", () => {
    const summary = {
      runs: 9,
      experiments: 7,
      primary_metric_name: "Correctness",
      primary_scored: 9,
      primary_pass: 1,
      flags: {
        hard_case: true,
        fails_of_runs: [8, 9],
        flaky: false,
        expected_changed_after_runs: 4,
      },
    };
    expect(runsSummaryText(summary)).toBe(
      "Ran in 9 runs · 7 experiments · Passes Correctness in 1/9",
    );
    expect(runsFlags(summary).map((f) => f.label)).toEqual([
      "Hard case · fails 8 of 9 runs",
      "Expected changed after 4 runs",
    ]);
  });
  it("links put the baseline first and carry the row", () => {
    expect(
      compareRunsHref("DS", ["r2", "r1"], { baselineId: "r1", rowId: "row" }),
    ).toBe(
      "/dashboard/develop/DS/compare/data?runs=r1%2Cr2&baseline=r1&row=row",
    );
    expect(
      runResultHref({ run_id: "R", experiment_id: "E" }, "row", {
        trajectory: true,
      }),
    ).toBe(
      "/dashboard/develop/experiment/E/data?row=row&run=R&dtab=trajectory",
    );
  });
});
