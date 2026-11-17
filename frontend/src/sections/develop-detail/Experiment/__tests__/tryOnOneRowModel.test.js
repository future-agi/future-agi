import { describe, expect, it } from "vitest";
import {
  canTryKind,
  cardModelParams,
  cardRunConfig,
  clampLines,
  evalGlyph,
  evalShortName,
  filterRows,
  formatUsd,
  formatWall,
  rowLabel,
  rowTitle,
  runningLabel,
  tryErrorText,
  visibleEvals,
} from "../tryOnOneRowModel";

const columns = [
  { id: "c1", name: "title", data_type: "text" },
  { id: "c2", name: "instruction", data_type: "text" },
];
const row = (n, title) => ({
  row_id: `r${n}`,
  order: String(n - 1),
  c1: { cell_value: title },
  c2: { cell_value: `Instruction ${n}` },
});
const ROWS = [
  row(1, "Refund a damaged mixer"),
  row(2, "Duplicate charge on A-6120"),
  row(12, "Change the address"),
];

describe("tryOnOneRowModel", () => {
  it("labels rows T1 on task suites, Row 1 elsewhere", () => {
    expect(rowLabel(ROWS[0], { datasetKind: "task_suite" })).toBe("T1");
    expect(rowLabel(ROWS[0])).toBe("Row 1");
    expect(rowLabel({})).toBe("Row");
    expect(rowTitle(ROWS[1], columns)).toBe("Duplicate charge on A-6120");
    expect(
      rowTitle({ c2: { cell_value: "x".repeat(100) } }, columns),
    ).toHaveLength(80);
  });

  it("searches rows by number or title", () => {
    const opts = { columns, datasetKind: "task_suite" };
    expect(filterRows(ROWS, "2", opts).map((r) => r.row_id)).toEqual(["r2"]);
    expect(filterRows(ROWS, "T12", opts).map((r) => r.row_id)).toEqual(["r12"]);
    expect(filterRows(ROWS, "row 1", opts).map((r) => r.row_id)).toEqual([
      "r1",
      "r12",
    ]);
    expect(filterRows(ROWS, "address", opts).map((r) => r.row_id)).toEqual([
      "r12",
    ]);
    expect(filterRows(ROWS, "", opts)).toHaveLength(3);
  });

  it("builds the I1 config of a prompt card with its tools", () => {
    const card = {
      targetType: "prompt",
      promptId: "p1",
      promptVersion: "pv1",
      versionLabel: "V1",
      name: "Support Ops Reply",
      model: [{ value: "" }, { value: "gpt-4o-mini" }],
      modelParams: { "gpt-4o-mini": { temperature: 0.2 } },
      configuration: { tools: [] },
      toolSetup: {
        versionTools: [{ name: "lookup_order", schema_hash: "a41f" }],
        mode: "mock",
        maxToolTurns: 8,
      },
    };
    expect(cardRunConfig(card)).toEqual({
      kind: "prompt",
      prompt: {
        id: "p1",
        name: "Support Ops Reply",
        version: "V1",
        version_id: "pv1",
      },
      model: { id: "gpt-4o-mini", params: { temperature: 0.2 } },
      tools: [{ name: "lookup_order", schema_hash: "a41f", mode: "mock" }],
      max_tool_turns: 8,
      trials: 1,
    });
    expect(cardRunConfig({ ...card, promptVersion: null })).toBeNull();
    expect(
      cardRunConfig({ targetType: "agent", agentId: "g", agentVersion: "v3" }),
    ).toMatchObject({ kind: "agent", agent: { graph_id: "g" } });
    expect(
      cardRunConfig({
        targetType: "harness",
        harness_config: { name: "Claude Code", limits: { max_turns: 30 } },
      }),
    ).toMatchObject({
      kind: "harness",
      harness: { name: "Claude Code", max_turns: 30 },
    });
    expect(cardRunConfig({ targetType: "http" })).toBeNull();
    expect(canTryKind("code")).toBe(false);
    expect(canTryKind("harness")).toBe(true);
  });

  it("formats money and time like the rest of the product", () => {
    expect(formatUsd(0.004259)).toBe("$0.004259");
    expect(formatUsd(0.011)).toBe("$0.011");
    expect(formatUsd(0.21029)).toBe("$0.21029");
    expect(formatUsd(6.2148)).toBe("$6.2148");
    expect(formatUsd(0.0006)).toBe("$0.0006");
    expect(formatUsd(undefined)).toBe("—");
    expect(formatWall(2100)).toBe("2.1 s");
    expect(formatWall(74000)).toBe("74 s");
    expect(formatWall(65000)).toBe("65 s");
    expect(formatWall(120000)).toBe("2 min");
    expect(formatWall(412)).toBe("412 ms");
    expect(formatWall(null)).toBe("—");
  });

  it("clamps the reply and picks the evals to show", () => {
    expect(clampLines("a\nb\nc\nd\ne", 4)).toEqual({
      head: "a\nb\nc\nd",
      more: true,
      lines: 5,
    });
    const evals = [
      { key: "check:order_found", type: "check", passed: false },
      { key: "eval:reply_rubric", type: "judge", passed: true },
    ];
    expect(visibleEvals(evals).map(evalShortName)).toEqual(["order_found"]);
    expect(visibleEvals(evals, { hasEvals: true })).toHaveLength(2);
    expect(evals.map(evalGlyph)).toEqual(["✗", "✓"]);
    expect(evalGlyph({ passed: null })).toBe("–");
  });

  it("says what is happening while it works", () => {
    expect(runningLabel("prompt", 800)).toBe("Running · step 3");
    expect(runningLabel("harness", 0)).toBe("Starting sandbox");
    expect(runningLabel("harness", 2000)).toBe("Running");
    expect(runningLabel("harness", 5000)).toBe("Checking");
    expect(
      tryErrorText({ response: { data: { result: "Row not found" } } }),
    ).toBe("Couldn't run this row: Row not found");
  });
});

