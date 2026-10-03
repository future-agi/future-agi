import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "src/utils/test-utils";
import { ANALYTICS_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import { QUERY_FAILED_RETRY_MESSAGE } from "src/utils/queryReadState";
import { serializeFilterListForApi } from "src/api/contracts/filter-contract";
import { combineGraphFilters } from "src/sections/projects/LLMTracing/GraphSection/graphFilterUtils";
import { toBackendFilters } from "src/sections/projects/LLMTracing/common";
import {
  parseUrlValue,
  stringifyUrlValue,
} from "src/routes/hooks/use-url-state";

// All requests are synthetic. Never initialize auth, inspect browser storage,
// or call a live service from this matrix.
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  hostedRows: null,
}));
vi.mock("src/utils/axios", () => ({
  readQuery: mocks.get,
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    project: {
      getProjectById: (id) => `/tracer/project/${id}/`,
      projectExperimentDetail: (id) => `/tracer/project/${id}/`,
      getTracesForObserveProject: () => "/tracer/trace/list_traces_of_session/",
      getSpansForObserveProject: () =>
        "/tracer/observation-span/list_spans_observe/",
      projectSessionList: () => "/tracer/trace-session/list_sessions/",
      getTrace: (id) => `/tracer/trace/${id}/`,
      traceSession: "/tracer/trace-session/",
    },
  },
}));
vi.mock("src/sections/tasks/components/TaskFilterBar", () => ({
  default: ({ setValue, toolbarStart }) => (
    <>
      {toolbarStart}
      {mocks.hostedRows && (
        <button
          type="button"
          onClick={() => setValue("filters", mocks.hostedRows)}
        >
          Apply hosted filters
        </button>
      )}
    </>
  ),
}));
vi.mock("src/sections/evals/components/DatasetTestMode", () => ({
  JsonValueTree: () => null,
}));
vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/SpanRowList", () => ({
  default: () => null,
}));
vi.mock("src/components/draggable-col-resizer", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/inline-audio/inline-row-audio", () => ({
  InlineAudio: () => null,
  RecordingGroup: () => null,
}));
vi.mock("src/sections/evals/hooks/useErrorLocalizerPoll", () => ({
  default: () => ({ state: {}, start: vi.fn() }),
}));
vi.mock("src/sections/evals/hooks/useCompositeEval", () => ({
  useExecuteCompositeEvalAdhoc: () => ({ mutateAsync: mocks.post }),
}));
vi.mock(
  "src/sections/evals/components/useExactEvalAttributeFields",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useExactEvalAttributeFields: () => ({
      data: [],
      queryReadState: "complete",
      isFetching: false,
    }),
  }),
);

import TaskLivePreview, { buildApiFilterArray } from "../TaskLivePreview";
import TracingTestMode from "src/sections/evals/components/TracingTestMode";

const PROJECT = "00000000-0000-4000-8000-000000000251";
const ENDPOINTS = {
  spans: "/tracer/observation-span/list_spans_observe/",
  traces: "/tracer/trace/list_traces_of_session/",
  sessions: "/tracer/trace-session/list_sessions/",
};
const wire = (key, type, operator, value, extra = {}) => ({
  column_id: key,
  property_id: `custom_attribute:${key}`,
  filter_config: {
    filter_type: type,
    filter_op: operator,
    filter_value: value,
    col_type: "SPAN_ATTRIBUTE",
    ...extra,
  },
});
// Synthetic edge cases, separate from the typed fixture matrix.
const FILTERS = [
  wire("company_id", "text", "in", ["00000001", "00000002"], {
    attribute_value_types: ["string", "string"],
  }),
  wire("prompt_slug", "text", "in", [
    "fixture_prompt_a",
    "fixture_prompt_b",
    "fixture_prompt_c",
  ]),
  {
    column_id: "total_cost",
    filter_config: {
      filter_type: "number",
      filter_op: "greater_than",
      filter_value: 0.01,
      col_type: "SYSTEM_METRIC",
    },
  },
  wire("call_id", "array", "contains", [
    "0000000001",
    "0000000002",
    "79",
    "call-4",
    "call-5",
    "call-6",
    "call-7",
  ]),
  wire("is_test", "boolean", "equals", false),
  wire("retry_count", "number", "between", [0, 2.5]),
  wire("metadata.config", "map", "contains", {
    enabled: false,
    retries: 2,
    region: "001",
  }),
  wire("mixed_scalar", "text", "in", ["001", 1, true], {
    attribute_value_types: ["string", "number", "boolean"],
  }),
  wire("catalog.attribute.251,retained", "text", "is_null", null),
  {
    column_id: "created_at",
    filter_config: {
      filter_type: "datetime",
      filter_op: "between",
      filter_value: ["2025-09-04T22:31:47.000Z", "2026-09-05T07:00:00.000Z"],
      col_type: "SYSTEM_METRIC",
    },
  },
];

