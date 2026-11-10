import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import axios, { readQuery } from "src/utils/axios";
import PrimaryGraph from "../PrimaryGraph";

// The real usePropertyCatalog runs here: the picker reads one current catalog
// page per category (system, eval, annotation), and those three responses can
// arrive in any order. The sibling PrimaryGraph test mocks the hook onto the
// single-request legacy reader, which cannot express that order.

vi.mock("react-apexcharts", () => ({ default: () => null }));

vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));

vi.mock("../../common", () => ({
  toBackendFilters: (filters) =>
    filters.map(({ id: _id, ...filter }) => filter),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  readQuery: vi.fn(),
  endpoints: {
    dashboard: { metrics: "/tracer/dashboard/metrics/" },
    project: {
      getTraceGraphData: () => "/tracer/trace/get_graph_methods/",
      getSpanGraphData: () => "/tracer/observation-span/get_graph_methods/",
    },
  },
}));

const TRACE_GRAPH = "/tracer/trace/get_graph_methods/";
const SPAN_GRAPH = "/tracer/observation-span/get_graph_methods/";
const SESSION_GRAPH = "/tracer/trace-session/get_session_graph_data/";
const USERS_GRAPH = "/tracer/project/get_users_aggregate_graph_data/";

const systemMetric = (source, name, displayName) => ({
  name,
  property_id: `system_attribute:${source}:${name}`,
  property_kind: "system_attribute",
  display_name: displayName,
  category: "system_metric",
  source,
  sources: ["system", source, name],
  type: "number",
  output_type: "number",
  role: "metric",
});

// Shapes follow the current catalog manifest: the sessions and users
// namespaces have no "latency" entry, although their graph endpoints default
// to it.
const SYSTEM_METRICS = {
  traces: [
    systemMetric("traces", "cost", "Cost"),
    systemMetric("traces", "latency", "Latency"),
  ],
  sessions: [
    systemMetric("sessions", "duration", "Duration"),
    systemMetric("sessions", "total_cost", "Total Cost"),
  ],
  users: [
    systemMetric("users", "active_users", "Active Users"),
    systemMetric("users", "avg_trace_latency", "Average Trace Latency"),
  ],
};

// Eval configs are project-scoped, so each project gets its own identity.
const evalMetrics = (projectId) => [
  {
    name: `tox-${projectId}`,
    property_id: `eval_config:tox-${projectId}`,
    property_kind: "eval_config",
    display_name: "Toxicity",
    category: "eval_metric",
    source: "all",
    sources: ["eval", "all"],
    type: "number",
    output_type: "SCORE",
    role: "metric",
  },
];

const ANNOTATION_METRICS = [
  {
    name: "label-1",
    property_id: "annotation:label-1",
    property_kind: "annotation",
    display_name: "Helpfulness",
    category: "annotation_metric",
    source: "traces",
    sources: ["annotation", "traces"],
    type: "number",
    output_type: "number",
    role: "metric",
  },
];

const SURFACES = {
  trace: {
    props: { graphEndpoint: TRACE_GRAPH, trafficLabel: "traces" },
    systemSource: "traces",
    latencyPropertyId: "system_attribute:traces:latency",
    transportSource: "traces",
  },
  span: {
    props: { graphEndpoint: SPAN_GRAPH, trafficLabel: "spans" },
    systemSource: "traces",
    latencyPropertyId: "system_attribute:spans:latency",
    transportSource: "traces",
  },
  session: {
    props: {
      graphEndpoint: SESSION_GRAPH,
      trafficLabel: "sessions",
      defaultMetric: "latency",
    },
    systemSource: "sessions",
    latencyPropertyId: "system_attribute:sessions:latency",
    transportSource: "sessions",
  },
  users: {
    props: {
      graphEndpoint: USERS_GRAPH,
      trafficLabel: "users",
      defaultMetric: "latency",
    },
    systemSource: "users",
    latencyPropertyId: "system_attribute:users:latency",
    transportSource: "sessions",
  },
};

const catalogPage = (metrics) => ({
  data: {
    result: {
      metrics,
      total: null,
      total_is_exact: false,
      page_size: 50,
      has_more: false,
      next_cursor: null,
      query_complete: true,
      query_exact: false,
      query_status: "complete",
      query_provenance: "current_property_catalog",
    },
  },
});

