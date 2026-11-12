import { describe, expect, it } from "vitest";
import { cellWritesFor } from "../usePrefillRowVariables";

const dataset = {
  columns: [
    { id: "c1", name: "user_message" },
    { id: "c2", name: "customer_tier" },
  ],
  rows: [
    {
      id: "r",
      cells: [
        { id: "x1", column_id: "c1", value: "" },
        { id: "x2", column_id: "c2", value: "free" },
      ],
    },
  ],
};

describe("cellWritesFor", () => {
  it("writes same-named values and skips unchanged cells", () => {
    expect(
      cellWritesFor(dataset, { customer_tier: "pro", other: "x" }),
    ).toEqual([{ cellId: "x2", name: "customer_tier", value: "pro" }]);
    expect(cellWritesFor(dataset, { customer_tier: "free" })).toEqual([]);
  });

  it("the one variable without a value takes the row's only input", () => {
    expect(
      cellWritesFor(dataset, { customer_tier: "pro" }, { soleInput: "Hi" }),
    ).toEqual([
      { cellId: "x1", name: "user_message", value: "Hi" },
      { cellId: "x2", name: "customer_tier", value: "pro" },
    ]);
    // Two unfilled variables: no guess.
    expect(cellWritesFor(dataset, {}, { soleInput: "Hi" })).toEqual([]);
  });

  it("returns nothing without a dataset or prefill", () => {
    expect(cellWritesFor(null, { a: "b" })).toEqual([]);
    expect(cellWritesFor(dataset, null)).toEqual([]);
  });
});