// Keys/types follow the sanitized fixture inventory. Values are synthetic
// test inputs, never production matching results. Keep all ten custom fields;
// the independent date range must not replace or count as a custom field.
const CATALOG_CUSTOM_FILTERS = [
  wire("company_id", "text", "in", ["00000001", "00000002"], {
    attribute_value_types: ["string", "string"],
  }),
  wire("prompt_slug", "text", "in", ["fixture_prompt_a", "fixture_prompt_b"]),
  wire("agent.duration_s", "number", "between", [0, 2.5]),
  wire("interruption_latency_s", "number", "greater_than", 0.125),
  wire("fixture.attribute_014", "boolean", "equals", false),
  wire("lk.function_tool.is_error", "boolean", "equals", true),
  wire("lk.function_tools", "array", "contains", [
    "fixture_tool_a",
    "fixture_tool_b",
  ]),
  wire("lk.provider_request_ids", "array", "contains", [
    "001",
    "fixture_request_b",
  ]),
  wire("tag.tags", "array", "contains", ["fixture_tag_a", "fixture_tag_b"]),
  // Catalog resolves mixed number/string call_id as JSON, not an ARRAY.
  // Scalar-list transport uses text + explicit per-value type witnesses.
  wire("call_id", "text", "in", [123, "123", "00123", 0], {
    attribute_value_types: ["number", "string", "string", "number"],
  }),
];
const CATALOG_DATE_RANGES = [
  ["7D", ["2026-08-28T12:00:00.000Z", "2026-09-04T12:00:00.000Z"]],
  ["30D", ["2026-08-05T12:00:00.000Z", "2026-09-04T12:00:00.000Z"]],
  ["12M", ["2025-09-04T12:00:00.000Z", "2026-09-04T12:00:00.000Z"]],
];

const formRows = (filters) =>
  filters.map(({ column_id, property_id, filter_config: config }) => ({
    property: config.col_type === "SPAN_ATTRIBUTE" ? "attributes" : column_id,
    propertyId: column_id,
    registryId: property_id,
    apiColType: config.col_type,
    filterConfig: {
      filterType: config.filter_type,
      filterOp: config.filter_op,
      filterValue: config.filter_value,
      ...(config.attribute_value_types
        ? { attributeValueTypes: config.attribute_value_types }
        : {}),
    },
  }));
const page = (cursor = null) => ({
  data: {
    status: true,
    result: {
      config: [],
      table: [],
      metadata: {
        total_rows: 0,
        has_more: cursor !== null,
        next_cursor: cursor,
      },
    },
  },
});

function TaskHarness({ rowType, filters, dateRange }) {
  const { control } = useForm({
    defaultValues: {
      rowType,
      filters: formRows(filters),
      startDate: dateRange?.[0] ?? null,
      endDate: dateRange?.[1] ?? null,
      evalsDetails: [],
    },
  });
  return <TaskLivePreview control={control} projectId={PROJECT} />;
}
TaskHarness.propTypes = {
  rowType: PropTypes.string,
  filters: PropTypes.array,
  dateRange: PropTypes.arrayOf(PropTypes.string),
};

const mounted = [];
function mountPreview(
  surface,
  rowType,
  filters,
  dateRange,
  hostsFilter = false,
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      {surface === "Tasks" ? (
        <TaskHarness
          rowType={rowType}
          filters={filters}
          dateRange={dateRange}
        />
      ) : (
        <TracingTestMode
          templateId="offline-fixture-only"
          initialProjectId={PROJECT}
          initialRowType={rowType}
          hostsFilter={hostsFilter}
          localFilters={buildApiFilterArray(
            formRows(filters),
            dateRange?.[0],
            dateRange?.[1],
          )}
          variables={["input"]}
        />
      )}
    </QueryClientProvider>,
  );
  mounted.push({ client, view });
}
const matrix = ["Tasks", "Eval"].flatMap((surface) =>
  Object.keys(ENDPOINTS).map((rowType) => [surface, rowType]),
);
const listCalls = (rowType) =>
  mocks.get.mock.calls.filter(([url]) => url === ENDPOINTS[rowType]);
