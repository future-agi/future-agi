import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "src/utils/test-utils";
import WidgetEditorView, * as editor from "../WidgetEditorView";

const h = vi.hoisted(() => ({
  dashboard: null,
  query: { mutate: vi.fn(), reset: vi.fn() },
  write: { mutate: vi.fn() },
  catalog: {
    metrics: [],
    isLoading: false,
    hasNextPage: false,
    fetchNextPage: vi.fn(),
  },
  agents: { data: [] },
  inventory: { filteredAttributes: [], inventoryControlProps: {} },
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardDetail: () => ({ data: h.dashboard, isLoading: false }),
  useDashboardQuery: () => h.query,
  useCreateWidget: () => h.write,
  useUpdateWidget: () => h.write,
  useDeleteWidget: () => h.write,
  useSimulationAgents: () => h.agents,
  usePropertyCatalog: () => h.catalog,
  useLegacyDashboardMetricsPaginated: () => h.catalog,
  isPropertyCatalogNotReadyError: () => false,
}));
vi.mock("react-router-dom", async (original) => ({
  ...(await original()),
  useParams: () => ({ dashboardId: "dashboard", widgetId: "saved-widget" }),
  useNavigate: () => vi.fn(),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace" }),
}));
vi.mock("../hooks/useCanEditDashboard", () => ({
  default: () => ({ canDelete: true, isReadOnly: false }),
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("react-apexcharts", () => ({ default: () => null }));
vi.mock("src/components/filter-value-label", () => ({
  default: () => null,
  useResolvedFilterOptions: () => ({ options: [] }),
  shouldShowFilterValueContinuation: () => false,
}));
vi.mock("src/sections/projects/LLMTracing/useCursorAttributeInventory", () => ({
  useLegacyCursorAttributeInventory: () => h.inventory,
  attributeInventoryKey: ({ key, type }) => `${key}:${type}`,
}));

const LABEL = "8900515c-ecac-417e-b0df-18fb7137cbee";
const LABEL_NAME = "e2e-dash10-0-mtvtc72j-numeric";
const EVAL = "11111111-1111-4111-8111-111111111111";
const COLUMN = "22222222-2222-4222-8222-222222222222";
const ATTRIBUTE = " customer.trace_id:start_time ";
const PROJECT_FILTER = {
  column_id: "project",
  property_id: "system_attribute:traces:project",
  display_name: "Project",
  source: "traces",
  output_type: "string",
  filter_config: {
    filter_type: "text",
    filter_op: "in",
    filter_value: ["a407da87-7a8c-4e8f-9605-6998b7a69470"],
    col_type: "SYSTEM_METRIC",
  },
};
const TRACES = {
  id: "trace_count",
  name: "trace_count",
  property_id: "system_attribute:traces:trace_count",
  display_name: "Traces",
  type: "system_metric",
  source: "traces",
  aggregation: "count_distinct",
};
// Literal saved query from D10 h baseline native-reads-writes-scope receipt 131.
// The expected wire is not derived from the hydration helper or query builder.
const SAVED_NUMERIC = {
  project_ids: [],
  time_range: { preset: "7D" },
  granularity: "day",
  metrics: [
    {
      id: LABEL,
      name: LABEL_NAME,
      property_id: `annotation:${LABEL}`,
      display_name: LABEL_NAME,
      type: "annotation_metric",
      source: "both",
      aggregation: "avg",
      label_id: LABEL,
      output_type: "numeric",
    },
    TRACES,
  ],
  filters: [
    PROJECT_FILTER,
    {
      column_id: LABEL,
      property_id: `annotation:${LABEL}`,
      display_name: LABEL_NAME,
      source: "both",
      output_type: "numeric",
      filter_config: {
        filter_type: "number",
        filter_op: "equals",
        filter_value: 25,
        col_type: "ANNOTATION",
      },
    },
  ],
  breakdowns: [
    {
      name: LABEL,
      property_id: `annotation:${LABEL}`,
      display_name: LABEL_NAME,
      type: "annotation_metric",
      source: "both",
      label_id: LABEL,
      output_type: "numeric",
    },
  ],
};

const freeze = (value) => {
  if (value && typeof value === "object") {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
};

const queryWith = (metric, breakdowns = []) => ({
  project_ids: [],
  time_range: { preset: "7D" },
  granularity: "day",
  metrics: [metric],
  filters: [],
  breakdowns,
});

// Change only the historically accepted item aliases, not opaque identities,
// filter-config wire fields, or the enclosing query configuration.
const camelItem = (item) => {
  const aliases = {
    display_name: "displayName",
    property_id: "propertyId",
    label_id: "labelId",
    column_id: "columnId",
    attribute_key: "attributeKey",
    attribute_type: "attributeType",
    data_type: "dataType",
    output_type: "outputType",
    eval_key: "evalKey",
    config_id: "configId",
  };
  const types = {
    annotation_metric: "annotationMetric",
    eval_metric: "evalMetric",
    custom_column: "customColumn",
    custom_attribute: "customAttribute",
    system_metric: "systemMetric",
  };
  return Object.fromEntries(
    Object.entries(item).map(([key, value]) => [
      aliases[key] || key,
      key === "type" ? types[value] || value : value,
    ]),
  );
};

async function captureReopen(queryConfig) {
  const original = structuredClone(queryConfig);
  h.dashboard = freeze({
    id: "dashboard",
    name: "Saved identities",
    widgets: [
      {
        id: "saved-widget",
        name: "Saved identity widget",
        query_config: queryConfig,
        chart_config: { chart_type: "table" },
      },
    ],
  });
  render(<WidgetEditorView />);
  // Run the real initialization effect, debounce and buildQueryConfig. Only
  // the data hooks are mocked; no serializer or hydration implementation is.
  await waitFor(() => expect(h.query.mutate).toHaveBeenCalledOnce(), {
    timeout: 2500,
  });
  expect(h.write.mutate).not.toHaveBeenCalled();
  expect(queryConfig).toEqual(original);
  const [request] = h.query.mutate.mock.calls[0];
  expect(request.refresh).toBe(false);
  expect(request.signal).toBeInstanceOf(AbortSignal);
  return request.queryConfig;
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(
    {},
    "",
    "/dashboard/dashboards/dashboard/widgets/saved-widget",
  );
  // Any unmocked browser request is a test error, never a service fallback.
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Unexpected network request");
    }),
  );
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(() => {
    throw new Error("Unexpected XMLHttpRequest");
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("saved WidgetEditor actual reopen payload", () => {
  it.each(["avg", "count", "count_distinct"])(
    "preserves the requested text annotation operation %s on reopen",
    async (aggregation) => {
      const metric = {
        id: LABEL,
        name: "Comment",
        display_name: "Comment",
        label_id: LABEL,
        type: "annotation_metric",
        source: "both",
        output_type: "text",
        aggregation,
      };
      const saved = queryWith({ ...metric, allowedAggregations: ["count", "count_distinct"] });
      expect(await captureReopen(saved)).toEqual(queryWith(metric));
    },
  );

  it.each(["snake", "camel"])(
    "retains recorded numeric annotation identity, types and filters (%s)",
    async (style) => {
      const saved = structuredClone(SAVED_NUMERIC);
      if (style === "camel") {
        saved.metrics = saved.metrics.map(camelItem);
        saved.breakdowns = saved.breakdowns.map(camelItem);
      }
      expect(await captureReopen(saved)).toEqual(SAVED_NUMERIC);
    },
  );

  const metricCases = [
    {
      title: "simulation eval with config and output key",
      saved: {
        id: "m0",
        name: EVAL,
        display_name: "Eval display",
        property_id: `eval_template:${EVAL}`,
        type: "eval_metric",
        source: "simulation",
        aggregation: "pass_rate",
        output_type: "PASS_FAIL",
        eval_key: "eval-empathy",
        config_id: EVAL,
      },
      expected: {
        id: EVAL,
        name: EVAL,
        display_name: "Eval display",
        property_id: `eval_template:${EVAL}`,
        type: "eval_metric",
        source: "simulation",
        aggregation: "pass_rate",
        output_type: "PASS_FAIL",
        eval_key: "eval-empathy",
        config_id: EVAL,
      },
    },
    {
      title: "dataset eval config routing",
      saved: {
        id: "m0",
        name: EVAL,
        display_name: "Dataset eval",
        property_id: `eval_template:${EVAL}`,
        type: "eval_metric",
        source: "datasets",
        aggregation: "avg",
        output_type: "SCORE",
        config_id: EVAL,
      },
      expected: {
        id: EVAL,
        name: EVAL,
        display_name: "Dataset eval",
        property_id: `eval_template:${EVAL}`,
        type: "eval_metric",
        source: "datasets",
        aggregation: "avg",
        output_type: "SCORE",
        config_id: EVAL,
      },
    },
    {
      title: "dataset boolean column identity",
      saved: {
        id: "m0",
        name: "Column display",
        column_id: COLUMN,
        display_name: "Column display",
        property_id: `dataset_column:${COLUMN}`,
        type: "custom_column",
        source: "datasets",
        aggregation: "true_rate",
        data_type: "boolean",
      },
      expected: {
        id: COLUMN,
        name: COLUMN,
        column_id: COLUMN,
        display_name: "Column display",
        property_id: `dataset_column:${COLUMN}`,
        type: "custom_column",
        source: "datasets",
        aggregation: "true_rate",
        data_type: "boolean",
      },
    },
    {
      title: "opaque numeric attribute key and explicit map family",
      saved: {
        id: "m0",
        name: "Attribute display",
        attribute_key: ATTRIBUTE,
        display_name: "Attribute display",
        property_id: `custom_attribute:${ATTRIBUTE}`,
        type: "custom_attribute",
        source: "traces",
        aggregation: "sum",
        attribute_type: "number",
        data_type: "string",
      },
      expected: {
        id: ATTRIBUTE,
        name: ATTRIBUTE,
        attribute_key: ATTRIBUTE,
        display_name: "Attribute display",
        property_id: `custom_attribute:${ATTRIBUTE}`,
        type: "custom_attribute",
        source: "traces",
        aggregation: "sum",
        attribute_type: "number",
      },
    },
    {
      title: "legacy system semantic name rather than m0 alias",
      saved: {
        id: "m0",
        name: "latency",
        display_name: "Latency",
        property_id: "system_attribute:traces:latency",
        type: "system_metric",
        source: "traces",
        aggregation: "avg",
        filters: [PROJECT_FILTER],
      },
      expected: {
        id: "latency",
        name: "latency",
        display_name: "Latency",
        property_id: "system_attribute:traces:latency",
        type: "system_metric",
        source: "traces",
        aggregation: "avg",
        filters: [PROJECT_FILTER],
      },
    },
  ];
  it.each(
    metricCases.flatMap((item) =>
      ["snake", "camel"].map((style) => ({ ...item, style })),
    ),
  )("preserves $title ($style)", async ({ saved, expected, style }) => {
    const wire = queryWith(
      style === "camel" ? camelItem(saved) : structuredClone(saved),
    );
    expect(await captureReopen(wire)).toEqual(queryWith(expected));
  });

  it.each(["snake", "camel"])(
    "retains eval breakdown output/config/key routing (%s)",
    async (style) => {
      const breakdown = {
        name: EVAL,
        display_name: "Eval choice",
        property_id: `eval_template:${EVAL}`,
        type: "eval_metric",
        source: "traces",
        output_type: "CHOICE",
        config_id: EVAL,
        eval_key: "eval-choice",
      };
      const expected = queryWith(TRACES, [breakdown]);
      const wire = queryWith(TRACES, [
        style === "camel" ? camelItem(breakdown) : breakdown,
      ]);
      expect(await captureReopen(structuredClone(wire))).toEqual(expected);
    },
  );

  // This checks conservation of retained saved bindings, not picker eligibility
  // or support for a new joined-breakdown adapter combination.
  it.each(
    [
      {
        title: "attribute",
        breakdown: {
          name: ATTRIBUTE,
          display_name: "Attribute",
          property_id: `custom_attribute:${ATTRIBUTE}`,
          type: "custom_attribute",
          source: "traces",
          attribute_key: ATTRIBUTE,
          attribute_type: "boolean",
        },
      },
      {
        title: "column",
        breakdown: {
          name: COLUMN,
          display_name: "Column",
          property_id: `dataset_column:${COLUMN}`,
          type: "custom_column",
          source: "datasets",
          column_id: COLUMN,
          data_type: "boolean",
        },
      },
    ].flatMap((item) =>
      ["snake", "camel"].map((style) => ({ ...item, style })),
    ),
  )(
    "retains typed $title breakdown routing ($style)",
    async ({ breakdown, style }) => {
      const metric = {
        id: "row_count",
        name: "row_count",
        display_name: "Rows",
        type: "system_metric",
        source: "datasets",
        aggregation: "count",
      };
      const expected = queryWith(
        breakdown.source === "datasets" ? metric : TRACES,
        [breakdown],
      );
      const saved = structuredClone(expected);
      saved.breakdowns = [style === "camel" ? camelItem(breakdown) : breakdown];
      expect(await captureReopen(saved)).toEqual(expected);
    },
  );

  it("retains legacy attribute breakdown wire without adding an identifier field", async () => {
    const saved = queryWith(TRACES, [
      {
        name: ATTRIBUTE,
        display_name: "Attribute",
        property_id: `custom_attribute:${ATTRIBUTE}`,
        type: "custom_attribute",
        source: "traces",
        attribute_type: "number",
      },
    ]);
    expect(await captureReopen(structuredClone(saved))).toEqual(saved);
  });

  it("retains legacy column breakdown wire without adding an identifier field", async () => {
    const saved = queryWith(TRACES, [
      {
        name: COLUMN,
        display_name: "Column",
        property_id: `dataset_column:${COLUMN}`,
        type: "custom_column",
        source: "datasets",
      },
    ]);
    expect(await captureReopen(structuredClone(saved))).toEqual(saved);
  });
});

describe("restoreWidgetQueryItem typed saved contract", () => {
  it("restores text capabilities without rewriting a legacy Average request", () => {
    const item = freeze({ name: LABEL, type: "annotation_metric", output_type: "text", aggregation: "avg" });
    expect(editor.restoreWidgetQueryItem(item)).toMatchObject({
      allowedAggregations: ["count", "count_distinct"],
      aggregation: "avg",
    });
  });
  const cases = [
    {
      type: "annotation_metric",
      key: "label_id",
      identity: LABEL,
      output_type: "numeric",
      dataType: "number",
      frontendType: "annotation",
    },
    {
      type: "annotation_metric",
      key: "label_id",
      identity: LABEL,
      output_type: "text",
      dataType: "string",
      frontendType: "annotation",
    },
    {
      type: "eval_metric",
      key: "name",
      identity: EVAL,
      output_type: "PASS_FAIL",
      dataType: "string",
      frontendType: "eval_metric",
    },
    {
      type: "custom_column",
      key: "column_id",
      identity: COLUMN,
      data_type: "boolean",
      dataType: "boolean",
      frontendType: "custom_column",
    },
    {
      type: "custom_attribute",
      key: "attribute_key",
      identity: ATTRIBUTE,
      attribute_type: "number",
      data_type: "string",
      dataType: "number",
      frontendType: "custom_attribute",
    },
    {
      type: "system_metric",
      key: "name",
      identity: "latency",
      data_type: "number",
      dataType: "number",
      frontendType: "system",
    },
  ];
  it.each(
    cases.flatMap((item) =>
      ["snake", "camel"].map((style) => ({ ...item, style })),
    ),
  )(
    "restores $type/$dataType without mutating the $style saved item",
    ({ key, identity, dataType, frontendType, style, ...fields }) => {
      const { type, output_type, data_type, attribute_type } = fields;
      const item = {
        id: "m0",
        name: "Backend name fallback",
        display_name: "Display only",
        type,
        [key]: identity,
        ...(output_type && { output_type }),
        ...(data_type && { data_type }),
        ...(attribute_type && { attribute_type }),
        aggregation: "count",
        filters: [structuredClone(PROJECT_FILTER)],
      };
      const saved = freeze(style === "camel" ? camelItem(item) : item);
      const original = structuredClone(saved);
      const result = editor.restoreWidgetQueryItem(saved, "datasets", "string");
      expect(result).toMatchObject({
        id: identity,
        name: "Display only",
        type: frontendType,
        source: "datasets",
        dataType,
        aggregation: "count",
        filters: original.filters,
      });
      if (output_type) expect(result.outputType).toBe(output_type);
      if (type === "custom_column")
        expect(result.columnDataType).toBe("boolean");
      expect(
        editor.restoreWidgetQueryItem(saved, "datasets", "string"),
      ).toEqual(result);
      expect(saved).toEqual(original);
    },
  );

  it("restores id-only annotation with a separate displayName", () => {
    expect(
      editor.restoreWidgetQueryItem({
        id: LABEL,
        displayName: "Old label",
        type: "annotation_metric",
        output_type: "star",
      }),
    ).toMatchObject({
      id: LABEL,
      name: "Old label",
      outputType: "star",
      source: "traces",
      dataType: "number",
    });
  });

  it("does not infer or repair an identity from property_id", () => {
    const saved = freeze({
      id: "m0",
      name: "explicit-name",
      label_id: "explicit-label",
      property_id: `annotation:${LABEL}`,
      display_name: "Display only",
      type: "annotation_metric",
      source: "both",
      output_type: "numeric",
    });
    expect(editor.restoreWidgetQueryItem(saved)).toMatchObject({
      id: "explicit-label",
      registryId: `annotation:${LABEL}`,
      name: "Display only",
      source: "both",
    });
    expect(
      editor.restoreWidgetQueryItem({
        type: "system_metric",
        property_id: "system_attribute:traces:latency",
        display_name: "Display only",
      }).id,
    ).toBeUndefined();
  });

  it("keeps explicit sources and caller datatype fallbacks for legacy items", () => {
    expect(
      editor.restoreWidgetQueryItem(
        { name: "status", type: "system_metric", source: "simulation" },
        "datasets",
        "string",
      ),
    ).toMatchObject({ id: "status", source: "simulation", dataType: "string" });
    expect(
      editor.restoreWidgetQueryItem({ name: "latency", type: "system_metric" }),
    ).toMatchObject({ id: "latency", source: "traces", dataType: "number" });
  });

  it.each(["annotation_metric", "eval_metric"])(
    "does not invent an output type for legacy %s",
    (type) => {
      expect(
        editor.restoreWidgetQueryItem(
          { name: LABEL, type, data_type: "number" },
          "traces",
          "string",
        ),
      ).toMatchObject({ dataType: "number", outputType: undefined });
      expect(
        editor.restoreWidgetQueryItem(
          { name: LABEL, type },
          "traces",
          "string",
        ),
      ).toMatchObject({ dataType: "string", outputType: undefined });
    },
  );
});
