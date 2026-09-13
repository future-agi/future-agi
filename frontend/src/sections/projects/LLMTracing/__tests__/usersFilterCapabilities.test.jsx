import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { buildApiFilterFromPanelRow } from "src/api/contracts/filter-contract";
import axios from "src/utils/axios";
import { buildTraceFilterProperties } from "../TraceFilterPanel";
import PrimaryGraph from "../GraphSection/PrimaryGraph";

// Synthetic definitions/responses only. The real picker and wire-contract
// code execute; every catalog and HTTP request is replaced locally.
const { catalogMock } = vi.hoisted(() => ({ catalogMock: vi.fn() }));

vi.mock("src/hooks/useDashboards", () => ({
  PROPERTY_CATALOG_REQUEST_TIMEOUT_MS: 9_000,
  isPropertyCatalogNotReadyError: () => false,
  usePropertyCatalog: (options) => catalogMock(options),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    dashboard: { metrics: "/dashboard/metrics/" },
    project: { getTraceGraphData: () => "/tracer/trace/get_graph_methods/" },
  },
}));
vi.mock("react-apexcharts", () => ({ default: () => null }));
vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));
vi.mock("../common", () => ({
  toBackendFilters: (filters) => filters.map(({ id: _id, ...leaf }) => leaf),
}));

const UNSUPPORTED = [
  "dataset",
  "eval_source",
  "active_users",
  "avg_cost_per_user",
  "avg_traces_per_user",
];
const POPULATION = [
  ["active_users", "Active Users"],
  ["avg_cost_per_user", "Average Cost Per User"],
  ["avg_traces_per_user", "Average Traces Per User"],
];
const systemMetric = (name, label, source = "users") => ({
  name,
  display_name: label,
  category: "system_metric",
  source,
  sources: [source],
  type: "number",
  property_id: `system_attribute:${source}:${name}`,
});
const graphMetrics = [
  ...POPULATION.map(([name, label]) => systemMetric(name, label)),
  systemMetric("cost", "Foreign Session Cost", "sessions"),
  systemMetric("latency", "Foreign Trace Latency", "traces"),
];

describe("Users system-filter capability protection", () => {
  it.each(UNSUPPORTED)(
    "excludes %s system definitions from generic/source-scoped filter pickers",
    (name) => {
      for (const sourceScope of [null, "users"]) {
        for (const category of ["system_metric", "systemMetric"]) {
          const definition = systemMetric(name, name);
          if (["dataset", "eval_source"].includes(name)) {
            definition.type = "string";
            definition.source = "all";
            definition.sources = ["all"];
          }
          expect(
            buildTraceFilterProperties([{ ...definition, category }], {
              sourceScope,
            }),
          ).toEqual([]);
        }
      }
    },
  );

  it.each([...UNSUPPORTED, "span_count", "project"])(
    "preserves raw %s and its numeric-looking string value",
    (name) => {
      for (const category of ["custom_attribute", "customAttribute"]) {
        const [property] = buildTraceFilterProperties([
          {
            name,
            category,
            type: "string",
            source: "traces",
            property_id: `custom_attribute:${name}`,
          },
        ]);
        expect(property).toMatchObject({
          id: name,
          apiColType: "SPAN_ATTRIBUTE",
          registryId: `custom_attribute:${name}`,
        });
        const leaf = buildApiFilterFromPanelRow({
          field: property.id,
          registryId: property.registryId,
          apiColType: property.apiColType,
          fieldType: property.type,
          operator: "in",
          value: ["10000001", "fixture-only"],
          valueTypes: ["string", "string"],
        });
        expect(leaf).toEqual({
          column_id: name,
          property_id: `custom_attribute:${name}`,
          filter_config: {
            col_type: "SPAN_ATTRIBUTE",
            filter_type: "text",
            filter_op: "in",
            filter_value: ["10000001", "fixture-only"],
            attribute_value_types: ["string", "string"],
          },
        });
      }
    },
  );

  it("retains legitimate per-user numeric filters", () => {
    expect(
      buildTraceFilterProperties(
        ["total_cost", "num_traces", "total_tokens"].map((name) =>
          systemMetric(name, name),
        ),
        { sourceScope: "users" },
      ).map((property) => property.id),
    ).toEqual(["total_cost", "num_traces", "total_tokens"]);
  });
});

describe.each(["catalog", "legacy"])("Users graph %s metrics", (path) => {
  beforeEach(() => {
    vi.clearAllMocks();
    let catalogLoaded = false;
    catalogMock.mockImplementation((options) => {
      catalogLoaded ||= options.enabled;
      return {
        metrics:
          path === "catalog" &&
          catalogLoaded &&
          options.category === "system_metric"
            ? options.source === "users"
              ? graphMetrics
              : graphMetrics.filter((metric) => metric.source === "sessions")
            : [],
        legacyFallbackRequired: path === "legacy",
        isLoading: false,
        isError: false,
        hasNextPage: false,
      };
    });
    axios.get.mockResolvedValue({
      data: { result: { metrics: graphMetrics } },
    });
    axios.post.mockImplementation(async (_url, body) => ({
      data: {
        result: {
          metric_name: body.req_data_config.id,
          data: [],
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
          query_completed_at: "2026-09-04T12:00:00Z",
        },
      },
    }));
  });

  it.each(POPULATION)(
    "selects %s via users catalog but keeps sessions wire transport",
    async (name, label) => {
      const client = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      });
      const graphEndpoint = "/tracer/project/get_users_aggregate_graph_data/";
      const { unmount } = render(
        <QueryClientProvider client={client}>
          <PrimaryGraph
            observeIdOverride="fixture-project"
            graphEndpoint={graphEndpoint}
            trafficLabel="users"
          />
        </QueryClientProvider>,
      );

      fireEvent.click(await screen.findByRole("button", { name: "Latency" }));
      // Catalog arrival resolves the default metric and issues its own graph
      // read. Wait for that render before clicking (not a detached loading DOM).
      await waitFor(() => {
        expect(client.isFetching()).toBe(0);
        expect(
          screen.getByTestId("graph-metric-picker-trigger"),
        ).toHaveTextContent("Active Users");
        expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
      });
      fireEvent.click(screen.getByRole("button", { name: label }));
      await waitFor(() => {
        expect(axios.post.mock.calls.at(-1)?.[1].req_data_config).toEqual({
          id: name,
          type: "SYSTEM_METRIC",
          property_id: `system_attribute:users:${name}`,
          source: "sessions",
        });
      });
      expect(axios.post.mock.calls.at(-1)[0]).toBe(graphEndpoint);

      const enabled = catalogMock.mock.calls
        .map(([options]) => options)
        .filter((options) => options.enabled);
      for (const [category, source] of [
        ["system_metric", "users"],
        ["eval_metric", "sessions"],
        ["annotation_metric", "sessions"],
      ]) {
        expect(enabled).toEqual(
          expect.arrayContaining([
            expect.objectContaining({ category, source, role: "metric" }),
          ]),
        );
      }
      // Reopen so exclusion assertions inspect the actual menu, not a closed one.
      fireEvent.click(await screen.findByRole("button", { name: label }));
      expect(
        screen.queryByText("Foreign Session Cost"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText("Foreign Trace Latency"),
      ).not.toBeInTheDocument();
      for (const [, metricLabel] of POPULATION) {
        expect(screen.getAllByText(metricLabel).length).toBeGreaterThan(0);
      }
      if (path === "catalog") expect(axios.get).not.toHaveBeenCalled();
      else expect(axios.get).toHaveBeenCalled();
      unmount();
      client.clear();
    },
  );
});
