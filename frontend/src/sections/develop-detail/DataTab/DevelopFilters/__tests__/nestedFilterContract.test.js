import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { formatPath } from "src/sections/common/Values/faPath";
import {
  pathFilterEntry,
  splitColumnPath,
} from "src/sections/common/Values/valueActions";
import valueActions from "../../../../../../../futureagi/model_hub/tests/fixtures/nested_value_action_cases.json";
import { useDevelopFilterStore } from "src/sections/develop-detail/states";
import {
  getDataGridFilters,
  useDataGridViewStore,
} from "../../DataGrid/useDataGridView";
import {
  gridNavQuery,
  rowDataFilter,
} from "../../DatapointDrawerV2/gridNavQuery";
import { transformFilter } from "../common";

const COLUMN = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const DATASET = "11111111-1111-4111-8111-111111111111";
const reset = () => {
  useDevelopFilterStore.setState({ filters: [] });
  useDataGridViewStore.setState({ views: {} });
};

beforeEach(reset);
afterEach(reset);

describe("nested dataset filter wire contract", () => {
  it.each([
    ["customer.tier", "enterprise", "text", "enterprise"],
    ["customer.score", 12, "text", "12"],
    ["customer.score", "12", "text", "12"],
    ["customer.score", "12.0", "text", "12.0"],
    ["customer.active", false, "text", "false"],
    ["items[0].name", "first", "text", "first"],
  ])(
    "shares the value action's %s predicate between both reads",
    (path, value, kind, wireValue) => {
      const entry = pathFilterEntry(COLUMN, path, value, "filter");
      useDevelopFilterStore.setState({ filters: [entry] });
      const expected = [
        {
          column_id: `${COLUMN}::${path}`,
          filter_config: {
            filter_type: kind,
            filter_op: "equals",
            filter_value: wireValue,
          },
        },
      ];
      expect(getDataGridFilters(DATASET)).toEqual(expected);
      expect(gridNavQuery({ filters: [entry] }).filters).toEqual(expected);
      expect(transformFilter(entry)).toEqual(expected[0]);
      expect(rowDataFilter(expected[0])).toEqual(expected[0]);
    },
  );

  // The backend consumes these same requests against stored JSON cells.
  it.each(valueActions.cases)(
    "serializes the real path producer and value action for $name",
    ({ keys, path, value, request, text_value: textValue }) => {
      if (typeof value === "number") expect(String(value)).toBe(textValue);
      const fullPath = formatPath(
        "metadata",
        keys.map((key) => ({ type: "key", key })),
      );
      const { rel } = splitColumnPath(fullPath);
      expect(rel).toBe(path);
      const entry = pathFilterEntry(
        valueActions.column_id,
        rel,
        value,
        "filter",
      );
      useDevelopFilterStore.setState({ filters: [entry] });
      expect(JSON.parse(JSON.stringify(getDataGridFilters(DATASET)))).toEqual([
        request,
      ]);
      expect(
        JSON.parse(JSON.stringify(gridNavQuery({ filters: [entry] }).filters)),
      ).toEqual([request]);
    },
  );

  it.each([
    [12, "12"],
    [1e-7, "1e-7"],
    [false, "false"],
    ["12", "12"],
  ])(
    "keeps flat value-action serialization unchanged for %s",
    (value, text) => {
      expect(transformFilter(pathFilterEntry(COLUMN, "", value))).toEqual({
        column_id: COLUMN,
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: text,
        },
      });
    },
  );

  it.each([
    ["number", "greater_than", "10", 10],
    ["boolean", "equals", false, false],
    ["text", "is_null", "", null],
    ["array", "contains", ["alpha"], ["alpha"]],
  ])("preserves typed %s panel predicates", (kind, op, value, wireValue) => {
    const entry = {
      columnId: `${COLUMN}::customer.value`,
      filterConfig: { filterType: kind, filterOp: op, filterValue: value },
    };
    useDevelopFilterStore.setState({ filters: [entry] });
    const expected = [
      {
        column_id: `${COLUMN}::customer.value`,
        filter_config: {
          filter_type: kind,
          filter_op: op,
          filter_value: wireValue,
        },
      },
    ];
    expect(getDataGridFilters(DATASET)).toEqual(expected);
    expect(gridNavQuery({ filters: [entry] }).filters).toEqual(expected);
  });

  it("does not discard an empty path and broaden it to a flat filter", () => {
    const entry = {
      columnId: `${COLUMN}::`,
      filterConfig: {
        filterType: "text",
        filterOp: "equals",
        filterValue: "x",
      },
    };
    expect(transformFilter(entry)).toEqual({
      column_id: `${COLUMN}::`,
      filter_config: {
        filter_type: "text",
        filter_op: "equals",
        filter_value: "x",
      },
    });
  });

  it("normalizes the older API path shape without changing flat filters", () => {
    const flat = {
      column_id: COLUMN,
      filter_config: {
        filter_type: "text",
        filter_op: "equals",
        filter_value: "x",
      },
    };
    expect(rowDataFilter(flat)).toEqual(flat);
    expect(
      rowDataFilter({
        ...flat,
        filter_config: { ...flat.filter_config, path: "customer.tier" },
      }),
    ).toEqual({ ...flat, column_id: `${COLUMN}::customer.tier` });
    expect(
      transformFilter({
        columnId: COLUMN,
        filterConfig: {
          filterType: "text",
          filterOp: "equals",
          filterValue: "x",
        },
      }),
    ).toEqual(flat);
  });
});