const assertCompanyStrings = (filters) => {
  const company = filters.find((filter) => filter.column_id === "company_id");
  expect(company.filter_config.filter_value).toEqual(["00000001", "00000002"]);
  expect(
    company.filter_config.filter_value.map((value) => typeof value),
  ).toEqual(["string", "string"]);
};
const assertWire = (rowType, filters, surface) => {
  const [, config] = listCalls(rowType)[0];
  expect(config.params).toMatchObject({
    project_id: PROJECT,
    page_number: 0,
    cursor_mode: true,
    page_size: surface === "Tasks" ? 1 : 50,
  });
  expect(JSON.parse(config.params.filters)).toEqual(filters);
  assertCompanyStrings(JSON.parse(config.params.filters));
};

describe("offline mixed-filter preview matrix", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.hostedRows = null;
    mocks.post.mockImplementation(() => {
      throw new Error(
        "Mutation/evaluation execution is forbidden in this matrix",
      );
    });
    mocks.get.mockImplementation(async (url) => {
      if (url === `/tracer/project/${PROJECT}/`)
        return { data: { result: { id: PROJECT, source: "api" } } };
      if (Object.values(ENDPOINTS).includes(url)) return page();
      throw new Error(`Unexpected read: ${url}`);
    });
  });
  afterEach(() => {
    for (const { view, client } of mounted.splice(0)) {
      view.unmount();
      client.clear();
    }
    expect(mocks.post).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it.each(
    matrix.flatMap(([surface, rowType]) => [
      [surface, rowType, "annotation-named", ["annotator", "my_annotations"]],
      [surface, rowType, "ID-named", ["trace_id", "span_id", "session"]],
    ]),
  )(
    "reserved raw typed filters reach %s %s %s requests and continuation",
    async (surface, rowType, _family, keys) => {
      const filters = keys.map((key, index) =>
        index % 2 === 0
          ? wire(key, "text", "in", ["001", 1, false, "x".repeat(4097)], {
              attribute_value_types: ["string", "number", "boolean", "string"],
            })
          : wire(key, "text", "not_in", [true, "false", 0], {
              attribute_value_types: ["boolean", "string", "number"],
            }),
      );
      const snapshot = structuredClone(filters);
      const defaultGet = mocks.get.getMockImplementation();
      let filteredReads = 0;
      mocks.get.mockImplementation((url, config) => {
        if (
          url !== ENDPOINTS[rowType] ||
          !JSON.parse(config.params.filters).some(
            (filter) => filter.filter_config.col_type === "SPAN_ATTRIBUTE",
          )
        )
          return defaultGet(url, config);
        filteredReads += 1;
        return Promise.resolve(
          page(filteredReads === 1 ? "reserved-raw-cursor" : null),
        );
      });
      if (surface === "Eval") mocks.hostedRows = formRows(filters);
      mountPreview(surface, rowType, filters, undefined, surface === "Eval");
      if (surface === "Eval") {
        await act(async () =>
          screen.getByRole("button", { name: "Apply hosted filters" }).click(),
        );
      }
      await waitFor(() => expect(filteredReads).toBe(2));
      const requests = listCalls(rowType).filter(([, config]) =>
        JSON.parse(config.params.filters).some(
          (filter) => filter.filter_config.col_type === "SPAN_ATTRIBUTE",
        ),
      );
      expect(requests).toHaveLength(2);
      for (const [, config] of requests) {
        const actual = JSON.parse(config.params.filters);
        expect(
          actual.filter(
            (filter) => filter.filter_config.col_type === "SPAN_ATTRIBUTE",
          ),
        ).toEqual(filters);
        const dates = actual.filter(
          (filter) => filter.column_id === "created_at",
        );
        expect(dates).toHaveLength(surface === "Eval" ? 1 : 0);
        if (surface === "Eval") {
          expect(dates[0].filter_config).toMatchObject({
            filter_type: "datetime",
            filter_op: "between",
          });
          expect(dates[0]).toEqual(
            JSON.parse(requests[0][1].params.filters).find(
              (filter) => filter.column_id === "created_at",
            ),
          );
          expect(
            screen.getByRole("button", { name: "Past 30D" }),
          ).toBeVisible();
        }
        expect(config.params).toMatchObject({
          project_id: PROJECT,
          cursor_mode: true,
          page_size: surface === "Tasks" ? 1 : 50,
        });
      }
      expect(requests[0][1].params.page_number).toBe(0);
      expect(requests[1][1].params.cursor).toBe("reserved-raw-cursor");
      expect(requests[1][1].params).not.toHaveProperty("page_number");
      expect(filters).toEqual(snapshot);
    },
  );

  describe("sanitized fixture keys with synthetic values and independent dates", () => {
    it.each(
      matrix.flatMap(([surface, rowType]) =>
        CATALOG_DATE_RANGES.map(([window, range]) => [
          surface,
          rowType,
          window,
          range,
        ]),
      ),
    )(
      "%s %s preserves ten custom attributes plus %s date through continuation",
      async (surface, rowType, _window, dateRange) => {
        const customFilters = structuredClone(CATALOG_CUSTOM_FILTERS);
        const inputSnapshot = JSON.stringify({ customFilters, dateRange });
        const expected = [
          ...structuredClone(customFilters),
          {
            column_id: "created_at",
            filter_config: {
              filter_type: "datetime",
              filter_op: "between",
              filter_value: [...dateRange],
            },
          },
        ];
        const defaultGet = mocks.get.getMockImplementation();
        let listCount = 0;
        mocks.get.mockImplementation((url, config) => {
          if (url !== ENDPOINTS[rowType]) return defaultGet(url, config);
          listCount += 1;
          return Promise.resolve(
            page(listCount === 1 ? "catalog-date-cursor" : null),
          );
        });

        mountPreview(surface, rowType, customFilters, dateRange);
        await waitFor(() => expect(listCalls(rowType)).toHaveLength(2));
        await waitFor(() =>
          expect(screen.queryByRole("progressbar")).not.toBeInTheDocument(),
        );
        assertWire(rowType, expected, surface);
        for (const [, config] of listCalls(rowType)) {
          const actual = JSON.parse(config.params.filters);
          expect(actual).toEqual(expected);
          expect(actual).toHaveLength(11);
          const custom = actual.filter(
            (filter) => filter.filter_config.col_type === "SPAN_ATTRIBUTE",
          );
          expect(custom).toHaveLength(10);
          expect(new Set(custom.map((filter) => filter.column_id)).size).toBe(
            10,
          );
          expect(
            actual.filter((filter) => filter.column_id === "created_at"),
          ).toEqual([expected[10]]);
          assertCompanyStrings(actual);
          const call = actual.find(
            (filter) => filter.column_id === "call_id",
          ).filter_config;
          expect(call.filter_type).toBe("text");
          expect(call.filter_op).toBe("in");
          expect(call.filter_value).toEqual([123, "123", "00123", 0]);
          expect(call.attribute_value_types).toEqual([
            "number",
            "string",
            "string",
            "number",
          ]);
          expect(call.filter_value.map((value) => typeof value)).toEqual(
            call.attribute_value_types,
          );
        }
        expect(listCalls(rowType)[1][1].params).toMatchObject({
          cursor: "catalog-date-cursor",
          cursor_mode: true,
          project_id: PROJECT,
          page_size: surface === "Tasks" ? 1 : 50,
        });
        expect(listCalls(rowType)[1][1].params).not.toHaveProperty(
          "page_number",
        );
        expect(JSON.stringify({ customFilters, dateRange })).toBe(
          inputSnapshot,
        );
        expect(
          screen.queryByPlaceholderText("Loading columns..."),
        ).not.toBeInTheDocument();
        expect(
          screen.queryByRole("button", { name: "Continue search" }),
        ).not.toBeInTheDocument();
        expect(
          screen.queryByText(QUERY_FAILED_RETRY_MESSAGE),
        ).not.toBeInTheDocument();
        if (surface === "Tasks")
          expect(screen.getByText("No matching rows")).toBeVisible();
      },
    );
  });

  it.each(
    matrix.flatMap(([surface, rowType]) =>
      [1, 5, 10].map((count) => [surface, rowType, count]),
    ),
  )(
    "%s %s preserves all %i mixed filters and settles terminal empty state",
    async (surface, rowType, count) => {
      const filters = structuredClone(FILTERS.slice(0, count));
      const snapshot = JSON.stringify(filters);
      mountPreview(surface, rowType, filters);
      await waitFor(() => expect(listCalls(rowType)).toHaveLength(1));
      await waitFor(() =>
        expect(screen.queryByRole("progressbar")).not.toBeInTheDocument(),
      );
      assertWire(rowType, filters, surface);
      expect(JSON.stringify(filters)).toBe(snapshot);
      expect(
        screen.queryByPlaceholderText("Loading columns..."),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Continue search" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText(QUERY_FAILED_RETRY_MESSAGE),
      ).not.toBeInTheDocument();
      if (surface === "Tasks")
        expect(screen.getByText("No matching rows")).toBeVisible();
    },
  );

  it.each(matrix)(
    "%s %s settles a 10-filter failure without claiming empty results",
    async (surface, rowType) => {
      const defaultGet = mocks.get.getMockImplementation();
      mocks.get.mockImplementation((url, config) =>
        url === ENDPOINTS[rowType]
          ? Promise.reject(new Error("private backend query detail"))
          : defaultGet(url, config),
      );
      mountPreview(surface, rowType, FILTERS);
      expect(await screen.findByText(QUERY_FAILED_RETRY_MESSAGE)).toBeVisible();
      assertWire(rowType, FILTERS, surface);
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
      expect(
        screen.queryByPlaceholderText("Loading columns..."),
      ).not.toBeInTheDocument();
      expect(screen.queryByText("No matching rows")).not.toBeInTheDocument();
      expect(
        screen.queryByText("private backend query detail"),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("button", {
          name: surface === "Tasks" ? "Retry search" : "Retry",
          exact: true,
        }),
      ).toBeEnabled();
    },
  );

  it.each(matrix)(
    "%s %s preserves all 10 filters when a timed-out continuation resumes",
    async (surface, rowType) => {
      vi.useFakeTimers();
      const defaultGet = mocks.get.getMockImplementation();
      let listCount = 0;
      let pendingSignal;
      mocks.get.mockImplementation((url, config) => {
        if (url !== ENDPOINTS[rowType]) return defaultGet(url, config);
        listCount += 1;
        if (listCount === 1)
          return Promise.resolve(page("unconsumed-matrix-cursor"));
        if (listCount === 2) {
          pendingSignal = config.signal;
          return new Promise(() => {});
        }
        return Promise.resolve(page());
      });
      mountPreview(surface, rowType, FILTERS);
      await act(async () =>
        vi.advanceTimersByTimeAsync(ANALYTICS_REQUEST_TIMEOUT_MS + 10),
      );
      expect(pendingSignal.aborted).toBe(true);
      expect(
        screen.getByRole("button", { name: "Continue search" }),
      ).toBeEnabled();
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
      expect(
        screen.queryByPlaceholderText("Loading columns..."),
      ).not.toBeInTheDocument();
      expect(screen.queryByText("No matching rows")).not.toBeInTheDocument();
      await act(async () =>
        screen.getByRole("button", { name: "Continue search" }).click(),
      );
      await act(async () => vi.advanceTimersByTimeAsync(10));
      expect(listCalls(rowType)).toHaveLength(3);
      const resumed = listCalls(rowType)[2][1].params;
      expect(resumed.cursor).toBe("unconsumed-matrix-cursor");
      expect(resumed).not.toHaveProperty("page_number");
      expect(JSON.parse(resumed.filters)).toEqual(FILTERS);
      for (const [, config] of listCalls(rowType)) {
        expect(JSON.parse(config.params.filters)).toEqual(FILTERS);
        assertCompanyStrings(JSON.parse(config.params.filters));
      }
      expect(
        screen.queryByRole("button", { name: "Continue search" }),
      ).not.toBeInTheDocument();
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    },
  );

  it("keeps the same 10 typed filters through shared list and graph serialization", () => {
    const uiFilters = FILTERS.map((filter, index) => ({
      ...filter,
      id: `ui-${index}`,
      _meta: { label: "UI only" },
    }));
    expect(serializeFilterListForApi(uiFilters)).toEqual(FILTERS);
    expect(toBackendFilters(uiFilters)).toEqual(FILTERS);
    assertCompanyStrings(serializeFilterListForApi(uiFilters));
    assertCompanyStrings(toBackendFilters(uiFilters));
    expect(
      toBackendFilters(combineGraphFilters({ filters: uiFilters })),
    ).toEqual(FILTERS);
    assertCompanyStrings(
      toBackendFilters(combineGraphFilters({ filters: uiFilters })),
    );
    expect(FILTERS[3].filter_config).not.toHaveProperty(
      "attribute_value_types",
    );
    expect(
      FILTERS[7].filter_config.filter_value.map((value) => typeof value),
    ).toEqual(["string", "number", "boolean"]);
  });

  it.each([1, 5, 10])(
    "round-trips %i structured filters through URL codecs without numeric string coercion",
    (count) => {
      const filters = FILTERS.slice(0, count);
      for (const field of [
        "primaryTraceFilter",
        "primarySpanFilter",
        "sessionFilter",
        "userFilter",
      ]) {
        // Pure URL codec coverage, not navigation or a token-editor interaction.
        const query = new URLSearchParams({
          [field]: stringifyUrlValue(filters),
        });
        const decoded = parseUrlValue(
          new URLSearchParams(query.toString()).get(field),
          [],
        );
        expect(decoded).toEqual(filters);
        assertCompanyStrings(decoded);
      }
    },
  );
});
