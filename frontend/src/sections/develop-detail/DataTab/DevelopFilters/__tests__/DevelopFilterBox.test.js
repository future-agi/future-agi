/**
 * Tests for panelFilterToStore / unwrapScalarValue.
 *
 * Regression coverage for TH-4400: the AI-query path in
 * TraceFilterPanel wraps every LLM-returned value in an array
 * (`[val]`) to match the trace chip-picker contract. The dataset
 * rows endpoint (`_apply_filters` in develop_dataset.py) expects
 * SCALARS for text/number/date/boolean columns — it does
 * `filter_value.lower()` / `float(filter_value)` directly. An array
 * value crashes inside a try/except the view swallows, so the user
 * sees every row unfiltered instead of a filtered result.
 *
 * The fix unwraps single-element arrays for scalar column types at
 * the panel→store boundary, so the store's filter shape always
 * matches what the backend expects regardless of whether the row
 * came from a manual edit or the AI query.
 */
import { describe, it, expect } from "vitest";
import { panelFilterToStore, unwrapScalarValue } from "../DevelopFilterBox";
import { transformFilter, validateFilter } from "../common";

describe("unwrapScalarValue", () => {
  it("preserves arrays for canonical list operators", () => {
    expect(unwrapScalarValue(["english"], "string", "in")).toEqual(["english"]);
    expect(unwrapScalarValue(["draft"], "string", "not_in")).toEqual(["draft"]);
  });

  it("unwraps single-element arrays for scalar string equality", () => {
    expect(unwrapScalarValue(["english"], "string", "equals")).toBe("english");
  });

  it("unwraps single-element arrays for number fields", () => {
    expect(unwrapScalarValue([5], "number", "equals")).toBe(5);
  });

  it("unwraps single-element arrays for boolean fields", () => {
    expect(unwrapScalarValue([true], "boolean", "equals")).toBe(true);
  });

  it("unwraps single-element arrays for date fields", () => {
    expect(unwrapScalarValue(["2026-04-21"], "date", "equals")).toBe(
      "2026-04-21",
    );
  });

  it("preserves arrays for `array` fieldType", () => {
    // Array columns store JSON lists — the backend expects a list here.
    expect(unwrapScalarValue(["a", "b"], "array", "contains")).toEqual([
      "a",
      "b",
    ]);
  });

  it("preserves arrays for between / not_between range ops", () => {
    // Numeric between needs [min, max]; collapsing would lose the range.
    expect(unwrapScalarValue([1, 10], "number", "between")).toEqual([1, 10]);
    expect(unwrapScalarValue([1, 10], "number", "not_between")).toEqual([
      1, 10,
    ]);
  });

  it("coerces empty arrays to '' so validateFilter rejects the row", () => {
    // Prevents `undefined` leaking into the store and hitting the
    // backend as `null` — `validateFilter` only checks `!== ''`.
    expect(unwrapScalarValue([], "string", "in")).toBe("");
    expect(unwrapScalarValue([undefined], "string", "equals")).toBe("");
    expect(unwrapScalarValue([null], "string", "equals")).toBe("");
  });

  it("passes non-array scalars through untouched", () => {
    expect(unwrapScalarValue("casual", "string", "in")).toBe("casual");
    expect(unwrapScalarValue(5, "number", "equals")).toBe(5);
    expect(unwrapScalarValue(false, "boolean", "equals")).toBe(false);
  });
});

