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
import {
  convertNewToOld,
  convertOldToNew,
  taskFilterPanelSources,
} from "../TaskFilterBar";
import { VOICE_CALL_FILTER_FIELDS } from "src/sections/projects/LLMTracing/voiceCallFilterFields";

describe("TaskFilterBar voice-call filter contract", () => {
  it.each([
    ["spans", "traces"],
    ["traces", "traces"],
    ["sessions", "sessions"],
    ["voiceCalls", "traces"],
  ])(
    "keeps %s value semantics while browsing raw keys through spans",
    (rowType, source) => {
      expect(taskFilterPanelSources(rowType)).toEqual({
        source,
        attributeSource: "spans",
      });
    },
  );

  it("encodes and hydrates all 15 canonical Task picker fields", () => {
    const panelRows = VOICE_CALL_FILTER_FIELDS.map((field) => ({
      field: field.value,
      fieldName: field.label,
      fieldCategory: field.category,
      fieldType: field.type,
      apiColType: field.apiColType,
      operator: field.type === "number" ? "equals" : "in",
      value:
        field.value === "call_status"
          ? ["completed"]
          : field.type === "number"
            ? 1
            : ["sample"],
    }));

    const formRows = convertNewToOld(panelRows, { rowType: "voiceCalls" });
    const requests = buildApiFilterArray(formRows);

    expect(requests).toHaveLength(15);
    requests.forEach((request, index) => {
      const field = VOICE_CALL_FILTER_FIELDS[index];
      expect(request).toMatchObject({
        column_id: field.value,
        filter_config: {
          filter_type: field.type === "number" ? "number" : "text",
          filter_op: field.type === "number" ? "equals" : "in",
          col_type: "SYSTEM_METRIC",
        },
      });
    });
    expect(
      convertOldToNew(formRows, { rowType: "voiceCalls" }).map((row) => [
        row.field,
        row.fieldLabel,
      ]),
    ).toEqual(
      VOICE_CALL_FILTER_FIELDS.map((field) => [field.value, field.label]),
    );
  });

  it("requests one exact voice-call preview row without exact-count mode", () => {
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
      page_size: 1,
      cursor_mode: true,
    });
    expect(JSON.parse(params.filters)).toHaveLength(1);
    expect(params).not.toHaveProperty("allow_sampled");
  });

  it("requests one trace preview row with signed continuation", () => {
    const params = buildTaskPreviewListParams({
      rowType: "traces",
      projectId: "project-1",
      apiFilters: [{ column_id: "final_status" }],
    });

    expect(params).toMatchObject({
      project_id: "project-1",
      page_number: 0,
      page_size: 1,
      cursor_mode: true,
    });
    expect(params).not.toHaveProperty("allow_sampled");
  });

  it.each(["spans", "sessions"])(
    "requests one %s preview row with signed continuation",
    (rowType) => {
      const params = buildTaskPreviewListParams({
        rowType,
        projectId: "project-1",
        apiFilters: [{ column_id: "final_status" }],
      });

      expect(params).toMatchObject({
        project_id: "project-1",
        page_number: 0,
        page_size: 1,
        cursor_mode: true,
      });
    },
  );

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

  it("round-trips safe and Task-known legacy system aliases canonically", () => {
    const aliases = [
      ["duration_seconds", 42],
      ["agent_latency", 350],
      ["tokens", 10],
      ["total_tokens", 11],
      ["total_cost", 0.122],
      ["user_interruptions", 2],
      ["ai_interruptions", 3],
    ].map(([property, filterValue]) => ({
      property,
      propertyId: property,
      fieldCategory: "system",
      apiColType: "SYSTEM_METRIC",
      filterConfig: {
        filterType: "number",
        filterOp: "equals",
        filterValue,
      },
    }));

    const panelRows = convertOldToNew(aliases, { rowType: "voiceCalls" });
    expect(panelRows).toEqual([
      expect.objectContaining({ field: "duration", value: [42] }),
      expect.objectContaining({
        field: "avg_agent_latency_ms",
        value: [350],
      }),
      expect.objectContaining({
        field: "gen_ai.usage.total_tokens",
        value: [10],
      }),
      expect.objectContaining({
        field: "gen_ai.usage.total_tokens",
        value: [11],
      }),
      expect.objectContaining({ field: "cost_cents", value: [12.2] }),
      expect.objectContaining({ field: "user_interruption_count", value: [2] }),
      expect.objectContaining({ field: "ai_interruption_count", value: [3] }),
    ]);

    expect(
      convertNewToOld(panelRows, { rowType: "voiceCalls" }).map((row) => [
        row.property,
        row.filterConfig.filterValue,
      ]),
    ).toEqual([
      ["duration", 42],
      ["avg_agent_latency_ms", 350],
      ["gen_ai.usage.total_tokens", 10],
      ["gen_ai.usage.total_tokens", 11],
      ["cost_cents", 12.2],
      ["user_interruption_count", 2],
      ["ai_interruption_count", 3],
    ]);
  });

  it("canonicalizes safe and Task-known alias ids entering convertNewToOld", () => {
    const aliases = [
      ["duration_seconds", 42],
      ["agent_latency", 350],
      ["tokens", 10],
      ["total_tokens", 11],
      // Task panel values are already displayed in canonical cents even when
      // an old in-memory row still carries the total_cost id.
      ["total_cost", 12.2],
      ["user_interruptions", 2],
      ["ai_interruptions", 3],
    ].map(([field, value]) => ({
      field,
      fieldCategory: "system",
      fieldType: "number",
      apiColType: "SYSTEM_METRIC",
      operator: "equals",
      value,
    }));

    expect(
      convertNewToOld(aliases, { rowType: "voiceCalls" }).map((row) => [
        row.property,
        row.filterConfig.filterValue,
      ]),
    ).toEqual([
      ["duration", 42],
      ["avg_agent_latency_ms", 350],
      ["gen_ai.usage.total_tokens", 10],
      ["gen_ai.usage.total_tokens", 11],
      ["cost_cents", 12.2],
      ["user_interruption_count", 2],
      ["ai_interruption_count", 3],
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

  it("hydrates Task transition and unknown statuses as in-progress", () => {
    const legacy = [
      {
        property: "call_status",
        propertyId: "call_status",
        fieldCategory: "system",
        apiColType: "SYSTEM_METRIC",
        filterConfig: {
          filterType: "text",
          filterOp: "in",
          filterValue: [
            "initiated",
            "processing",
            "scheduled",
            "future-provider-transition",
          ],
        },
      },
    ];

    expect(convertOldToNew(legacy, { rowType: "voiceCalls" })).toEqual([
      expect.objectContaining({
        field: "call_status",
        fieldCategory: "system",
        apiColType: "SYSTEM_METRIC",
        value: ["in-progress"],
      }),
    ]);
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

  it("keeps an explicitly selected call.status span attribute raw", () => {
    const panelRows = [
      {
        field: "call.status",
        fieldName: "call.status",
        fieldCategory: "attribute",
        fieldType: "string",
        apiColType: "SPAN_ATTRIBUTE",
        operator: "in",
        value: ["ended", "ringing"],
      },
    ];

    const formRows = convertNewToOld(panelRows, { rowType: "voiceCalls" });
    expect(formRows).toEqual([
      expect.objectContaining({
        property: "attributes",
        propertyId: "call.status",
        fieldCategory: "attribute",
        apiColType: "SPAN_ATTRIBUTE",
        filterConfig: expect.objectContaining({
          filterValue: ["ended", "ringing"],
        }),
      }),
    ]);
    expect(buildApiFilterArray(formRows)).toEqual([
      {
        column_id: "call.status",
        filter_config: {
          filter_type: "text",
          filter_op: "in",
          filter_value: ["ended", "ringing"],
          col_type: "SPAN_ATTRIBUTE",
        },
      },
    ]);
    expect(convertOldToNew(formRows, { rowType: "voiceCalls" })).toEqual([
      expect.objectContaining({
        field: "call.status",
        fieldCategory: "attribute",
        apiColType: "SPAN_ATTRIBUTE",
        value: ["ended", "ringing"],
      }),
    ]);
  });

  it("keeps raw cost_cents and call_id distinct from same-id system fields", () => {
    const panelRows = [
      {
        field: "cost_cents",
        fieldName: "cost_cents",
        fieldCategory: "attribute",
        fieldType: "number",
        apiColType: "SPAN_ATTRIBUTE",
        operator: "equals",
        value: 12.2,
      },
      {
        field: "call_id",
        fieldName: "call_id",
        fieldCategory: "attribute",
        fieldType: "string",
        apiColType: "SPAN_ATTRIBUTE",
        operator: "in",
        value: ["raw-call-id"],
      },
    ];

    const formRows = convertNewToOld(panelRows, { rowType: "voiceCalls" });
    expect(buildApiFilterArray(formRows)).toEqual([
      {
        column_id: "cost_cents",
        filter_config: {
          filter_type: "number",
          filter_op: "equals",
          filter_value: 12.2,
          col_type: "SPAN_ATTRIBUTE",
        },
      },
      {
        column_id: "call_id",
        filter_config: {
          filter_type: "text",
          filter_op: "in",
          filter_value: ["raw-call-id"],
          col_type: "SPAN_ATTRIBUTE",
        },
      },
    ]);
    expect(convertOldToNew(formRows, { rowType: "voiceCalls" })).toEqual([
      expect.objectContaining({
        field: "cost_cents",
        fieldCategory: "attribute",
        apiColType: "SPAN_ATTRIBUTE",
        value: [12.2],
      }),
      expect.objectContaining({
        field: "call_id",
        fieldCategory: "attribute",
        apiColType: "SPAN_ATTRIBUTE",
        value: ["raw-call-id"],
      }),
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

  it("round-trips registry identity independently from the native task field", () => {
    const panelRows = [
      {
        field: "model",
        registryId: "custom_attribute:model",
        fieldName: "Custom model",
        fieldCategory: "attribute",
        fieldType: "string",
        apiColType: "SPAN_ATTRIBUTE",
        operator: "equals",
        value: "tenant-model",
      },
    ];

    const formRows = convertNewToOld(panelRows, { rowType: "traces" });
    expect(formRows[0]).toMatchObject({
      property: "attributes",
      propertyId: "model",
      registryId: "custom_attribute:model",
    });
    expect(convertOldToNew(formRows, { rowType: "traces" })[0]).toMatchObject({
      field: "model",
      registryId: "custom_attribute:model",
    });
    expect(buildApiFilterArray(formRows)[0]).toMatchObject({
      column_id: "model",
      property_id: "custom_attribute:model",
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