const catalogMetrics = (category, surface, projectId = "project-1") =>
  ({
    system_metric: SYSTEM_METRICS[surface.systemSource],
    eval_metric: evalMetrics(projectId),
    annotation_metric: ANNOTATION_METRICS,
  })[category];

const firstLabel = (category, surface) =>
  catalogMetrics(category, surface)[0].display_name;

// Each catalog read stays pending until the test releases it, so the test
// owns the arrival order.
let pendingCatalogReads;

const releaseCatalog = async (category, surface) => {
  const read = pendingCatalogReads.get(category);
  if (!read) throw new Error(`No pending ${category} catalog read`);
  pendingCatalogReads.delete(category);
  await act(async () => {
    read.resolve(
      catalogPage(catalogMetrics(category, surface, read.params.project_ids)),
    );
  });
};

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

const renderGraph = (props, observeId = "project-1") => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const graph = (id) => (
    <QueryClientProvider client={queryClient}>
      <PrimaryGraph observeIdOverride={id} {...props} />
    </QueryClientProvider>
  );
  const view = render(graph(observeId));
  return { ...view, rerenderProject: (id) => view.rerender(graph(id)) };
};

const trigger = () => screen.getByTestId("graph-metric-picker-trigger");
const picker = () => screen.getByPlaceholderText("Search metrics...");

const openPicker = async () => {
  fireEvent.click(trigger());
  await screen.findByPlaceholderText("Search metrics...");
};

const closePickerWithEscape = async () => {
  fireEvent.keyDown(picker(), { key: "Escape", code: "Escape" });
  await waitFor(() =>
    expect(
      screen.queryByPlaceholderText("Search metrics..."),
    ).not.toBeInTheDocument(),
  );
};

const pickerList = () => within(screen.getByRole("presentation"));

const graphRequests = () =>
  axios.post.mock.calls.map(([url, body]) => ({
    url,
    ...body.req_data_config,
  }));

// The chart shows a skeleton (without the picker trigger) until its first
// graph response lands.
const waitForGraphRequests = async (count) => {
  await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(count));
  await screen.findByTestId("graph-metric-picker-trigger");
};

const expectLatencyOnly = (surface) => {
  expect(graphRequests()).toEqual([
    expect.objectContaining({
      url: surface.props.graphEndpoint,
      id: "latency",
      type: "SYSTEM_METRIC",
      property_id: surface.latencyPropertyId,
      source: surface.transportSource,
    }),
  ]);
  expect(trigger()).toHaveTextContent("Latency");
};