describe("panelFilterToStore — AI-path regression (TH-4400)", () => {
  it("keeps canonical list text values as arrays", () => {
    // This is exactly what TraceFilterPanel.handleAiFilter emits for a
    // dataset text column after the LLM returns `value: "english"`.
    const panel = {
      field: "col_lang",
      fieldCategory: "dataset",
      fieldType: "string",
      operator: "in",
      value: ["english"],
    };
    const out = panelFilterToStore(panel);
    expect(out.columnId).toBe("col_lang");
    expect(out.filterConfig).toMatchObject({
      filterType: "text",
      filterOp: "in",
      filterValue: ["english"],
    });
  });

  it("unwraps array-wrapped scalar equality values", () => {
    const out = panelFilterToStore({
      field: "col_lang",
      fieldCategory: "dataset",
      fieldType: "string",
      operator: "equals",
      value: ["english"],
    });
    expect(out.filterConfig).toMatchObject({
      filterType: "text",
      filterOp: "equals",
      filterValue: "english",
    });
  });

  it("unwraps array-wrapped numeric equals values", () => {
    const out = panelFilterToStore({
      field: "col_score",
      fieldCategory: "dataset",
      fieldType: "number",
      operator: "equals",
      value: [0.8],
    });
    expect(out.filterConfig).toMatchObject({
      filterType: "number",
      filterOp: "equals",
      filterValue: 0.8,
    });
  });

  it("keeps [min, max] tuple for numeric between", () => {
    const out = panelFilterToStore({
      field: "col_score",
      fieldType: "number",
      operator: "between",
      value: [0, 1],
    });
    expect(out.filterConfig.filterOp).toBe("between");
    expect(out.filterConfig.filterValue).toEqual([0, 1]);
  });

  it("keeps array values for array columns", () => {
    // Array column = JSON list — `contains` works against list membership.
    const out = panelFilterToStore({
      field: "col_tags",
      fieldType: "array",
      operator: "contains",
      value: ["urgent"],
    });
    expect(out.filterConfig).toMatchObject({
      filterType: "array",
      filterOp: "contains",
      filterValue: ["urgent"],
    });
  });

  it("round-trips a manual scalar entry unchanged", () => {
    // Manual DatasetColumnValuePicker emits strings directly — must not
    // be accidentally wrapped, unwrapped, or altered by this path.
    const out = panelFilterToStore({
      field: "col_lang",
      fieldType: "string",
      operator: "contains",
      value: "casual",
    });
    expect(out.filterConfig).toMatchObject({
      filterType: "text",
      filterOp: "contains",
      filterValue: "casual",
    });
  });

  it("coerces [undefined] from a stray AI response to '' (row gets rejected)", () => {
    const out = panelFilterToStore({
      field: "col_lang",
      fieldType: "string",
      operator: "in",
      value: [undefined],
    });
    expect(out.filterConfig.filterValue).toBe("");
  });
});

describe("panelFilterToStore — free-text scalar + list operator (TH-6621)", () => {
  // Repro: Dataset Detail → Basic filter → pick a text column (its "equals"
  // label is the canonical list op `in`) → type a value in the free-text
  // input. That input emits a bare scalar; `in` needs a list, so the row was
  // dropped by validateFilter and the grid never updated. The store must wrap
  // the scalar into a single-element list so the same typed value applies —
  // and crucially, the result must survive validateFilter (the exact gate the
  // grid's request builder uses).
  it("wraps a typed scalar into a list for `in` and passes validateFilter", () => {
    const out = panelFilterToStore({
      field: "vf",
      fieldCategory: "dataset",
      fieldType: "string",
      operator: "in",
      value: "02",
    });
    expect(out.filterConfig).toMatchObject({
      filterType: "text",
      filterOp: "in",
      filterValue: ["02"],
    });
    expect(validateFilter(out)).toBe(true);
  });

  it("wraps a typed scalar into a list for `not_in` too", () => {
    const out = panelFilterToStore({
      field: "vf",
      fieldType: "string",
      operator: "not_in",
      value: "02",
    });
    expect(out.filterConfig.filterValue).toEqual(["02"]);
    expect(validateFilter(out)).toBe(true);
  });

  it("leaves scalar operators (contains) as a bare scalar", () => {
    const out = panelFilterToStore({
      field: "vf",
      fieldType: "string",
      operator: "contains",
      value: "02",
    });
    expect(out.filterConfig.filterValue).toBe("02");
    expect(validateFilter(out)).toBe(true);
  });

  it("keeps an empty scalar empty so the row stays invalid (not [''])", () => {
    const out = panelFilterToStore({
      field: "vf",
      fieldType: "string",
      operator: "in",
      value: "",
    });
    expect(out.filterConfig.filterValue).toBe("");
    expect(validateFilter(out)).toBe(false);
  });
});

describe("transformFilter", () => {
  it("emits the canonical backend filter contract", () => {
    const out = transformFilter({
      columnId: "col_score",
      filterConfig: {
        filterType: "number",
        filterOp: "greater_than_or_equal",
        filterValue: "10",
      },
    });

    expect(out).toEqual({
      column_id: "col_score",
      filter_config: {
        filter_type: "number",
        filter_op: "greater_than_or_equal",
        filter_value: 10,
      },
    });
    expect(out).not.toHaveProperty("columnId");
    expect(out).not.toHaveProperty("filterConfig");
  });

  it("keeps dataset datetime values in the backend's accepted format", () => {
    const out = transformFilter({
      columnId: "col_created_at",
      filterConfig: {
        filterType: "datetime",
        filterOp: "between",
        filterValue: [
          new Date(2026, 4, 1, 0, 0, 0),
          new Date(2026, 4, 2, 0, 0, 0),
        ],
      },
    });

    expect(out.filter_config).toEqual({
      filter_type: "datetime",
      filter_op: "between",
      filter_value: ["2026-05-01 00:00:00", "2026-05-02 00:00:00"],
    });
  });

  it("rejects non-canonical operators before the API call", () => {
    expect(() =>
      transformFilter({
        columnId: "col_score",
        filterConfig: {
          filterType: "number",
          filterOp: "not_in_between",
          filterValue: [1, 2],
        },
      }),
    ).toThrow(/Unsupported filter operator/);
  });
});
