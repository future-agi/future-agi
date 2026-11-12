import { describe, expect, it } from "vitest";
import {
  apiFilterToPanelRow,
  apiFiltersToPanelRows,
  panelRowsToApiFilters,
} from "../observeFilterConfig";

const START = "2026-11-08T09:15";
const END = "2026-11-09T12:30";
const dateFilter = (filter_type, filter_op, filter_value) => ({
  column_id: "last_active",
  property_id: "users:last_active",
  display_name: "Last Active",
  filter_config: {
    filter_type,
    filter_op,
    filter_value,
    col_type: "SYSTEM_METRIC",
  },
});

describe.each(["datetime", "date", "timestamp"])(
  "OBS-SW-57: reopen %s filters",
  (filterType) => {
    it.each([
      "equals",
      "not_equals",
      "greater_than",
      "greater_than_or_equal",
      "less_than",
      "less_than_or_equal",
    ])("keeps %s as a scalar date comparison", (operator) => {
      const input = dateFilter(filterType, operator, END);
      const row = apiFilterToPanelRow(input);

      expect(row).toMatchObject({
        field: "last_active",
        registryId: "users:last_active",
        fieldName: "Last Active",
        fieldCategory: "system",
        fieldType: filterType,
        apiColType: "SYSTEM_METRIC",
        operator,
        value: END,
      });
      expect(panelRowsToApiFilters([row])).toEqual([
        dateFilter("datetime", operator, END),
      ]);
    });

    it.each(["between", "not_between"])(
      "keeps both %s date bounds in order",
      (operator) => {
        const input = dateFilter(filterType, operator, [START, END]);
        const [row] = apiFiltersToPanelRows([input]);

        expect(row).toMatchObject({
          fieldType: filterType,
          operator,
          value: [START, END],
        });
        expect(panelRowsToApiFilters([row])).toEqual([
          dateFilter("datetime", operator, [START, END]),
        ]);
      },
    );

    it("hydrates legacy comma-separated ranges as two date bounds", () => {
      const row = apiFilterToPanelRow(
        dateFilter(filterType, "between", `${START}, ${END}`),
      );

      expect(row).toMatchObject({
        fieldType: filterType,
        operator: "between",
        value: [START, END],
      });
      expect(panelRowsToApiFilters([row])).toEqual([
        dateFilter("datetime", "between", [START, END]),
      ]);
    });

    it.each([null, undefined, ""])(
      "hydrates an absent scalar date (%s) as an empty editor string",
      (value) => {
        expect(
          apiFilterToPanelRow(dateFilter(filterType, "greater_than", value)),
        ).toMatchObject({
          fieldType: filterType,
          operator: "greater_than",
          value: "",
        });
      },
    );
  },
);

describe("OBS-SW-57: reapply a reopened Users filter list", () => {
  it("keeps date predicates when another numeric condition is added", () => {
    const after = dateFilter("datetime", "greater_than", START);
    const between = {
      ...dateFilter("datetime", "between", [START, END]),
      column_id: "activated_at",
      property_id: "users:activated_at",
      display_name: "First Active",
    };
    const rows = apiFiltersToPanelRows([after, between]);
    rows.push({
      field: "total_tokens",
      fieldCategory: "system",
      fieldType: "number",
      operator: "greater_than",
      value: "0",
    });

    expect(panelRowsToApiFilters(rows)).toEqual([
      after,
      between,
      {
        column_id: "total_tokens",
        filter_config: {
          filter_type: "number",
          filter_op: "greater_than",
          filter_value: 0,
          col_type: "SYSTEM_METRIC",
        },
      },
    ]);
  });
});
