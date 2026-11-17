import { describe, expect, it } from "vitest";
import {
  DEFAULT_HIDDEN,
  ROLLUP_KEYS,
  buildResultColumns,
  columnMenuEntries,
  columnToggleKey,
  formatRollup,
  isColumnShown,
  rollupEntries,
  rollupValue,
} from "../resultsColumns";
import { otherRunsOnRow } from "../cells/OutputCell";

const A = "run-a";
const B = "run-b";
const summary = {
  runs: [
    { run_id: A, label: "#1A" },
    { run_id: B, label: "#1D" },
  ],
  metrics: [],
  human_metrics: [],
  system_metrics: [
    { key: "ops:latency", name: "Latency" },
    { key: "ops:cost", name: "Cost" },
    { key: "ops:tokens", name: "Tokens" },
    { key: "agent:steps", name: "Steps" },
  ],
  aggregates: {},
};
const ok = (steps, tools, extra = {}) => ({
  status: "ok",
  steps,
  tool_calls: tools,
  llm_calls: 3,
  tool_modes: [],
  ...extra,
});

describe("trace roll-ups", () => {
  it("averages trace_summary over trials and skips infra / no-trace", () => {
    const r = {
      status: "completed",
      trace_summary: [ok(5, 2), ok(6, 2), { status: "infra", steps: 0 }],
    };
    expect(rollupValue(r, ROLLUP_KEYS.steps)).toBe(5.5);
    expect(rollupValue(r, ROLLUP_KEYS.toolCalls)).toBe(2);
    expect(rollupValue(r, ROLLUP_KEYS.turns)).toBeNull();
    const h = {
      status: "completed",
      trace_summary: [ok(21, 7, { turns: 14 }), ok(16, 5, { turns: 11 })],
    };
    expect(rollupValue(h, ROLLUP_KEYS.turns)).toBe(12.5);
    expect(rollupValue({ status: "missing" }, ROLLUP_KEYS.steps)).toBeNull();
  });

  it("counts tool errors from the record, else from failed tool steps", () => {
    expect(
      rollupValue(
        { status: "completed", steps: { tool_errors: 2 } },
        ROLLUP_KEYS.toolErrors,
      ),
    ).toBe(2);
    const r = {
      status: "completed",
      trace_summary: [
        ok(5, 2),
        {
          status: "error",
          steps: 4,
          error: { step: 4, text: "lookup_customer 404" },
        },
      ],
    };
    expect(rollupValue(r, ROLLUP_KEYS.toolErrors)).toBe(0.5);
  });

  it("formats with `—` for missing", () => {
    expect(formatRollup(5)).toBe("5");
    expect(formatRollup(5.33)).toBe("5.3");
    expect(formatRollup(null)).toBe("—");
    expect(formatRollup(1234)).toBe("1,234");
  });

  it("new roll-ups are off by default and persist as opt-in keys", () => {
    const hidden = new Set(DEFAULT_HIDDEN);
    expect(isColumnShown(hidden, ROLLUP_KEYS.toolCalls)).toBe(false);
    expect(isColumnShown(hidden, "ops:cost")).toBe(true);
    expect(columnToggleKey(ROLLUP_KEYS.toolCalls)).toBe("+rollup:tool_calls");
    expect(columnToggleKey("ops:cost")).toBe("ops:cost");
    hidden.add(columnToggleKey(ROLLUP_KEYS.toolCalls));
    expect(isColumnShown(hidden, ROLLUP_KEYS.toolCalls)).toBe(true);
  });

  it("lists Steps · Tool calls · Tool errors · Turns · Cost · Wall time", () => {
    expect(rollupEntries(summary).map((e) => e.label)).toEqual([
      "Steps",
      "Tool calls",
      "Tool errors",
      "Turns",
      "Cost",
      "Wall time",
    ]);
    expect(rollupEntries(summary)[0].key).toBe("agent:steps");
    expect(rollupEntries({ ...summary, system_metrics: [] })[0].key).toBe(
      ROLLUP_KEYS.steps,
    );
    const { perRun, rollups } = columnMenuEntries(summary);
    expect(perRun.map((p) => p.key)).toEqual(["output", "ops:tokens"]);
    expect(rollups).toHaveLength(6);
  });

  it("adds one right-aligned column per run when turned on", () => {
    const hidden = new Set([...DEFAULT_HIDDEN, "+rollup:tool_calls"]);
    const cols = buildResultColumns({ summary, baselineId: A, hidden });
    const g = cols.find((c) => c.groupId === ROLLUP_KEYS.toolCalls);
    expect(g.headerName).toBe("Tool calls");
    expect(g.children.map((c) => c.colId)).toEqual([
      `rollup:tool_calls|${A}`,
      `rollup:tool_calls|${B}`,
    ]);
    expect(g.children[0].cellStyle.textAlign).toBe("right");
    expect(
      g.children[0].valueGetter({
        data: {
          results: { [A]: { status: "completed", trace_summary: [ok(5, 2)] } },
        },
      }),
    ).toBe(2);
    expect(cols.find((c) => c.groupId === ROLLUP_KEYS.turns)).toBeUndefined();
    expect(cols.find((c) => c.groupId === "ops:latency").headerName).toBe(
      "Wall time",
    );
  });

  it("Compare with… lists the other runs that ran the row", () => {
    const row = {
      results: {
        [A]: { status: "completed" },
        [B]: { status: "completed" },
        c: { status: "missing" },
      },
    };
    const runsById = {
      [A]: { run_id: A },
      [B]: { run_id: B },
      c: { run_id: "c" },
    };
    expect(otherRunsOnRow(row, A, runsById).map((r) => r.run_id)).toEqual([B]);
  });
});
