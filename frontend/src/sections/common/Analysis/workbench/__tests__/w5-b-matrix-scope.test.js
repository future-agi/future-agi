// W5 B1: Explore's group by an expression evaluates the one expression
// language over a matrix row, and buildPivot groups by its value.
import { describe, expect, it } from "vitest";
import {
  compileGroupExpression,
  expressionGrouper,
  groupNameOf,
  matrixCatalog,
} from "../matrixScope";
import { buildPivot } from "../workbenchPivot";
import {
  groupLabel,
  groupOptions,
  groupsOfRow,
  MISSING,
} from "../workbenchModel";

const R = "run-a";
const row = (n, values, gv, trials) => ({
  row_id: `r${n}`,
  n,
  label: `Row ${n}`,
  values,
  group_values: {
    category: null,
    difficulty: null,
    split: null,
    source: "file",
    ...gv,
  },
  cells: { [R]: { trials } },
});
const matrix = {
  rows: [
    row(
      1,
      { customer_message: "short", order: { items: 2 } },
      { tier: "pro", split: "smoke, train" },
      [1],
    ),
    row(
      2,
      { customer_message: "x".repeat(500), order: { items: 5 } },
      { tier: "enterprise", split: "train" },
      [0],
    ),
    row(
      3,
      { customer_message: "y".repeat(450), order: { items: 1 } },
      { tier: "pro" },
      [1],
    ),
    row(4, { customer_message: "", order: null }, { tier: null }, [1]),
  ],
};
const columns = [{ key: R, label: "#1A", runIds: [R] }];
const measure = { key: "pass_at_1", kind: "rate" };

describe("matrixCatalog", () => {
  it("names columns, metadata.<key> and the row fields the Data grid uses", () => {
    const names = matrixCatalog(matrix).fields.map((f) => f.name);
    expect(names).toEqual(
      expect.arrayContaining([
        "customer_message",
        "order",
        "metadata.tier",
        "split",
        "tags",
        "row",
        "id",
      ]),
    );
    expect(matrixCatalog(matrix)).toBe(matrixCatalog(matrix));
  });
  it("refuses an unknown name with did-you-mean, and per-run fields", () => {
    expect(
      compileGroupExpression("len(customer_mesage) > 400", matrix).error
        .message,
    ).toMatch(
      /Unknown field .customer_mesage.\. Did you mean customer_message/,
    );
    expect(compileGroupExpression("output.intent", matrix).error).toBeTruthy();
    expect(compileGroupExpression("  ", matrix).error.message).toBe(
      "Type an expression.",
    );
  });
});

describe("expressionGrouper", () => {
  it("puts each row in the group of its value", () => {
    const g = expressionGrouper(matrix, "len(customer_message) > 400");
    expect(matrix.rows.map((r) => g(r))).toEqual([
      ["false"],
      ["true"],
      ["true"],
      ["false"],
    ]);
    const tier = expressionGrouper(matrix, "metadata.tier");
    expect(matrix.rows.map((r) => tier(r))).toEqual([
      ["pro"],
      ["enterprise"],
      ["pro"],
      [MISSING],
    ]);
  });
  it("opens a JSON column as a path and a list into each of its values", () => {
    const items = expressionGrouper(matrix, "order.items >= 2");
    expect(matrix.rows.map((r) => items(r)[0])).toEqual([
      "true",
      "true",
      "false",
      "false",
    ]);
    const split = expressionGrouper(matrix, "split");
    expect(split(matrix.rows[0])).toEqual(["smoke", "train"]);
    expect(split(matrix.rows[3])).toEqual([MISSING]);
  });
  it("names values as groups", () => {
    expect(groupNameOf(null)).toBe(MISSING);
    expect(groupNameOf(true)).toBe("true");
    expect(groupNameOf(3)).toBe("3");
    expect(groupNameOf({ a: 1 })).toBe('{"a":1}');
  });
});

describe("buildPivot with Rows = Expression", () => {
  it("each group's n and value equal the rows the expression puts there", () => {
    const rows = "expr:len(customer_message) > 400";
    const pivot = buildPivot(matrix, { rows, columns, measure, k: 1 });
    const cell = (g) => pivot.cells[g]?.[R];
    const t = cell("true");
    const f = cell("false");
    expect(t.n).toBe(2);
    expect(t.value).toBeCloseTo(0.5, 12);
    expect(f.n).toBe(2);
    expect(f.value).toBeCloseTo(1, 12);
    expect([...new Set(t.rowIds.map((x) => x.split("|")[0]))].sort()).toEqual([
      "r2",
      "r3",
    ]);
    expect(pivot.rowKeys).toEqual(expect.arrayContaining(["true", "false"]));
  });
  it("is offered as Expression… and labelled with its text", () => {
    expect(groupOptions(matrix).map((o) => o.key)).toContain("expr");
    expect(groupLabel("expr:metadata.tier")).toBe("Expression · metadata.tier");
    expect(groupsOfRow(matrix.rows[0], "expr:metadata.tier", {})).toEqual([
      MISSING,
    ]);
  });
});
