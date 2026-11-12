import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import { useDevelopFilterStore } from "src/sections/develop-detail/states";
import {
  getDataGridFilters,
  useDataGridViewStore,
} from "../../DataGrid/useDataGridView";
import DevelopFilterBox from "../DevelopFilterBox";
import { compareFilterChange, transformFilter, validateFilter } from "../common";

const harness = vi.hoisted(() => ({
  panel: null,
  get: vi.fn(),
  onFilterChanged: vi.fn(),
  columns: [
    {
      field: "created_at",
      headerName: "Created at",
      col: { id: "created_at", name: "Created at", data_type: "datetime" },
    },
    {
      field: "note",
      headerName: "Note",
      col: { id: "note", name: "Note", data_type: "text" },
    },
  ],
}));

vi.mock("src/utils/axios", () => ({
  default: { get: harness.get },
  endpoints: {
    develop: {
      getDatasetDetail: (id) =>
        `/model-hub/develops/${id}/get-dataset-table/`,
    },
  },
}));
vi.mock("src/generated/api-contracts/api", () => ({
  modelHubDevelopsGetDatasetTableList: vi.fn(),
}));
vi.mock("src/sections/develop-detail/DataTab/common", () => ({
  getColumnConfig: vi.fn(),
}));
vi.mock("src/api/develop/develop-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetColumnConfig: () => harness.columns,
  useGetJsonColumnSchema: () => ({ data: {} }),
}));
vi.mock("src/routes/hooks", () => ({
  useParams: () => ({ dataset: "w4-dataset" }),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({
    gridApi: { current: { onFilterChanged: harness.onFilterChanged } },
  }),
}));
vi.mock("src/api/develop/row-collab", () => ({
  useAssignments: () => ({ data: null }),
  useTranslateInfo: () => ({ data: null }),
}));
vi.mock("src/hooks/useDashboards", () => ({
  useDatasetColumnValues: () => ({ data: [], isLoading: false }),
}));
vi.mock("src/utils/prototype", () => ({ isPrototypeMode: () => false }));

/* eslint-disable react/prop-types */
// Only the editor is stubbed: apply/close, store, chips and query serialization
// use production code. The product check must also exercise the real editor.
vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  default: (props) => {
    harness.panel = props;
    return null;
  },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
/* eslint-enable react/prop-types */

const nullOperators = ["is_null", "is_not_null"];
const dateFilter = (filterOp, filterValue = "") => ({
  columnId: "created_at",
  filterConfig: { filterType: "datetime", filterOp, filterValue },
});
const dateApiFilter = (filterOp, filterValue = null) => ({
  column_id: "created_at",
  filter_config: {
    filter_type: "datetime",
    filter_op: filterOp,
    filter_value: filterValue,
  },
});
const validDate = new Date(2026, 4, 1, 12, 30, 45);
const laterDate = new Date(2026, 4, 2, 12, 30, 45);

// RED targets on FINAL-1f and c26: validation rejects the empty timestamp;
// direct transformation throws, and applying the predicate drops chips/filters.
describe("W4-A-002 datetime null predicates", () => {
  it.each(nullOperators)("accepts %s without a timestamp", (operator) => {
    expect(validateFilter(dateFilter(operator))).toBe(true);
  });

  it.each(nullOperators)("serializes %s without formatting a date", (operator) => {
    expect(transformFilter(dateFilter(operator))).toEqual(dateApiFilter(operator));
  });

  it.each(nullOperators)("detects adding or removing %s", (operator) => {
    const filters = [dateFilter(operator)];
    expect(compareFilterChange([], filters)).toBe(false);
    expect(compareFilterChange(filters, [])).toBe(false);
    expect(compareFilterChange(filters, filters)).toBe(true);
  });

  it("detects switching between null predicates", () => {
    expect(
      compareFilterChange([dateFilter("is_null")], [dateFilter("is_not_null")]),
    ).toBe(false);
  });

  it.each(nullOperators)("still requires a column and type for %s", (operator) => {
    expect(validateFilter({ ...dateFilter(operator), columnId: "" })).toBe(false);
    const filter = dateFilter(operator);
    filter.filterConfig.filterType = "";
    expect(validateFilter(filter)).toBe(false);
  });
});

describe("W4-A-002 valued datetime compatibility", () => {
  it.each([
    "equals",
    "not_equals",
    "less_than",
    "greater_than",
    "less_than_or_equal",
    "greater_than_or_equal",
  ])("keeps date validation and formatting for %s", (operator) => {
    expect(validateFilter(dateFilter(operator, validDate))).toBe(true);
    expect(transformFilter(dateFilter(operator, validDate))).toEqual(
      dateApiFilter(operator, "2026-05-01 12:30:45"),
    );
    ["", "not-a-date", new Date(NaN)].forEach((value) => {
      expect(validateFilter(dateFilter(operator, value))).toBe(false);
    });
  });

  it.each(["between", "not_between"])(
    "keeps both endpoints required and formatted for %s",
    (operator) => {
      const filter = dateFilter(operator, [validDate, laterDate]);
      expect(validateFilter(filter)).toBe(true);
      expect(transformFilter(filter)).toEqual(
        dateApiFilter(operator, ["2026-05-01 12:30:45", "2026-05-02 12:30:45"]),
      );
      [
        [],
        [validDate],
        ["", laterDate],
        [validDate, ""],
        [new Date(NaN), laterDate],
        [validDate, new Date(NaN)],
      ].forEach((value) => {
        expect(validateFilter(dateFilter(operator, value))).toBe(false);
      });
    },
  );
});

