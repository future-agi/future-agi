// HO results R1: custom columns as results grid groups (pure parts).
import { describe, expect, it, vi } from "vitest";
import {
  addColumnItem,
  customCatalogEntries,
  customGroupMenuItems,
  customKeyOf,
  customSortLabel,
  customSortOf,
  customSortShortLabel,
  customValueOf,
  formatCustomValue,
  parseCustomSortString,
  pushCustomColumns,
} from "../resultsCustomColumns";

const runCol = {
  id: "c1",
  name: "out_len",
  kind: "expression",
  expr: "len(output)",
  applies_to: "run",
  type: "number",
};
const rowCol = {
  id: "c2",
  name: "intent_in",
  kind: "path",
  path: "intent",
  applies_to: "row",
  type: "text",
};
const summary = { custom_columns: [runCol, rowCol] };
const row = {
  example_id: "e1",
  custom: { c2: "refund" },
  custom_errors: {},
  results: {
    a: { status: "completed", custom: { c1: 12 } },
    b: { status: "completed", custom: { c1: 3.14159 } },
  },
};

describe("custom column groups", () => {
  it("builds one group per column: per-run children, or one Value child", () => {
    const out = [];
    const groupMenu = vi.fn(() => [{ label: "x" }]);
    pushCustomColumns((key, def) => out.push({ key, def }), {
      summary,
      runIds: ["a", "b"],
      runsById: { a: { label: "#1A" }, b: { label: "#1B" } },
      baselineId: "a",
      groupMenu,
    });
    expect(out.map((e) => e.key)).toEqual(["custom:c1", "custom:c2"]);
    const [g1, g2] = out.map((e) => e.def);
    expect(g1.children.map((c) => c.colId)).toEqual([
      "custom:c1|a",
      "custom:c1|b",
    ]);
    expect(g1.children.map((c) => c.headerName)).toEqual(["#1A", "#1B"]);
    expect(g1.children[0].headerComponentParams.isBaseline).toBe(true);
    expect(g2.children.map((c) => c.headerName)).toEqual(["Value"]);
    expect(g1.headerGroupComponentParams.resultGroup.kind).toBe("custom");
    expect(groupMenu.mock.calls[0][0].runLabels).toEqual({
      a: "#1A",
      b: "#1B",
    });
    const get = (col) => col.valueGetter({ data: row });
    expect(get(g1.children[1])).toBe(3.14159);
    expect(get(g2.children[0])).toBe("refund");
    expect(g1.children[1].valueFormatter({ value: 3.14159 })).toBe("3.14");
  });

  it("leaves out hidden columns and lists catalog entries in summary order", () => {
    const out = [];
    pushCustomColumns((key) => out.push(key), {
      summary,
      runIds: ["a"],
      isHidden: (k) => k === "custom:c1",
    });
    expect(out).toEqual(["custom:c2"]);
    expect(customCatalogEntries(summary).map((e) => [e.id, e.group])).toEqual([
      ["custom:c1", "custom"],
      ["custom:c2", "custom"],
    ]);
  });

  it("reads values from the row or the run, and formats every type", () => {
    expect(customValueOf(row, runCol, "a")).toBe(12);
    expect(customValueOf(row, rowCol, "a")).toBe("refund");
    expect(customValueOf(row, runCol, "zz")).toBeNull();
    expect(formatCustomValue(null)).toBe("—");
    expect(formatCustomValue(true)).toBe("true");
    expect(formatCustomValue(1234)).toBe("1,234");
    expect(formatCustomValue(0.123456)).toBe("0.1235");
    expect(formatCustomValue(["a", "b"])).toBe('["a","b"]');
  });

  it("round-trips the sort string the mock reads", () => {
    const s = customSortOf(runCol, "b", "desc");
    expect(s).toBe("custom:c1:b:desc");
    expect(parseCustomSortString(s)).toEqual({
      colId: "c1",
      target: "b",
      dir: "desc",
    });
    expect(customSortOf(rowCol, "b", "asc")).toBe("custom:c2:row:asc");
    expect(parseCustomSortString("score:eval:x:b:asc")).toBeNull();
    expect(
      customSortLabel(parseCustomSortString(s), runCol, [
        { run_id: "b", label: "#1B" },
      ]),
    ).toBe("Sorted by out_len ↓ · #1B");
    expect(
      customSortLabel(parseCustomSortString("custom:c2:row:asc"), rowCol),
    ).toBe("Sorted by intent_in ↑");
    // The narrow toolbar's chip: the arrow is its icon.
    expect(
      customSortShortLabel(parseCustomSortString(s), runCol, [
        { run_id: "b", label: "#1B" },
      ]),
    ).toBe("out_len · #1B");
    expect(
      customSortShortLabel(parseCustomSortString("custom:c2:row:asc"), rowCol),
    ).toBe("intent_in");
  });

  it("menu: sort both ways on the candidate, edit, add", () => {
    const sortBy = vi.fn();
    const openColumn = vi.fn();
    const group = {
      custom: runCol,
      runIds: ["a", "b"],
      runLabels: { a: "#1A", b: "#1B" },
    };
    const items = customGroupMenuItems(group, {
      sortBy,
      openColumn,
      firstCandidate: "b",
    });
    expect(items.map((i) => i.label)).toEqual([
      "Sort ascending · #1B",
      "Sort descending · #1B",
      "Edit column…",
    ]);
    items[1].onClick();
    expect(sortBy).toHaveBeenCalledWith("custom:c1:b:desc");
    items[2].onClick();
    expect(openColumn).toHaveBeenCalledWith(runCol);
    const add = addColumnItem({ openColumn });
    expect(add.map((i) => i.label)).toEqual(["Add column…"]);
    add[0].onClick();
    expect(openColumn).toHaveBeenLastCalledWith(null);
    expect(addColumnItem({})).toEqual([]);
    expect(addColumnItem({ openColumn, readOnly: true })[0].disabled).toBe(
      true,
    );
    expect(customKeyOf(runCol)).toBe("custom:c1");
  });
});
