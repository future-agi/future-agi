// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";

// TaskLivePreview's exported request builders do not need the heavy result
// renderer. Mock it so this contract test stays isolated from that renderer's
// module-scope browser storage state.
vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => null,
}));

import {
  buildApiFilterArray,
  buildTaskPreviewListParams,
} from "../TaskLivePreview";
import { convertNewToOld, convertOldToNew } from "../TaskFilterBar";

describe("TaskFilterBar voice-call filter contract", () => {
  it("requires a complete filtered voice-call preview page without exact-count mode", () => {
    const params = buildTaskPreviewListParams({
      rowType: "voiceCalls",
      projectId: "project-1",
      apiFilters: [
        {
          column_id: "call_status",
          filter_config: {
            filter_type: "text",
            filter_op: "in",
            filter_value: ["completed"],
            col_type: "SYSTEM_METRIC",
          },
        },
      ],
    });

    expect(params).toMatchObject({
      project_id: "project-1",
      page: 1,
      page_size: 50,
      cursor_mode: true,
    });
    expect(JSON.parse(params.filters)).toHaveLength(1);
    expect(params).not.toHaveProperty("allow_sampled");
  });

  it("requires a complete filtered trace preview page without exact-count mode", () => {
    const params = buildTaskPreviewListParams({
      rowType: "traces",
      projectId: "project-1",
      apiFilters: [{ column_id: "final_status" }],
    });

    expect(params).toMatchObject({
      project_id: "project-1",
      page_number: 0,
      page_size: 50,
      cursor_mode: true,
    });
    expect(params).not.toHaveProperty("allow_sampled");
  });

  it("uses signed bounded continuation for session previews", () => {
    const params = buildTaskPreviewListParams({
      rowType: "sessions",
      projectId: "project-1",
      apiFilters: [{ column_id: "final_status" }],
    });

    expect(params).toMatchObject({
      project_id: "project-1",
      page_number: 0,
      page_size: 50,
      cursor_mode: true,
    });
  });

  it("maps Live Preview Status to the normalized voice-list alias", () => {
    const formRows = convertNewToOld(
      [
        {
          field: "call_status",
          fieldName: "Status",
          fieldCategory: "system",
          fieldType: "string",
          apiColType: "SYSTEM_METRIC",
          operator: "in",
          value: ["ended"],
        },
      ],
      { rowType: "voiceCalls" },
    );

    expect(formRows).toEqual([
      expect.objectContaining({
        property: "call_status",
        propertyId: "call_status",
        fieldCategory: "system",
        apiColType: "SYSTEM_METRIC",
        filterConfig: {
          filterType: "text",
          filterOp: "in",
          filterValue: ["completed"],
        },
      }),
    ]);
    expect(buildApiFilterArray(formRows)).toEqual([
      {
        column_id: "call_status",
        filter_config: {
          filter_type: "text",
          filter_op: "in",
          filter_value: ["completed"],
          col_type: "SYSTEM_METRIC",
        },
      },
    ]);
  });

  it("maps displayed cost to the provider-normalized cost_cents alias", () => {
    const formRows = convertNewToOld(
      [
        {
          field: "cost_cents",
          fieldName: "Cost (cents)",
          fieldCategory: "system",
          fieldType: "number",
          apiColType: "SYSTEM_METRIC",
          operator: "equals",
          value: "12.2",
        },
      ],
      { rowType: "voiceCalls" },
    );

    expect(buildApiFilterArray(formRows)).toEqual([
      {
        column_id: "cost_cents",
        filter_config: {
          filter_type: "number",
          filter_op: "equals",
          filter_value: 12.2,
          col_type: "SYSTEM_METRIC",
        },
      },
    ]);

    expect(convertOldToNew(formRows, { rowType: "voiceCalls" })).toEqual([
      expect.objectContaining({
        field: "cost_cents",
        fieldLabel: "Cost (cents)",
        fieldType: "number",
        fieldCategory: "system",
        apiColType: "SYSTEM_METRIC",
        value: [12.2],
      }),
    ]);
  });

  it("maps the displayed provider Call ID to the voice-list system alias", () => {
    const formRows = convertNewToOld(
      [
        {
          field: "call_id",
          fieldName: "Call ID",
          fieldCategory: "system",
          fieldType: "text",
          apiColType: "SYSTEM_METRIC",
          operator: "in",
          value: "call_384d399921cd470931481ef565c",
        },
      ],
      { rowType: "voiceCalls" },
    );

    expect(buildApiFilterArray(formRows)).toEqual([
      {
        column_id: "call_id",
        filter_config: {
          filter_type: "text",
          filter_op: "in",
          filter_value: ["call_384d399921cd470931481ef565c"],
          col_type: "SYSTEM_METRIC",
        },
      },
    ]);
  });

  it("hydrates legacy total_cost drafts back to their displayed cents value", () => {
    const legacyRows = [
      {
        property: "total_cost",
        propertyId: "total_cost",
        fieldCategory: "system",
        apiColType: "SYSTEM_METRIC",
        filterConfig: {
          filterType: "number",
          filterOp: "equals",
          filterValue: 0.122,
        },
      },
    ];

    expect(convertOldToNew(legacyRows, { rowType: "voiceCalls" })).toEqual([
      expect.objectContaining({
        field: "cost_cents",
        fieldLabel: "Cost (cents)",
        value: [12.2],
      }),
    ]);
  });

  it("repairs legacy voice status rows without changing normal trace status", () => {
    const legacy = [
      {
        property: "status",
        propertyId: "status",
        fieldCategory: "system",
        apiColType: "SYSTEM_METRIC",
        filterConfig: {
          filterType: "text",
          filterOp: "in",
          filterValue: ["ended", "DONE"],
        },
      },
    ];

    expect(convertOldToNew(legacy, { rowType: "voiceCalls" })[0]).toMatchObject(
      {
        field: "call_status",
        fieldCategory: "system",
        apiColType: "SYSTEM_METRIC",
        value: ["completed"],
      },
    );
    expect(convertOldToNew(legacy, { rowType: "traces" })[0]).toMatchObject({
      field: "status",
      fieldCategory: "system",
      apiColType: "SYSTEM_METRIC",
    });
  });

  it("normalizes provider failure and connection aliases deterministically", () => {
    const aliases = ["ERROR", "cancelled", "no_answer", "ok"];
    const rows = convertNewToOld(
      [
        {
          field: "call_status",
          fieldCategory: "system",
          fieldType: "string",
          operator: "in",
          value: aliases,
        },
      ],
      { rowType: "voiceCalls" },
    );

    expect(rows[0].filterConfig.filterValue).toEqual([
      "failed",
      "dropped",
      "not-connected",
      "completed",
    ]);
  });
});

