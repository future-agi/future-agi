import { describe, expect, it } from "vitest";
import {
  markdownTable,
  pivotTableData,
  tableCsv,
  workbenchMarkdown,
} from "../workbenchMarkdown";
import { fmtDelta, fmtMoney, fmtSeconds, fmtValue } from "../workbenchFormat";
import {
  readWorkbench,
  pathKeyOf,
  groupLabel,
  groupsOfRow,
} from "../workbenchModel";
import { rowsToSettle } from "../workbenchStats";

const RATE = { kind: "rate", higherIsBetter: true };
const pivot = {
  columns: [
    { key: "a", label: "#2A" },
    { key: "b", label: "#2B" },
  ],
  cells: {
    refund: { a: { value: 11 / 18 }, b: { value: 7 / 18 } },
    "multi|request": { a: { value: 0.2 }, b: { value: 0.4 } },
  },
  rowTotals: {
    refund: { rows: 6, value: 0.5 },
    "multi|request": { rows: 5, value: 0.3 },
  },
  colTotals: { a: { value: 0.533 }, b: { value: 0.656 } },
  all: { value: 0.594 },
};

describe("workbenchMarkdown", () => {
  it("builds a valid markdown table from the pivot", () => {
    const t = pivotTableData(pivot, ["refund", "multi|request"], RATE, {
      groupTitle: "Category",
    });
    const md = markdownTable(t);
    const lines = md.split("\n");
    expect(lines[0]).toBe("| Category | #2A | #2B | Total |");
    expect(lines[1]).toBe("| --- | ---: | ---: | ---: |");
    expect(lines[2]).toBe("| refund (6) | 61% | 39% | 50% |");
    expect(lines[3]).toContain("multi\\|request (5)");
    expect(lines.at(-1)).toBe("| Total | 53% | 66% | 59% |");
    lines.forEach((l) => expect(l.split(/(?<!\\)\|/).length).toBe(6));
  });

  it("report: title, sentence, gap lines, link", () => {
    const md = workbenchMarkdown({
      dataset: "Support ops tasks · sandbox",
      runs: ["#2B", "#2D"],
      measure: "Pass rate (pass@1)",
      sentence: "Biggest spread: multi_request (20% to 93%).",
      gap: {
        title: "Where #2D beats #2B most",
        lines: ["Category refund · +55.6 pts · 6 rows (7/18 → 17/18)"],
      },
      link: "http://x/y?view=workbench",
    });
    expect(
      md.startsWith(
        "**Support ops tasks · sandbox · #2B, #2D · Pass rate (pass@1)**\n\n",
      ),
    ).toBe(true);
    expect(md).toContain("- Category refund · +55.6 pts");
    expect(md).toContain("[Open in Future AGI](http://x/y?view=workbench)");
  });

  it("csv escapes commas and quotes", () => {
    expect(tableCsv({ head: ["a", "b"], rows: [["x,y", 'q"']] })).toBe(
      'a,b\n"x,y","q"""\n',
    );
  });
});

describe("workbench formatting and state", () => {
  it("formats values per the quality bar", () => {
    expect(fmtValue(RATE, 0.6111)).toBe("61%");
    expect(fmtValue(RATE, 0.6111, { precise: true })).toBe("61.1%");
    expect(fmtMoney(0.0041)).toBe("$0.0041");
    expect(fmtMoney(0.21)).toBe("$0.21");
    expect(fmtSeconds(2.94)).toBe("2.9 s");
    expect(fmtSeconds(74)).toBe("74 s");
    expect(fmtSeconds(130)).toBe("2 min");
    expect(fmtDelta(RATE, 0.389)).toBe("+39");
    expect(fmtDelta(RATE, -0.2)).toBe("−20");
    expect(fmtValue(RATE, null)).toBe("—");
  });

  it("reads the URL with defaults", () => {
    const s = readWorkbench(
      new URLSearchParams(
        "view=workbench&rows=meta:category&cols=run&measure=pass_at_1",
      ),
    );
    expect(s).toMatchObject({
      rows: "meta:category",
      cols: "run",
      measure: "pass_at_1",
      chart: "bars",
      agg: "avg",
      across: "runs",
    });
  });

  it("Path… keys resolve, `[*]` gives one group per element, missing reads (missing)", () => {
    const key = pathKeyOf("start_state.orders[*].status", ["start_state"]);
    expect(key).toBe("path:start_state:orders[*].status");
    expect(groupLabel(key)).toBe("Path · start_state.orders[*].status");
    const row = {
      values: { start_state: { orders: [{ status: "a" }, { status: "b" }] } },
    };
    expect(groupsOfRow(row, key)).toEqual(["a", "b"]);
    expect(groupsOfRow({ values: {} }, key)).toEqual(["(missing)"]);
  });

  it("rows to settle a within-noise change (addendum F2 math)", () => {
    // #3A vs #4A Task completion: 14 improved, 7 regressed of 236, +2.5 pts
    expect(
      rowsToSettle({ improved: 14, regressed: 7, n: 236, delta: 0.025424 }),
    ).toBe(1100);
  });
});