describe("IT-R2 G1 P10 · unmapped variables block the try", () => {
  it("names each variable", async () => {
    const { unmappedBlockText } = await import("../tryOnOneRowModel");
    expect(unmappedBlockText(["customer_email", "order_context"])).toBe(
      "Map customer_email and order_context first",
    );
    expect(unmappedBlockText(["customer_email"])).toBe(
      "Map customer_email first",
    );
    expect(unmappedBlockText([])).toBeNull();
  });
});

describe("cardRunConfig sends what the created run uses (L3 G8 P5)", () => {
  it("reads a dotted model's params, the mapping and the tool setup", () => {
    const card = {
      targetType: "prompt",
      promptId: "p1",
      promptVersion: "v3",
      versionLabel: "V3",
      model: [{ value: "gemini-2.5-flash" }],
      modelParams: {
        "gemini-2__DOT__5-flash": {
          temperature: 0.9,
          maxTokens: 512,
          responseFormat: "json_object",
          reasoning: { sliders: {} },
        },
      },
      variableMapping: { customer_message: "c2" },
      toolSetup: {
        versionTools: [{ name: "lookup_order" }, { name: "search_kb" }],
        mode: "per_tool",
        perTool: { lookup_order: "live" },
        endpoints: { lookup_order: "https://orders.acme.test" },
        maxToolTurns: 1,
        onUnrecorded: "fail",
      },
    };
    expect(cardModelParams(card, "gemini-2.5-flash")).toEqual({
      temperature: 0.9,
      maxTokens: 512,
      responseFormat: "json_object",
    });
    const cfg = cardRunConfig(card);
    expect(cfg.model).toEqual({
      id: "gemini-2.5-flash",
      params: {
        temperature: 0.9,
        maxTokens: 512,
        responseFormat: "json_object",
      },
    });
    expect(cfg.variable_mapping).toEqual({ customer_message: "c2" });
    expect(cfg.tools).toEqual([
      {
        name: "lookup_order",
        schema_hash: null,
        mode: "live",
        endpoint: "https://orders.acme.test",
      },
      { name: "search_kb", schema_hash: null, mode: "mock" },
    ]);
    expect(cfg).toMatchObject({ max_tool_turns: 1, on_unrecorded: "fail" });
    expect(
      cardRunConfig({ ...card, variableMapping: {} }).variable_mapping,
    ).toBeUndefined();
  });

  it("formats sub-cent costs with two significant digits", () => {
    expect(formatUsd(0.000245)).toBe("$0.000245");
    expect(formatUsd(0.0006)).toBe("$0.0006");
  });
});
