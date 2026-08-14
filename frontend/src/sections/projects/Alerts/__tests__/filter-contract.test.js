import { describe, expect, it } from "vitest";

import {
  convertFiltersToPayload,
  getAlertFilterValue,
  isAlertMuted,
  isSpanAttrFilterValid,
  normalizeAlertListRow,
} from "../common";
import { transformFilterResponse } from "../components/validation";

describe("alert filter contract", () => {
  it("sends canonical span attribute filters to the API", () => {
    const payload = convertFiltersToPayload([
      {
        property: "observationType",
        filterConfig: { filterValue: "llm" },
      },
      {
        property: "attributes",
        propertyId: "customer_tier",
        filterConfig: {
          filterType: "text",
          filterOp: "equals",
          filterValue: "enterprise",
        },
      },
    ]);

    expect(payload).toEqual({
      observation_type: ["llm"],
      span_attributes_filters: [
        {
          column_id: "customer_tier",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "enterprise",
            col_type: "SPAN_ATTRIBUTE",
          },
        },
      ],
    });
    expect(payload.span_attributes_filters[0]).not.toHaveProperty("columnId");
    expect(payload.span_attributes_filters[0]).not.toHaveProperty(
      "filterConfig",
    );
  });

  it("validates canonical span attribute filters before submit", () => {
    expect(
      isSpanAttrFilterValid([
        {
          column_id: "customer_tier",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "enterprise",
          },
        },
      ]),
    ).toBe(true);
    expect(
      isSpanAttrFilterValid([
        {
          column_id: "customer_tier",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "",
          },
        },
      ]),
    ).toBe(false);
  });

  it("hydrates canonical filters from the API into local form state", () => {
    const filters = transformFilterResponse({
      observation_type: ["llm"],
      span_attributes_filters: [
        {
          column_id: "customer_tier",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "enterprise",
          },
        },
      ],
    });

    expect(filters).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          property: "observationType",
          filterConfig: expect.objectContaining({ filterValue: "llm" }),
        }),
        expect.objectContaining({
          propertyId: "customer_tier",
          property: "attributes",
          filterConfig: {
            filterType: "text",
            filterOp: "equals",
            filterValue: "enterprise",
          },
        }),
      ]),
    );
  });

  it("normalizes alert list rows from API snake_case into UI fields", () => {
    const row = normalizeAlertListRow({
      id: "alert-1",
      metric_type: "Count of errors",
      last_triggered: "2026-05-24T00:00:00Z",
      no_of_alerts: 3,
      is_mute: true,
      filters: {
        observation_type: ["llm"],
        span_attributes_filters: [
          {
            column_id: "customer_tier",
            filter_config: {
              filter_type: "text",
              filter_op: "equals",
              filter_value: "enterprise",
            },
          },
        ],
      },
    });

    expect(row.metricType).toBe("Count of errors");
    expect(row.lastTriggered).toBe("2026-05-24T00:00:00Z");
    expect(row.noOfAlerts).toBe(3);
    expect(isAlertMuted(row)).toBe(true);
    expect(getAlertFilterValue(row)).toEqual([
      "Span Type is LLM",
      "Custom attribute is (customer_tier)",
    ]);
  });
});