describe("PrimaryGraph metric picker catalog arrival order", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    pendingCatalogReads = new Map();
    readQuery.mockImplementation(
      (_url, { params }) =>
        new Promise((resolve) => {
          pendingCatalogReads.set(params.category, { params, resolve });
        }),
    );
    axios.post.mockResolvedValue({
      data: {
        result: {
          metric_name: "latency",
          data: [],
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
          query_completed_at: "2026-09-25T12:00:00Z",
        },
      },
    });
  });

  // Orders observed on dev (env-v3-deploy 56aede472): Trace eval before
  // system, Span annotation and eval before system, Session and Users system
  // first. The Trace system-first run is the control that already held.
  it.each([
    ["trace", ["eval_metric", "annotation_metric", "system_metric"]],
    ["trace", ["system_metric", "annotation_metric", "eval_metric"]],
    ["span", ["annotation_metric", "eval_metric", "system_metric"]],
    ["session", ["system_metric", "eval_metric", "annotation_metric"]],
    ["session", ["eval_metric", "annotation_metric", "system_metric"]],
    ["users", ["system_metric", "eval_metric", "annotation_metric"]],
    ["users", ["annotation_metric", "eval_metric", "system_metric"]],
  ])(
    "%s: opening and dismissing the picker keeps Latency when catalogs arrive %j",
    async (surfaceName, arrivalOrder) => {
      const surface = SURFACES[surfaceName];
      renderGraph(surface.props);
      await waitForGraphRequests(1);
      expectLatencyOnly(surface);

      await openPicker();
      await waitFor(() => expect(pendingCatalogReads.size).toBe(3));
      expect(pendingCatalogReads.get("system_metric").params.source).toBe(
        surface.systemSource,
      );

      // Deliver all but the last category while the picker is open.
      for (const category of arrivalOrder.slice(0, -1)) {
        await releaseCatalog(category, surface);
        await pickerList().findByText(firstLabel(category, surface));
        await settle();
        expectLatencyOnly(surface);
      }

      // Dismiss without choosing, then let the last category land.
      await closePickerWithEscape();
      await releaseCatalog(arrivalOrder.at(-1), surface);
      await settle();
      expectLatencyOnly(surface);

      // Reopening over the now-complete catalog changes nothing either.
      await openPicker();
      for (const category of arrivalOrder) {
        await pickerList().findByText(firstLabel(category, surface));
      }
      await closePickerWithEscape();
      await settle();
      expectLatencyOnly(surface);
    },
  );

  it("still graphs a metric the user explicitly picks, and keeps it on reopen", async () => {
    const surface = SURFACES.trace;
    renderGraph(surface.props);
    await waitForGraphRequests(1);

    await openPicker();
    await waitFor(() => expect(pendingCatalogReads.size).toBe(3));
    await releaseCatalog("system_metric", surface);
    await releaseCatalog("eval_metric", surface);
    await releaseCatalog("annotation_metric", surface);
    fireEvent.click(await pickerList().findByText("Toxicity"));

    await waitForGraphRequests(2);
    expect(graphRequests().at(-1)).toEqual(
      expect.objectContaining({
        id: "tox-project-1",
        type: "EVAL",
        property_id: "eval_config:tox-project-1",
      }),
    );
    expect(trigger()).toHaveTextContent("Toxicity");

    await openPicker();
    await pickerList().findByText("Cost");
    await closePickerWithEscape();
    await settle();
    expect(trigger()).toHaveTextContent("Toxicity");
    expect(axios.post).toHaveBeenCalledTimes(2);
  });

  // Toxicity is a project-1 eval config; Cost is a system metric every
  // project's catalog lists, so only an explicit reset keeps project-2's
  // picker from reviving it when that catalog arrives.
  it.each([
    ["Toxicity", ["eval_metric", "annotation_metric", "system_metric"]],
    ["Cost", ["system_metric", "eval_metric", "annotation_metric"]],
  ])(
    "returns to Latency after %s was picked and the project changes",
    async (pickedLabel, projectTwoArrivalOrder) => {
      const surface = SURFACES.trace;
      const { rerenderProject } = renderGraph(surface.props, "project-1");
      await waitForGraphRequests(1);

      await openPicker();
      await waitFor(() => expect(pendingCatalogReads.size).toBe(3));
      await releaseCatalog("system_metric", surface);
      await releaseCatalog("eval_metric", surface);
      await releaseCatalog("annotation_metric", surface);
      fireEvent.click(await pickerList().findByText(pickedLabel));
      await waitForGraphRequests(2);
      expect(trigger()).toHaveTextContent(pickedLabel);

      rerenderProject("project-2");
      await waitForGraphRequests(3);
      await settle();
      expect(axios.post).toHaveBeenCalledTimes(3);
      expect(axios.post.mock.calls.at(-1)[1]).toEqual(
        expect.objectContaining({
          project_id: "project-2",
          req_data_config: expect.objectContaining({
            id: "latency",
            type: "SYSTEM_METRIC",
            property_id: "system_attribute:traces:latency",
          }),
        }),
      );
      expect(trigger()).toHaveTextContent("Latency");

      // Opening project-2's picker changes nothing, whichever category
      // arrives first.
      await openPicker();
      await waitFor(() => expect(pendingCatalogReads.size).toBe(3));
      const [firstCategory, ...laterCategories] = projectTwoArrivalOrder;
      await releaseCatalog(firstCategory, surface);
      await pickerList().findByText(firstLabel(firstCategory, surface));
      await settle();
      expect(axios.post).toHaveBeenCalledTimes(3);
      expect(trigger()).toHaveTextContent("Latency");
      await closePickerWithEscape();
      for (const category of laterCategories) {
        await releaseCatalog(category, surface);
      }
      await settle();
      expect(axios.post).toHaveBeenCalledTimes(3);
      expect(trigger()).toHaveTextContent("Latency");
    },
  );
});
