import { describe, expect, it } from "vitest";

import { toFormRows, toPanelRows } from "../components/alertFilterRows";
import { convertFiltersToPayload } from "../common";
import { transformFilterResponse } from "../components/validation";

// Swapping the alert form's filter UI must not change a single byte of what
// the API receives. Round-trip a saved payload through the panel and back.
const roundTrip = (payload) =>
  convertFiltersToPayload(
    toFormRows(toPanelRows(transformFilterResponse(payload))),
  );

describe("alert filter bar round-trip", () => {
  it("preserves span types and attribute filters", () => {
    const payload = {
      observation_type: ["llm", "tool"],
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
    };

    expect(roundTrip(payload)).toEqual(payload);
  });

  it("keeps the stored operator verbatim rather than rewriting equals to in", () => {
    const payload = {
      observation_type: [],
      span_attributes_filters: [
        {
          column_id: "region",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "us-east",
            col_type: "SPAN_ATTRIBUTE",
          },
        },
      ],
    };

    const result = roundTrip(payload);
    expect(result.span_attributes_filters[0].filter_config.filter_op).toBe(
      "equals",
    );
    expect(result).toEqual(payload);
  });

  it("preserves a stored type that disagrees with the attribute's real type", () => {
    // Saved as text on an attribute that holds numbers. Coercing the stored
    // type would change the payload and could invalidate the operator.
    const payload = {
      observation_type: [],
      span_attributes_filters: [
        {
          column_id: "retry_count",
          filter_config: {
            filter_type: "text",
            filter_op: "contains",
            filter_value: "5",
            col_type: "SPAN_ATTRIBUTE",
          },
        },
      ],
    };

    expect(roundTrip(payload)).toEqual(payload);
  });

  it("round-trips numeric and boolean attribute types", () => {
    const payload = {
      observation_type: [],
      span_attributes_filters: [
        {
          column_id: "confidence_score",
          filter_config: {
            filter_type: "number",
            filter_op: "greater_than",
            filter_value: 0.8,
            col_type: "SPAN_ATTRIBUTE",
          },
        },
        {
          column_id: "escalated_to_human",
          filter_config: {
            filter_type: "boolean",
            filter_op: "equals",
            filter_value: true,
            col_type: "SPAN_ATTRIBUTE",
          },
        },
      ],
    };

    expect(roundTrip(payload)).toEqual(payload);
  });

  it("collapses span type rows into one panel row and explodes them back", () => {
    const formRows = transformFilterResponse({
      observation_type: ["llm", "tool", "agent"],
      span_attributes_filters: [],
    });

    const panelRows = toPanelRows(formRows);
    expect(panelRows).toHaveLength(1);
    expect(panelRows[0].value).toEqual(["llm", "tool", "agent"]);

    expect(toFormRows(panelRows)).toHaveLength(3);
  });

  it("maps a single-value `in` back to the legacy scalar `equals`", () => {
    // The panel has no scalar equals for strings, so hydration turns a saved
    // `equals` into `in` + [value]. One value must come back as it was stored.
    expect(
      toFormRows([
        {
          field: "customer_tier",
          fieldType: "text",
          operator: "in",
          value: ["enterprise"],
        },
      ])[0].filterConfig,
    ).toEqual({
      filterType: "text",
      filterOp: "equals",
      filterValue: "enterprise",
    });
  });

  it("leaves a genuinely multi-value `in` alone", () => {
    // Collapsing this to `equals` would silently drop every value but the first.
    expect(
      toFormRows([
        {
          field: "customer_tier",
          fieldType: "text",
          operator: "in",
          value: ["enterprise", "premium"],
        },
      ])[0].filterConfig,
    ).toEqual({
      filterType: "text",
      filterOp: "in",
      filterValue: ["enterprise", "premium"],
    });
  });

  it("bridges boolean values between the panel and the API", () => {
    // The panel's boolean control works in "true"/"false"; the API takes a
    // native bool and silently drops the condition given anything else.
    const saved = transformFilterResponse({
      observation_type: [],
      span_attributes_filters: [
        {
          column_id: "cache_hit",
          filter_config: {
            filter_type: "boolean",
            filter_op: "equals",
            filter_value: true,
            col_type: "SPAN_ATTRIBUTE",
          },
        },
      ],
    });

    expect(toPanelRows(saved)[0].value).toBe("true");
    expect(toFormRows(toPanelRows(saved))[0].filterConfig.filterValue).toBe(
      true,
    );
  });

  it("drops attribute rows with no selected property", () => {
    expect(toPanelRows([{ property: "attributes", propertyId: "" }])).toEqual(
      [],
    );
  });

  it("normalises stored type spellings through the contract's aliases", () => {
    // `str`, `int`, `bool` are all live spellings in filter_contract.json.
    const rowFor = (filterType) =>
      toPanelRows([
        {
          property: "attributes",
          propertyId: "attr",
          filterConfig: { filterType, filterOp: "equals", filterValue: "x" },
        },
      ])[0];

    expect(rowFor("str").fieldType).toBe("text");
    expect(rowFor("int").fieldType).toBe("number");
    expect(rowFor("bool").fieldType).toBe("boolean");
    expect(rowFor("float").fieldType).toBe("number");
  });
});
