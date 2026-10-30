import { describe, expect, it } from "vitest";
import { buildApiFilterFromPanelRow } from "src/api/contracts/filter-contract";
import {
  OBSERVE_FILTER_CONFIG,
  SESSION_FILTER_FIELDS,
  USER_FILTER_FIELDS,
  apiFilterToPanelRow,
  apiFiltersToPanelRows,
  filterPanelSettings,
  filtersForUnit,
  panelRowsToApiFilters,
} from "../observeFilterConfig";

const filter = (column_id, filter_config, extra = {}) => ({
  column_id,
  filter_config,
  ...extra,
});

describe("each view's filter panel", () => {
  it.each([
    // mode, tab, isSimulator → tab, namespace, value source, attribute source
    ["traces", "trace", false, "trace", "traces", "traces", undefined],
    ["traces", "spans", false, "spans", "traces", "traces", undefined],
    ["traces", "trace", true, "voiceCalls", "voice_calls", "traces", undefined],
    ["sessions", undefined, false, undefined, "sessions", "sessions", "traces"],
    ["users", undefined, false, undefined, "users", "sessions", "traces"],
  ])(
    "%s (tab %s, simulator %s) reads its own catalog",
    (mode, tab, isSimulator, effectiveTab, namespace, source, attributes) => {
      expect(filterPanelSettings({ mode, tab, isSimulator })).toEqual({
        effectiveFilterTab: effectiveTab,
        propertyNamespace: namespace,
        source,
        attributeSource: attributes,
      });
    },
  );

  it("configures each unit as its view's toolbar does", () => {
    expect(OBSERVE_FILTER_CONFIG).toEqual({
      traces: {
        source: "traces",
        propertyNamespace: "traces",
        tab: "trace",
        attributeSource: undefined,
        isSpansView: false,
        filterFields: undefined,
      },
      spans: {
        source: "traces",
        propertyNamespace: "traces",
        tab: "spans",
        attributeSource: undefined,
        isSpansView: true,
        filterFields: undefined,
      },
      sessions: {
        source: "sessions",
        propertyNamespace: "sessions",
        tab: undefined,
        attributeSource: "traces",
        isSpansView: false,
        filterFields: SESSION_FILTER_FIELDS,
      },
      users: {
        source: "sessions",
        propertyNamespace: "users",
        tab: undefined,
        attributeSource: "traces",
        isSpansView: false,
        filterFields: USER_FILTER_FIELDS,
      },
    });
  });
});

describe("panel rows and backend filters", () => {
  const STATUS = filter("status", {
    filter_type: "text",
    filter_op: "in",
    filter_value: ["ERROR"],
    col_type: "SYSTEM_METRIC",
  });
  const COST = filter("total_cost", {
    filter_type: "number",
    filter_op: "greater_than",
    filter_value: 1.5,
    col_type: "SYSTEM_METRIC",
  });
  const TRACE_ID = filter("trace_id", {
    filter_type: "text",
    filter_op: "in",
    filter_value: ["t1", "t2"],
  });
  const ANSWER = filter(
    "config-1",
    {
      filter_type: "categorical",
      filter_op: "in",
      filter_value: ["No"],
      col_type: "EVAL_METRIC",
    },
    { display_name: "Was it resolved?" },
  );

  it("reads a backend filter as the panel's row", () => {
    expect(apiFilterToPanelRow(COST)).toMatchObject({
      field: "total_cost",
      fieldCategory: "system",
      fieldType: "number",
      apiColType: "SYSTEM_METRIC",
      operator: "greater_than",
      value: "1.5",
    });
    // A native id is an exact-membership filter with no category.
    expect(apiFilterToPanelRow(TRACE_ID)).toMatchObject({
      field: "trace_id",
      fieldCategory: undefined,
      apiColType: undefined,
      value: ["t1", "t2"],
    });
    expect(apiFilterToPanelRow(ANSWER)).toMatchObject({
      fieldName: "Was it resolved?",
      fieldCategory: "eval",
      fieldType: "categorical",
    });
    expect(apiFiltersToPanelRows([])).toBeNull();
  });

  it("writes the rows back as the same backend filters", () => {
    for (const item of [STATUS, TRACE_ID, ANSWER]) {
      const [back] = panelRowsToApiFilters(apiFiltersToPanelRows([item]));
      expect(back).toEqual(
        buildApiFilterFromPanelRow(apiFilterToPanelRow(item)),
      );
      expect(back.column_id).toBe(item.column_id);
      expect(back.filter_config.filter_op).toBe(item.filter_config.filter_op);
      expect(back.filter_config.filter_value).toEqual(
        item.filter_config.filter_value,
      );
    }
  });
});

describe("a filter moving to another unit", () => {
  const STATUS = filter("status", { filter_op: "equals", filter_value: "OK" });
  const SPAN_NAME = filter("span_name", {
    filter_op: "equals",
    filter_value: "llm",
  });
  const TRACE_NAME = filter("name", { filter_op: "equals", filter_value: "x" });

  it("stays between traces and spans unless it names the other's rows", () => {
    expect(filtersForUnit([STATUS, SPAN_NAME], "spans", "traces")).toEqual({
      kept: [STATUS],
      dropped: [SPAN_NAME],
    });
    expect(filtersForUnit([STATUS, TRACE_NAME], "traces", "spans")).toEqual({
      kept: [STATUS],
      dropped: [TRACE_NAME],
    });
  });

  it("is dropped between traces, sessions and users", () => {
    const COST = filter("total_cost", {
      filter_op: "greater_than",
      filter_value: 1,
    });
    expect(filtersForUnit([COST], "sessions", "users")).toEqual({
      kept: [],
      dropped: [COST],
    });
    expect(filtersForUnit([COST], "users", "users").kept).toEqual([COST]);
  });
});