const rows = [
  { id: "null-alpha", created_at: null, note: "alpha" },
  { id: "dated-alpha", created_at: "2026-05-01 12:30:45", note: "alpha" },
  { id: "null-beta", created_at: null, note: "beta" },
  { id: "dated-beta", created_at: "2026-05-02 12:30:45", note: "beta" },
];

// This is an explicit mocked server, not proof of backend execution. Its row
// membership depends only on the serialized request, never the selected operator.
const mockTableRequest = async (_url, { params }) => {
  const filters = JSON.parse(params.filters);
  const matchingRows = rows.filter((row) =>
    filters.every(({ column_id, filter_config }) => {
      const { filter_op, filter_value } = filter_config;
      if (filter_op === "is_null") return row[column_id] === null;
      if (filter_op === "is_not_null") return row[column_id] !== null;
      if (filter_op === "equals") return row[column_id] === filter_value;
      throw new Error(`Unexpected fixture operator: ${filter_op}`);
    }),
  );
  return { data: { results: matchingRows } };
};

describe("W4-A-002 applied chips and mocked table requests", () => {
  beforeEach(() => {
    harness.panel = null;
    harness.get.mockReset().mockImplementation(mockTableRequest);
    harness.onFilterChanged.mockReset();
    useDevelopFilterStore.setState({ filters: [], isDevelopFilterOpen: true });
    useDataGridViewStore.setState({ views: {} });
  });

  afterEach(() => {
    cleanup();
    useDevelopFilterStore.getState().resetFilters();
    useDataGridViewStore.setState({ views: {} });
  });

  it.each([
    {
      operator: "is_null",
      chipLabel: "is empty",
      withText: false,
      expectedIds: ["null-alpha", "null-beta"],
    },
    {
      operator: "is_not_null",
      chipLabel: "is not empty",
      withText: false,
      expectedIds: ["dated-alpha", "dated-beta"],
    },
    {
      operator: "is_null",
      chipLabel: "is empty",
      withText: true,
      expectedIds: ["null-alpha"],
    },
    {
      operator: "is_not_null",
      chipLabel: "is not empty",
      withText: true,
      expectedIds: ["dated-alpha"],
    },
  ])(
    "applies $operator with text=$withText to chips, request and rows",
    async ({ operator, chipLabel, withText, expectedIds }) => {
      const { container } = render(React.createElement(DevelopFilterBox));
      const textPanelFilter = {
        field: "note",
        fieldType: "string",
        operator: "equals",
        value: "alpha",
      };
      const panelFilters = [
        ...(withText ? [textPanelFilter] : []),
        { field: "created_at", fieldType: "date", operator, value: "" },
      ];
      act(() => {
        harness.panel.onApply(panelFilters);
        harness.panel.onClose();
      });

      expect(harness.onFilterChanged).toHaveBeenCalledTimes(1);
      expect(useDevelopFilterStore.getState().isDevelopFilterOpen).toBe(false);
      const chip = container.querySelector('[data-filter-chip-column="created_at"]');
      expect(chip).toHaveTextContent("Created at");
      expect(chip).toHaveTextContent(chipLabel);
      expect(screen.getByText(chipLabel)).toBeInTheDocument();
      expect(container.querySelectorAll("[data-filter-chip-column]")).toHaveLength(
        withText ? 2 : 1,
      );

      const expectedFilters = [
        ...(withText
          ? [{
              column_id: "note",
              filter_config: {
                filter_type: "text",
                filter_op: "equals",
                filter_value: "alpha",
              },
            }]
          : []),
        dateApiFilter(operator),
      ];
      const filters = getDataGridFilters("w4-dataset");
      expect(filters).toEqual(expectedFilters);
      const response = await getDatasetQueryOptions(
        "w4-dataset", 0, filters, [], "",
      ).queryFn();
      expect(harness.get).toHaveBeenCalledTimes(1);
      expect(harness.get).toHaveBeenCalledWith(
        "/model-hub/develops/w4-dataset/get-dataset-table/",
        { params: {
          current_page_index: 0,
          filters: JSON.stringify(expectedFilters),
          sort: "[]",
          page_size: 30,
        } },
      );
      expect(response.data.results.map((row) => row.id)).toEqual(expectedIds);
    },
  );
});
