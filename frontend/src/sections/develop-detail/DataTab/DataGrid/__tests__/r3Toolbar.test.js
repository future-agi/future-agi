import { describe, expect, it } from "vitest";
import { dataFooterParts, dataFooterText, dataHelpText } from "../dataHelp";
import { failedOnlyCopy } from "../ToolbarControls";
import { normalizeDensity, systemFilters } from "../dataGridView";

describe("R3 P1 grid toolbar copy", () => {
  it("writes the help line per dataset kind", () => {
    expect(
      dataHelpText({ kind: "task_suite", total: 30, columns: [] }).lead,
    ).toBe(
      "30 tasks. Each has an input, an expected result and a start state.",
    );
    expect(
      dataHelpText({
        kind: "rows",
        total: 248,
        columns: [{ role: "input" }, { role: "expected" }],
        failedRows: 71,
      }),
    ).toMatchObject({
      lead: "248 rows. Each has an input and an expected result.",
      failed: 71,
    });
    expect(dataHelpText({ kind: "rows", total: 0 }).lead).toBe(
      "0 rows. Add your first row to start.",
    );
  });

  it("folds the help line into the grid footer (one view, A1-09)", () => {
    const cols = [{ role: "input" }, { role: "expected" }];
    expect(
      dataFooterText({
        kind: "task_suite",
        total: 30,
        shown: 30,
        failedRows: 3,
        version: 2,
      }),
    ).toBe(
      "30 tasks · each has an input, an expected result and a start state · 3 fail at least one check · v2",
    );
    // No failures → that clause is dropped.
    expect(
      dataFooterText({ kind: "task_suite", total: 30, shown: 30, version: 2 }),
    ).toBe(
      "30 tasks · each has an input, an expected result and a start state · v2",
    );
    // Other kinds: dataHelpText()'s lead, as footer parts.
    expect(
      dataFooterText({
        kind: "rows",
        total: 248,
        columns: cols,
        failedRows: 71,
        version: 6,
      }),
    ).toBe(
      "248 rows · each has an input and an expected result · 71 fail at least one check · v6",
    );
    // Filtered, pending rows and a selection keep the status-bar facts.
    expect(
      dataFooterParts({
        kind: "rows",
        total: 248,
        shown: 71,
        columns: cols,
        selected: 3,
        version: 6,
        added: 2,
      }),
    ).toEqual({
      count: "71 of 248 rows (filtered) + 2 new, unsaved",
      each: "each has an input and an expected result",
      failed: null,
      selected: "3 selected",
      version: "v6",
    });
    expect(dataFooterText({ kind: "rows", total: 0, shown: 0 })).toBe(
      "0 rows · add your first row to start",
    );
  });

  it("disables Failed only with a reason on a never-run dataset", () => {
    expect(failedOnlyCopy({ evals: [] })).toEqual({
      tooltip: "No pass/fail evaluations are available",
      disabled: true,
    });
    expect(failedOnlyCopy({ evals: [1, 2, 3], version: 6 }).header).toBe(
      "Rows failing at least one of 3 evals in v6",
    );
  });

  it("narrows Failed only to some evals and reads R2's wrap density", () => {
    expect(
      systemFilters({ failedOnly: true, failedEvalIds: ["c1"] })[0],
    ).toEqual({
      column_id: "system:row_failed_in",
      filter_config: {
        filter_type: "categorical",
        filter_op: "in",
        filter_value: ["c1"],
      },
    });
    expect(systemFilters({ failedOnly: true })[0].column_id).toBe(
      "system:row_failed",
    );
    expect(normalizeDensity("wrap")).toBe("full");
  });
});

import { jsonChipSummary } from "src/sections/common/DevelopCellRenderer/CellRenderers/jsonChips";

describe("JSON cells as summary chips (SVP-4.2)", () => {
  it("summarises tool calls and objects", () => {
    expect(
      jsonChipSummary(
        '[{"name":"lookup_order","args":{"order_id":"A-1"}},{"name":"refund","args":{}}]',
      ),
    ).toEqual({
      kind: "calls",
      text: "lookup_order(order_id)",
      more: 1,
      count: 2,
    });
    expect(
      jsonChipSummary({ order: { id: 1 }, customer: { id: 2 } }).text,
    ).toBe("{ 1 order, 1 customer }");
    expect(jsonChipSummary("[]")).toBeNull();
    expect(jsonChipSummary("not json")).toBeNull();
  });
});

import { peekText } from "../CellPeekPopover";

describe("cell peek (DG#7)", () => {
  it("pretty-prints JSON, clips at 600 chars and counts the rest", () => {
    expect(peekText({ cell_value: '{"a":1}' })).toEqual({
      text: '{\n  "a": 1\n}',
      mono: true,
      more: 0,
    });
    const long = peekText("x".repeat(700));
    expect(long.text).toHaveLength(600);
    expect(long.more).toBe(100);
    expect(peekText(null).text).toBe("");
  });
});