describe("TaskFilterBar structured and mixed filter contract", () => {
  const mixedPanelFilters = [
    {
      field: "final_status",
      fieldName: "final_status",
      fieldCategory: "attribute",
      fieldType: "string",
      apiColType: "SPAN_ATTRIBUTE",
      operator: "in",
      value: ["Rejected"],
    },
    {
      field: "customer.tags",
      fieldName: "customer.tags",
      fieldCategory: "attribute",
      fieldType: "array",
      apiColType: "SPAN_ATTRIBUTE",
      operator: "contains",
      value: ["vip", 3, true],
    },
    {
      field: "customer.context",
      fieldName: "customer.context",
      fieldCategory: "attribute",
      fieldType: "map",
      apiColType: "SPAN_ATTRIBUTE",
      operator: "contains",
      value: '{"tier":"vip","attempt":2}',
    },
  ];

  it("keeps text, array, and map rows independent when used together", () => {
    const formRows = convertNewToOld(mixedPanelFilters, {
      rowType: "traces",
    });

    expect(formRows).toHaveLength(3);
    expect(buildApiFilterArray(formRows)).toEqual([
      {
        column_id: "final_status",
        filter_config: {
          filter_type: "text",
          filter_op: "in",
          filter_value: ["Rejected"],
          col_type: "SPAN_ATTRIBUTE",
        },
      },
      {
        column_id: "customer.tags",
        filter_config: {
          filter_type: "array",
          filter_op: "contains",
          filter_value: ["vip", 3, true],
          col_type: "SPAN_ATTRIBUTE",
        },
      },
      {
        column_id: "customer.context",
        filter_config: {
          filter_type: "map",
          filter_op: "contains",
          filter_value: { attempt: 2, tier: "vip" },
          col_type: "SPAN_ATTRIBUTE",
        },
      },
    ]);
  });

  it("round-trips mixed typed attribute options into task preview requests", () => {
    const panelRows = [
      {
        field: "attempt",
        fieldName: "attempt",
        fieldCategory: "attribute",
        fieldType: "string",
        apiColType: "SPAN_ATTRIBUTE",
        operator: "in",
        value: ["1", 1, true],
        valueTypes: ["string", "number", "boolean"],
      },
    ];

    const formRows = convertNewToOld(panelRows, { rowType: "traces" });
    expect(formRows[0].filterConfig).toEqual({
      filterType: "text",
      filterOp: "in",
      filterValue: ["1", 1, true],
      attributeValueTypes: ["string", "number", "boolean"],
    });
    expect(buildApiFilterArray(formRows)).toEqual([
      {
        column_id: "attempt",
        filter_config: {
          filter_type: "text",
          filter_op: "in",
          filter_value: ["1", 1, true],
          col_type: "SPAN_ATTRIBUTE",
          attribute_value_types: ["string", "number", "boolean"],
        },
      },
    ]);
    expect(convertOldToNew(formRows, { rowType: "traces" })[0]).toMatchObject({
      field: "attempt",
      value: ["1", 1, true],
      valueTypes: ["string", "number", "boolean"],
    });
  });

  it("round-trips legacy json lists and objects without changing shape", () => {
    const legacyRows = [
      {
        property: "attributes",
        propertyId: "customer.tags",
        fieldCategory: "attribute",
        apiColType: "SPAN_ATTRIBUTE",
        filterConfig: {
          filterType: "json",
          filterOp: "contains",
          filterValue: ["vip", 3, true],
        },
      },
      {
        property: "attributes",
        propertyId: "customer.context",
        fieldCategory: "attribute",
        apiColType: "SPAN_ATTRIBUTE",
        filterConfig: {
          filterType: "json",
          filterOp: "equals",
          filterValue: { tier: "vip" },
        },
      },
    ];

    expect(convertOldToNew(legacyRows, { rowType: "traces" })).toEqual([
      expect.objectContaining({
        field: "customer.tags",
        fieldType: "array",
        value: ["vip", 3, true],
      }),
      expect.objectContaining({
        field: "customer.context",
        fieldType: "map",
        value: { tier: "vip" },
      }),
    ]);
  });
});
