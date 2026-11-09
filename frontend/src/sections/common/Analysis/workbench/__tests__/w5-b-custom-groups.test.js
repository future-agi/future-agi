// W5 B1: Rows · Custom column ▸ <name> groups by Lane A's column values; a
// run column puts each run's attempts in the group of that run's own value.
import { describe, expect, it } from "vitest";
import {
  customGrouper,
  customNameOf,
  withCustomGroups,
} from "../customColumnGroups";
import { buildPivot } from "../workbenchPivot";
import { groupLabel, MISSING } from "../workbenchModel";

const A = "run-a";
const B = "run-b";
const row = (n, ta, tb) => ({
  row_id: `r${n}`,
  n,
  values: {},
  group_values: {},
  cells: { [A]: { trials: [ta] }, [B]: { trials: [tb] } },
});
const matrix = { rows: [row(1, 1, 0), row(2, 0, 0), row(3, 1, 1)] };
const values = {
  columns: [
    { id: "c-long", name: "long_message", applies_to: "row" },
    { id: "c-intent", name: "intent", applies_to: "run" },
  ],
  values: {
    r1: {
      row: { "c-long": true },
      [A]: { "c-intent": "refund" },
      [B]: { "c-intent": "billing" },
    },
    r2: {
      row: { "c-long": false },
      [A]: { "c-intent": "refund" },
      [B]: { "c-intent": null },
    },
    r3: {
      row: { "c-long": true },
      [A]: { "c-intent": "billing" },
      [B]: { "c-intent": "billing" },
    },
  },
};
const columns = [
  { key: A, label: "#1A", runIds: [A] },
  { key: B, label: "#2A", runIds: [B] },
];
const measure = { key: "pass_at_1", kind: "rate" };

describe("custom column groups", () => {
  it("offers each saved column before None, keyed by its name", () => {
    const opts = withCustomGroups(
      [{ key: "path" }, { key: "none" }],
      values.columns,
    );
    expect(opts.map((o) => o.key)).toEqual([
      "path",
      "cc:long_message",
      "cc:intent",
      "none",
    ]);
    expect(opts[1].label).toBe("Custom column ▸ long_message");
    expect(customNameOf("cc:intent")).toBe("intent");
    expect(customNameOf("meta:x")).toBeNull();
    expect(groupLabel("cc:intent")).toBe("Custom column · intent");
    expect(withCustomGroups([{ key: "none" }], [])).toEqual([{ key: "none" }]);
  });
  it("a row column groups rows by its value", () => {
    const g = customGrouper(values, "long_message");
    expect(matrix.rows.map((r) => g(r, A))).toEqual([
      ["true"],
      ["false"],
      ["true"],
    ]);
    const pivot = buildPivot(matrix, {
      rows: "cc:long_message",
      columns,
      measure,
      k: 1,
      ctx: { customGroups: g },
    });
    expect(pivot.cells.true[A].n).toBe(2);
    expect(pivot.cells.true[A].value).toBeCloseTo(1, 12);
    expect(pivot.cells.false[B].n).toBe(1);
  });
  it("a run column groups each run's attempts by that run's value", () => {
    const g = customGrouper(values, "intent");
    const pivot = buildPivot(matrix, {
      rows: "cc:intent",
      columns,
      measure,
      k: 1,
      ctx: { customGroups: g },
    });
    expect(pivot.cells.refund[A].n).toBe(2);
    expect(pivot.cells.refund[B]?.n ?? 0).toBe(0);
    expect(pivot.cells.billing[A].n).toBe(1);
    expect(pivot.cells.billing[B].n).toBe(2);
    expect(pivot.cells.billing[B].value).toBeCloseTo(0.5, 12);
    expect(pivot.cells[MISSING][B].n).toBe(1);
  });
  it("an unknown column puts every row in (missing)", () => {
    expect(customGrouper(values, "nope")(matrix.rows[0], A)).toEqual([MISSING]);
  });
});
