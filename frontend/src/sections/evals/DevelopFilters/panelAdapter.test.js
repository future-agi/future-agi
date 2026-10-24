import { describe, expect, it } from "vitest";
import { validateFilter } from "./common";
import { columnsToFields, fromPanelValue, toPanelValue } from "./panelAdapter";

const columns = [
  { colId: "c-text", headerName: "Input", data_type: "text" },
  { colId: "c-num", headerName: "Score", data_type: "float" },
  { colId: "c-bool", headerName: "Passed", data_type: "boolean" },
  { colId: "c-date", headerName: "Created", data_type: "datetime" },
  { colId: "c-arr", headerName: "Tags", data_type: "array" },
  { headerName: "No id", data_type: "text" },
];

describe("evals logs filter adapter", () => {
  const fields = columnsToFields(columns);

  it("builds one field per grid column that has an id", () => {
    expect(fields.map((f) => [f.id, f.type])).toEqual([
      ["c-text", "text"],
      ["c-num", "number"],
      ["c-bool", "boolean"],
      ["c-date", "date"],
      ["c-arr", "array"],
    ]);
  });

  it("writes the values the per-type editors wrote (one string for text)", () => {
    const stored = fromPanelValue([
      { field: "c-text", fieldType: "string", operator: "in", value: ["abc"] },
      {
        field: "c-text",
        fieldType: "string",
        operator: "in",
        value: ["a", "b"],
      },
      {
        field: "c-text",
        fieldType: "string",
        operator: "contains",
        value: ["x"],
      },
      {
        field: "c-num",
        fieldType: "number",
        operator: "between",
        value: ["1", "5"],
      },
      {
        field: "c-bool",
        fieldType: "boolean",
        operator: "equals",
        value: "true",
      },
      {
        field: "c-arr",
        fieldType: "array",
        operator: "contains",
        value: ["t"],
      },
    ]);
    expect(stored.map((f) => [f.columnId, f.filterConfig])).toEqual([
      [
        "c-text",
        { filterType: "text", filterOp: "equals", filterValue: "abc" },
      ],
      ["c-text", { filterType: "text", filterOp: "equals", filterValue: "a" }],
      [
        "c-text",
        { filterType: "text", filterOp: "contains", filterValue: "x" },
      ],
      [
        "c-num",
        { filterType: "number", filterOp: "between", filterValue: ["1", "5"] },
      ],
      [
        "c-bool",
        { filterType: "boolean", filterOp: "equals", filterValue: "true" },
      ],
      [
        "c-arr",
        { filterType: "array", filterOp: "contains", filterValue: "t" },
      ],
    ]);
    stored.forEach((filter) => expect(validateFilter(filter)).toBe(true));
  });

  it("reads stored filters back into panel rows and skips blank drafts", () => {
    const rows = toPanelValue(
      [
        {
          id: "0",
          columnId: "",
          filterConfig: { filterType: "", filterOp: "equals", filterValue: "" },
        },
        {
          id: "1",
          columnId: "c-num",
          filterConfig: {
            filterType: "number",
            filterOp: "greater_than",
            filterValue: 3,
          },
        },
        {
          id: "2",
          columnId: "c-arr",
          filterConfig: {
            filterType: "array",
            filterOp: "contains",
            filterValue: "t",
          },
        },
        {
          id: "3",
          columnId: "c-bool",
          filterConfig: {
            filterType: "boolean",
            filterOp: "equals",
            filterValue: "false",
          },
        },
      ],
      fields,
    );
    expect(
      rows.map(({ field, operator, value }) => ({ field, operator, value })),
    ).toEqual([
      { field: "c-num", operator: "greater_than", value: "3" },
      { field: "c-arr", operator: "contains", value: ["t"] },
      { field: "c-bool", operator: "equals", value: "false" },
    ]);
  });
});
