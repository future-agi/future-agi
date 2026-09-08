import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import {
  AGGREGATION_POLL_MAX_ATTEMPTS,
  AGGREGATION_POLLING_PAUSED_MESSAGE,
  AGGREGATION_REQUEST_TIMEOUT_MS,
  GRAPH_LOADING_MESSAGE,
  QUERY_FAILED_RETRY_MESSAGE,
} from "src/utils/queryReadState";
import PrimaryGraph from "../PrimaryGraph";

const { propertyCatalogOptionsSpy } = vi.hoisted(() => ({
  propertyCatalogOptionsSpy: vi.fn(),
}));

vi.mock("react-apexcharts", () => ({
  default: ({ series, options }) => (
    <div
      data-testid="apex-chart"
      data-traffic-series-name={series?.[1]?.name}
      data-traffic-axis-series-name={options?.yaxis?.[1]?.seriesName}
      data-primary-first-y={series?.[0]?.data?.[0]?.y}
    />
  ),
}));

vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));

vi.mock("src/hooks/useDashboards", () => ({
  PROPERTY_CATALOG_REQUEST_TIMEOUT_MS: 9_000,
  isPropertyCatalogNotReadyError: (error) =>
    error?.response?.status === 503 &&
    error?.response?.data?.code === "property_catalog_not_ready",
  usePropertyCatalog: (options) => {
    propertyCatalogOptionsSpy(options);
    return {
      error: {
        response: {
          status: 503,
          data: { code: "property_catalog_not_ready" },
        },
      },
      legacyFallbackRequired: true,
      metrics: [],
    };
  },
}));

vi.mock("../../common", () => ({
  toBackendFilters: (filters) =>
    filters.map(({ id: _id, ...filter }) => filter),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
  endpoints: {
    dashboard: {
      metrics: "/dashboard/metrics/",
    },
    project: {
      getTraceGraphData: () => "/tracer/trace/get_graph_methods/",
      getSpanGraphData: () => "/tracer/observation-span/get_graph_methods/",
    },
  },
}));

function renderWithQueryClient(ui) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

const installIntersectionObserver = () => {
  const observers = [];
  class IntersectionObserverMock {
    constructor(callback, options) {
      this.callback = callback;
      this.options = options;
      this.observe = vi.fn();
      this.disconnect = vi.fn();
      observers.push(this);
    }
  }
  vi.stubGlobal("IntersectionObserver", IntersectionObserverMock);
  const emit = (isIntersecting) => {
    const observer = observers.at(-1);
    if (!observer) throw new Error("No IntersectionObserver was created");
    act(() => observer.callback([{ isIntersecting }]));
  };
  return { observers, emit };
};

const advanceMetricPagination = (sentinel, intersection) => {
  intersection.emit(true);
  fireEvent.wheel(sentinel.parentElement, { deltaY: 1 });
};

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

describe("PrimaryGraph", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    axios.get.mockResolvedValue({
      data: {
        result: {
          metrics: [
            {
              category: "system_metric",
              name: "latency",
              displayName: "Latency",
              type: "number",
            },
          ],
        },
      },
    });
    axios.post.mockResolvedValue({
      data: {
        result: {
          metric_name: "latency",
          data: [],
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
          query_completed_at: "2026-08-03T02:00:00Z",
        },
      },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("uses observeIdOverride as the graph project id", async () => {
    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );

    await waitFor(() => expect(axios.post).toHaveBeenCalled());
    expect(axios.get).not.toHaveBeenCalled();

    fireEvent.click(await screen.findByText("Latency"));
    await waitFor(() => expect(axios.get).toHaveBeenCalled());

    expect(axios.get).toHaveBeenCalledWith("/dashboard/metrics/", {
      params: {
        exclude_custom_attributes: true,
        page: 1,
        page_size: 200,
        project_ids: "project-override",
        per_eval_config: true,
      },
      signal: expect.anything(),
      timeout: 9_000,
    });

    expect(axios.post).toHaveBeenCalledWith(
      "/tracer/trace/get_graph_methods/",
      expect.objectContaining({
        project_id: "project-override",
        req_data_config: expect.objectContaining({
          id: "latency",
          type: "SYSTEM_METRIC",
          property_id: "system_attribute:traces:latency",
          source: "traces",
        }),
      }),
      expect.objectContaining({ params: undefined }),
    );
  });

  it("scopes the primary metric picker to renderable metric categories", async () => {
    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );

    fireEvent.click(await screen.findByText("Latency"));
    await waitFor(() => {
      const enabledCategories = new Set(
        propertyCatalogOptionsSpy.mock.calls
          .map(([options]) => options)
          .filter((options) => options.enabled)
          .map((options) => options.category),
      );
      expect(enabledCategories).toEqual(
        new Set(["annotation_metric", "eval_metric", "system_metric"]),
      );
    });

    const enabledOptions = [
      ...new Map(
        propertyCatalogOptionsSpy.mock.calls
          .map(([options]) => options)
          .filter((options) => options.enabled)
          .map((options) => [options.category, options]),
      ).values(),
    ];
    expect(enabledOptions.map((options) => options.category).sort()).toEqual([
      "annotation_metric",
      "eval_metric",
      "system_metric",
    ]);
    for (const options of enabledOptions) {
      expect(options).toEqual(
        expect.objectContaining({
          projectIds: ["project-override"],
          role: "metric",
          source: "traces",
        }),
      );
    }
  });

  it("binds session aggregate graphs to the session property source", async () => {
    renderWithQueryClient(
      <PrimaryGraph
        observeIdOverride="project-override"
        graphEndpoint="/tracer/trace-session/get_session_graph_data/"
        trafficLabel="sessions"
      />,
    );

    await waitFor(() => expect(axios.post).toHaveBeenCalled());
    expect(axios.post.mock.calls.at(-1)[1].req_data_config).toEqual(
      expect.objectContaining({
        id: "latency",
        property_id: "system_attribute:sessions:latency",
        source: "sessions",
      }),
    );
  });

  it("preserves the users namespace while using the session transport", async () => {
    renderWithQueryClient(
      <PrimaryGraph
        observeIdOverride="project-override"
        graphEndpoint="/tracer/project/get_users_aggregate_graph_data/"
        trafficLabel="users"
      />,
    );

    await waitFor(() => expect(axios.post).toHaveBeenCalled());
    expect(axios.post.mock.calls.at(-1)[1].req_data_config).toEqual(
      expect.objectContaining({
        id: "latency",
        property_id: "system_attribute:users:latency",
        source: "sessions",
      }),
    );
  });

  it("uses the supplied graph endpoint for span graphs", async () => {
    renderWithQueryClient(
      <PrimaryGraph
        observeIdOverride="project-override"
        graphEndpoint="/tracer/observation-span/get_graph_methods/"
        trafficLabel="spans"
      />,
    );

    await waitFor(() => expect(axios.post).toHaveBeenCalled());

    expect(axios.post).toHaveBeenCalledWith(
      "/tracer/observation-span/get_graph_methods/",
      expect.objectContaining({
        project_id: "project-override",
        req_data_config: expect.objectContaining({
          property_id: "system_attribute:spans:latency",
          source: "traces",
        }),
      }),
      expect.objectContaining({ params: undefined }),
    );
  });

  it("offers project eval configs and excludes simulation-only system metrics", async () => {
    axios.get.mockResolvedValue({
      data: {
        result: {
          metrics: [
            {
              category: "system_metric",
              name: "latency",
              display_name: "Latency",
              source: "traces",
              property_id: "system_attribute:traces:latency",
              type: "number",
            },
            {
              category: "system_metric",
              name: "call_count",
              display_name: "Call Count",
              source: "simulation",
              property_id: "system_attribute:simulation:call_count",
              type: "number",
            },
            {
              category: "eval_metric",
              name: "config-1",
              display_name: "Quality eval",
              source: "traces",
              property_id: "eval_config:config-1",
              output_type: "SCORE",
            },
          ],
        },
      },
    });

    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );
    await waitFor(() => expect(axios.post).toHaveBeenCalled());

    fireEvent.click(await screen.findByText("Latency"));
    expect(await screen.findByText("Quality eval")).toBeVisible();
    expect(screen.queryByText("Call Count")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Quality eval"));
    await waitFor(() =>
      expect(axios.post.mock.calls.at(-1)[1].req_data_config).toEqual(
        expect.objectContaining({
          id: "config-1",
          type: "EVAL",
          property_id: "eval_config:config-1",
          source: "traces",
        }),
      ),
    );
  });

  it("loads legacy metric pages after bounded scroll gestures when responses omit page", async () => {
    const intersection = installIntersectionObserver();
    const secondPage = deferred();
    const thirdPage = deferred();
    const response = (result) => ({ data: { result } });
    axios.get.mockImplementation((_url, { params }) => {
      if (params.page === 2) return secondPage.promise;
      if (params.page === 3) return thirdPage.promise;
      return Promise.resolve(
        response({
          metrics: [
            {
              category: "system_metric",
              name: "latency",
              display_name: "Latency",
              source: "traces",
              property_id: "system_attribute:traces:latency",
              type: "number",
            },
          ],
          page_size: 200,
          total: 202,
          has_more: true,
        }),
      );
    });

    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );
    fireEvent.click(await screen.findByText("Latency"));

    const sentinel = await screen.findByTestId(
      "primary-graph-metric-pagination-sentinel",
    );
    expect(intersection.observers[0].options.root).toBe(sentinel.parentElement);
    expect(
      screen.queryByRole("button", { name: /load more|continue/i }),
    ).not.toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledTimes(1);

    advanceMetricPagination(sentinel, intersection);
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
    expect(axios.get).toHaveBeenLastCalledWith("/dashboard/metrics/", {
      params: expect.objectContaining({ page: 2, page_size: 200 }),
      signal: expect.anything(),
      timeout: 9_000,
    });

    await act(async () => {
      secondPage.resolve(
        response({
          metrics: [
            {
              category: "annotation_metric",
              name: "annotation-1",
              display_name: "QA Annotation",
              source: "both",
              property_id: "annotation:annotation-1",
              output_type: "numeric",
            },
          ],
          page_size: 200,
          total: 202,
          has_more: true,
        }),
      );
      await secondPage.promise;
    });

    expect(await screen.findByText("QA Annotation")).toBeVisible();
    // Each new continuation requires another user advance gesture, even while
    // the sentinel remains visible after a short page.
    fireEvent.wheel(sentinel.parentElement, { deltaY: 1 });
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(3));
    expect(axios.get).toHaveBeenLastCalledWith("/dashboard/metrics/", {
      params: expect.objectContaining({ page: 3, page_size: 200 }),
      signal: expect.anything(),
      timeout: 9_000,
    });

    await act(async () => {
      thirdPage.resolve(
        response({
          metrics: [
            {
              category: "eval_metric",
              name: "eval-1",
              display_name: "Quality Eval",
              source: "traces",
              property_id: "eval_config:eval-1",
              output_type: "SCORE",
            },
          ],
          page_size: 200,
          total: 202,
          has_more: false,
        }),
      );
      await thirdPage.promise;
    });

    expect(await screen.findByText("Quality Eval")).toBeVisible();
    expect(axios.get).toHaveBeenCalledTimes(3);
    expect(
      screen.queryByRole("button", { name: /load more|continue/i }),
    ).not.toBeInTheDocument();
  });

  it("restarts the same metric continuation when the project changes", async () => {
    const intersection = installIntersectionObserver();
    const response = (result) => ({ data: { result } });
    axios.get.mockImplementation((_url, { params }) =>
      Promise.resolve(
        response({
          metrics:
            params.page === 1
              ? [
                  {
                    category: "system_metric",
                    name: "latency",
                    display_name: "Latency",
                    source: "traces",
                    property_id: `system_attribute:traces:latency:${params.project_ids}`,
                    type: "number",
                  },
                ]
              : [],
          page_size: 200,
          total: 201,
          has_more: params.page === 1,
        }),
      ),
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const graph = (projectId) => (
      <QueryClientProvider client={queryClient}>
        <PrimaryGraph observeIdOverride={projectId} />
      </QueryClientProvider>
    );
    const { rerender } = render(graph("project-one"));

    fireEvent.click(await screen.findByText("Latency"));
    let sentinel = await screen.findByTestId(
      "primary-graph-metric-pagination-sentinel",
    );
    advanceMetricPagination(sentinel, intersection);
    await waitFor(() =>
      expect(
        axios.get.mock.calls.some(
          ([, { params }]) =>
            params.project_ids === "project-one" && params.page === 2,
        ),
      ).toBe(true),
    );

    rerender(graph("project-two"));
    sentinel = await screen.findByTestId(
      "primary-graph-metric-pagination-sentinel",
    );
    advanceMetricPagination(sentinel, intersection);
    await waitFor(() =>
      expect(
        axios.get.mock.calls.some(
          ([, { params }]) =>
            params.project_ids === "project-two" && params.page === 2,
        ),
      ).toBe(true),
    );
  });

  it("requires an explicit retry after a gesture-triggered metric page fails", async () => {
    const intersection = installIntersectionObserver();
    let pageTwoAttempts = 0;
    const response = (result) => ({ data: { result } });
    axios.get.mockImplementation((_url, { params }) => {
      if (params.page === 2) {
        pageTwoAttempts += 1;
        if (pageTwoAttempts === 1) {
          return Promise.reject(new Error("next page failed"));
        }
        return Promise.resolve(
          response({
            metrics: [
              {
                category: "annotation_metric",
                name: "annotation-recovered",
                display_name: "Recovered Annotation",
                source: "both",
                property_id: "annotation:annotation-recovered",
                output_type: "numeric",
              },
            ],
            page: 2,
            page_size: 200,
            total: 201,
            has_more: false,
          }),
        );
      }
      return Promise.resolve(
        response({
          metrics: [
            {
              category: "system_metric",
              name: "latency",
              display_name: "Latency",
              source: "traces",
              property_id: "system_attribute:traces:latency",
              type: "number",
            },
          ],
          page: 1,
          page_size: 200,
          total: 201,
          has_more: true,
        }),
      );
    });

    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );
    fireEvent.click(await screen.findByText("Latency"));
    const sentinel = await screen.findByTestId(
      "primary-graph-metric-pagination-sentinel",
    );

    advanceMetricPagination(sentinel, intersection);
    const retry = await screen.findByRole("button", {
      name: "Retry loading more metrics",
    });
    expect(axios.get).toHaveBeenCalledTimes(2);
    expect(
      screen.queryByRole("button", { name: /load more|continue/i }),
    ).not.toBeInTheDocument();

    intersection.emit(true);
    await act(async () => undefined);
    expect(axios.get).toHaveBeenCalledTimes(2);

    fireEvent.click(retry);
    expect(await screen.findByText("Recovered Annotation")).toBeVisible();
    expect(axios.get).toHaveBeenCalledTimes(3);
  });

  const statusFilter = {
    column_id: "status",
    filter_config: {
      col_type: "NORMAL",
      filter_type: "text",
      filter_op: "equals",
      filter_value: "SUCCESS",
    },
  };

  const metricFilter = {
    id: "fe-react-key",
    column_id: "latency",
    filter_config: {
      col_type: "SYSTEM_METRIC",
      filter_type: "number",
      filter_op: "greater_than",
      filter_value: 2,
    },
  };

  const postedFilters = () => axios.post.mock.calls.at(-1)[1].filters;

  it("keeps non-date filters when extraFilters is omitted (users/sessions)", async () => {
    // Regression guard for the round-1 review bug: UsersView and
    // SessionsView render PrimaryGraph WITHOUT extraFilters, and their
    // graph must receive the same chip filters as their table.
    renderWithQueryClient(
      <PrimaryGraph
        observeIdOverride="project-override"
        filters={[statusFilter]}
      />,
    );

    await waitFor(() => expect(axios.post).toHaveBeenCalled());

    expect(postedFilters()).toEqual([statusFilter]);
  });

  it("keeps validated grid filters when trace/span toolbar filters are empty", async () => {
    renderWithQueryClient(
      <PrimaryGraph
        observeIdOverride="project-override"
        filters={[statusFilter]}
        extraFilters={[]}
      />,
    );

    await waitFor(() => expect(axios.post).toHaveBeenCalled());

    expect(postedFilters()).toEqual([statusFilter]);
  });

  it("combines grid and toolbar filters and strips FE-only ids", async () => {
    renderWithQueryClient(
      <PrimaryGraph
        observeIdOverride="project-override"
        filters={[statusFilter]}
        extraFilters={[metricFilter]}
      />,
    );

    await waitFor(() => expect(axios.post).toHaveBeenCalled());

    const { id: _id, ...metricFilterWithoutId } = metricFilter;
    expect(postedFilters()).toEqual([statusFilter, metricFilterWithoutId]);
  });

  it("does not present a degraded graph read as an empty time range", async () => {
    axios.post.mockResolvedValue({
      data: {
        query_complete: false,
        query_status: "degraded",
        result: {
          metric_name: "latency",
          data: [
            {
              timestamp: "2026-08-03T00:00:00Z",
              value: 999,
              primary_traffic: 999,
            },
          ],
        },
      },
    });

    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );

    expect(
      await screen.findByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("No data available for this time range"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("apex-chart")).not.toBeInTheDocument();
  });

  it("does not render a sampled graph response as exact data", async () => {
    axios.post.mockResolvedValue({
      data: {
        result: {
          metric_name: "latency",
          data: [
            {
              timestamp: "2026-08-03T00:00:00Z",
              value: 12,
              primary_traffic: 1,
            },
          ],
          query_complete: false,
          query_status: "sampled",
          query_sampled: true,
          query_exact: false,
          query_provenance: "bounded_candidates",
          query_error_code: "sample_limit",
          query_sampling_strategy: "time_stratified_latest_state",
          query_sampling_strata: 8,
          query_sampling_strata_completed: 8,
        },
      },
    });

    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );

    expect(
      await screen.findByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("apex-chart")).not.toBeInTheDocument();
    expect(screen.queryByText(/sampled estimates/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText("No data available for this time range"),
    ).not.toBeInTheDocument();
  });

  it("shows a generic graph error without exposing backend exception text", async () => {
    axios.post.mockRejectedValue({
      result: "Code: 159 DB::Exception: Timeout exceeded Stack trace...",
    });

    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );

    expect(
      await screen.findByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("status", { name: GRAPH_LOADING_MESSAGE }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/DB::Exception/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Stack trace/i)).not.toBeInTheDocument();
  });

  it("keeps exact data visible when an explicit refresh is not exact", async () => {
    const exactCompletion = vi.fn();
    window.addEventListener("observe-aggregation-completed", exactCompletion, {
      once: true,
    });
    axios.post
      .mockResolvedValueOnce({
        data: {
          result: {
            metric_name: "latency",
            data: [
              {
                timestamp: "2026-08-03T00:00:00Z",
                value: 12,
                primary_traffic: 1,
              },
              {
                timestamp: "2026-08-03T01:00:00Z",
                value: 0,
                primary_traffic: 0,
              },
            ],
            query_complete: true,
            query_status: "complete",
            query_sampled: false,
            query_completed_at: "2026-08-03T02:00:00Z",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            metric_name: "latency",
            data: [
              {
                timestamp: "2026-08-03T00:00:00Z",
                value: 999,
                primary_traffic: 999,
              },
            ],
            query_complete: false,
            query_status: "sampled",
            query_error_code: "sample_limit",
            query_sampling_strategy: "time_stratified_latest_state",
            query_sampling_strata: 8,
            query_sampling_strata_completed: 8,
          },
        },
      });

    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );
    expect(await screen.findByTestId("apex-chart")).toBeInTheDocument();
    expect(exactCompletion).toHaveBeenCalledOnce();
    expect(exactCompletion.mock.calls[0][0].detail).toEqual({
      observeId: "project-override",
      queryCompletedAt: "2026-08-03T02:00:00.000Z",
    });

    act(() => window.dispatchEvent(new CustomEvent("observe-refresh")));

    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("apex-chart")).toBeInTheDocument();
    expect(axios.post).toHaveBeenNthCalledWith(
      2,
      "/tracer/trace/get_graph_methods/",
      expect.any(Object),
      expect.objectContaining({
        params: { refresh: true },
      }),
    );
    expect(screen.queryByText(/sampled estimates/i)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("status", { name: GRAPH_LOADING_MESSAGE }),
    ).not.toBeInTheDocument();
  });

  it("polls a cold pending graph without refresh and publishes only final completion", async () => {
    vi.useFakeTimers();
    const exactCompletion = vi.fn();
    window.addEventListener("observe-aggregation-completed", exactCompletion);
    axios.post
      .mockResolvedValueOnce({
        data: {
          result: {
            metric_name: "latency",
            data: [],
            query_complete: false,
            query_status: "pending",
            query_sampled: false,
            query_refreshing: true,
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            metric_name: "latency",
            data: [
              {
                timestamp: "2026-08-03T00:00:00Z",
                value: 12,
                primary_traffic: 1,
              },
            ],
            query_complete: true,
            query_status: "complete",
            query_sampled: false,
            query_refreshing: false,
            query_completed_at: "2026-08-03T03:00:00Z",
          },
        },
      });

    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );
    await act(async () => vi.advanceTimersByTimeAsync(10));

    expect(axios.post).toHaveBeenCalledOnce();
    expect(
      screen.getByRole("status", { name: GRAPH_LOADING_MESSAGE }),
    ).toBeInTheDocument();
    expect(exactCompletion).not.toHaveBeenCalled();

    await act(async () => vi.advanceTimersByTimeAsync(1000));
    await act(async () => vi.advanceTimersByTimeAsync(10));

    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(axios.post).toHaveBeenNthCalledWith(
      2,
      "/tracer/trace/get_graph_methods/",
      expect.any(Object),
      expect.objectContaining({ params: undefined }),
    );
    expect(screen.getByTestId("apex-chart")).toBeInTheDocument();
    expect(exactCompletion).toHaveBeenCalledOnce();
    window.removeEventListener(
      "observe-aggregation-completed",
      exactCompletion,
    );
  });

  it("terminalizes a failed exact refresh instead of polling forever", async () => {
    vi.useFakeTimers();
    axios.post.mockResolvedValue({
      data: {
        result: {
          metric_name: "latency",
          data: [],
          query_complete: false,
          query_status: "pending",
          query_sampled: false,
          query_refreshing: false,
          query_refresh_failed: true,
        },
      },
    });

    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );
    await act(async () => vi.advanceTimersByTimeAsync(10_000));

    expect(axios.post).toHaveBeenCalledOnce();
    expect(screen.getByText(QUERY_FAILED_RETRY_MESSAGE)).toBeVisible();
    expect(
      screen.queryByRole("status", { name: GRAPH_LOADING_MESSAGE }),
    ).not.toBeInTheDocument();
  });

  it("keeps observing a healthy 12M exact job beyond one request window", async () => {
    vi.useFakeTimers();
    const pendingResponse = {
      data: {
        result: {
          metric_name: "latency",
          data: [],
          query_complete: false,
          query_status: "pending",
          query_sampled: false,
          query_refreshing: true,
          query_refresh_failed: false,
        },
      },
    };
    axios.post
      .mockResolvedValueOnce(pendingResponse)
      .mockResolvedValueOnce(pendingResponse)
      .mockResolvedValueOnce(pendingResponse)
      .mockResolvedValueOnce({
        data: {
          result: {
            metric_name: "latency",
            data: [
              {
                timestamp: "2026-08-03T00:00:00Z",
                value: 42,
                primary_traffic: 2,
              },
            ],
            query_complete: true,
            query_status: "complete",
            query_sampled: false,
            query_refreshing: false,
          },
        },
      });

    renderWithQueryClient(
      <PrimaryGraph
        observeIdOverride="project-override"
        dateFilter={{ dateOption: "12M" }}
        filters={[
          {
            column_id: "duration",
            operator: "greater_than",
            value: 30,
          },
          {
            column_id: "annotator",
            operator: "is_not_null",
          },
        ]}
      />,
    );

    await act(async () => vi.advanceTimersByTimeAsync(15_000));

    expect(axios.post).toHaveBeenCalledTimes(4);
    expect(
      screen.queryByText(AGGREGATION_POLLING_PAUSED_MESSAGE),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("apex-chart")).toHaveAttribute(
      "data-primary-first-y",
      "42",
    );
  });

  it("stops a cold pending graph at the finite budget and resumes only after explicit refresh", async () => {
    vi.useFakeTimers();
    const pendingResponse = {
      data: {
        result: {
          metric_name: "latency",
          data: [],
          query_complete: false,
          query_status: "pending",
          query_sampled: false,
          query_refreshing: true,
          query_refresh_failed: false,
        },
      },
    };
    axios.post.mockResolvedValue(pendingResponse);

    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );
    await act(async () => vi.advanceTimersByTimeAsync(500_000));

    const boundedRequestCount = axios.post.mock.calls.length;
    expect(boundedRequestCount).toBeLessThanOrEqual(
      AGGREGATION_POLL_MAX_ATTEMPTS + 1,
    );
    expect(screen.getByText(AGGREGATION_POLLING_PAUSED_MESSAGE)).toBeVisible();
    expect(
      screen.queryByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("apex-chart")).not.toBeInTheDocument();

    await act(async () => vi.advanceTimersByTimeAsync(500_000));
    expect(axios.post).toHaveBeenCalledTimes(boundedRequestCount);

    axios.post.mockResolvedValueOnce({
      data: {
        result: {
          metric_name: "latency",
          data: [
            {
              timestamp: "2026-08-03T00:00:00Z",
              value: 42,
              primary_traffic: 4,
            },
          ],
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
          query_refreshing: false,
          query_refresh_failed: false,
        },
      },
    });
    act(() => window.dispatchEvent(new CustomEvent("observe-refresh")));
    await act(async () => vi.advanceTimersByTimeAsync(10));

    expect(axios.post).toHaveBeenCalledTimes(boundedRequestCount + 1);
    expect(screen.getByTestId("apex-chart")).toHaveAttribute(
      "data-primary-first-y",
      "42",
    );
    expect(
      screen.queryByRole("status", { name: GRAPH_LOADING_MESSAGE }),
    ).not.toBeInTheDocument();
  });

  it("keeps a confirmed pending job neutral during transient failures and stops after three consecutive failures", async () => {
    vi.useFakeTimers();
    const refreshStates = [];
    const recordRefreshState = (event) => {
      if (event.detail?.observeId === "project-override") {
        refreshStates.push(event.detail.refreshing);
      }
    };
    window.addEventListener(
      "observe-aggregation-refresh-state",
      recordRefreshState,
    );
    axios.post
      .mockResolvedValueOnce({
        data: {
          result: {
            metric_name: "latency",
            data: [],
            query_complete: false,
            query_status: "pending",
            query_sampled: false,
            query_refreshing: true,
          },
        },
      })
      .mockRejectedValue(new Error("transport failed"));

    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );
    await act(async () => vi.advanceTimersByTimeAsync(10));
    expect(
      screen.getByRole("status", { name: GRAPH_LOADING_MESSAGE }),
    ).toBeInTheDocument();
    expect(refreshStates.at(-1)).toBe(true);

    await act(async () => vi.advanceTimersByTimeAsync(1_000));
    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(
      screen.getByRole("status", { name: GRAPH_LOADING_MESSAGE }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).not.toBeInTheDocument();

    await act(async () => vi.advanceTimersByTimeAsync(2_000));
    expect(axios.post).toHaveBeenCalledTimes(3);
    expect(
      screen.getByRole("status", { name: GRAPH_LOADING_MESSAGE }),
    ).toBeInTheDocument();

    await act(async () => vi.advanceTimersByTimeAsync(4_000));
    expect(axios.post).toHaveBeenCalledTimes(4);
    expect(
      screen.getByText("We couldn't load this data. Please retry in a moment."),
    ).toBeInTheDocument();
    expect(refreshStates.at(-1)).toBe(false);

    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(axios.post).toHaveBeenCalledTimes(4);
    expect(refreshStates.at(-1)).toBe(false);
    window.removeEventListener(
      "observe-aggregation-refresh-state",
      recordRefreshState,
    );
  });

  it("bounds a never-resolving refresh, preserves exact data, and ignores its late response", async () => {
    vi.useFakeTimers();
    const exactResponse = {
      data: {
        result: {
          metric_name: "latency",
          data: [
            {
              timestamp: "2026-08-03T00:00:00Z",
              value: 12,
              primary_traffic: 1,
            },
          ],
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
        },
      },
    };
    let resolveLateRefresh;
    axios.post.mockResolvedValueOnce(exactResponse).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveLateRefresh = resolve;
        }),
    );

    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );
    await act(async () => vi.advanceTimersByTimeAsync(10));
    expect(screen.getByTestId("apex-chart")).toBeInTheDocument();
    expect(screen.getByTestId("apex-chart")).toHaveAttribute(
      "data-primary-first-y",
      "12",
    );

    act(() => window.dispatchEvent(new CustomEvent("observe-refresh")));
    await act(async () => vi.advanceTimersByTimeAsync(10));
    expect(axios.post).toHaveBeenCalledTimes(2);
    const refreshSignal = axios.post.mock.calls[1][2].signal;
    expect(refreshSignal.aborted).toBe(false);
    expect(screen.getByTestId("apex-chart")).toBeInTheDocument();

    await act(async () =>
      vi.advanceTimersByTimeAsync(AGGREGATION_REQUEST_TIMEOUT_MS),
    );
    expect(screen.getByTestId("apex-chart")).toBeInTheDocument();
    expect(
      screen.getByText("We couldn't load this data. Please retry in a moment."),
    ).toBeInTheDocument();
    const boundedRequestCount = axios.post.mock.calls.length;
    expect(boundedRequestCount).toBe(2);
    expect(refreshSignal.aborted).toBe(true);

    resolveLateRefresh({
      data: {
        result: {
          metric_name: "latency",
          data: [
            {
              timestamp: "2026-08-03T00:00:00Z",
              value: 999,
              primary_traffic: 999,
            },
          ],
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
        },
      },
    });
    await act(async () => vi.advanceTimersByTimeAsync(0));

    expect(axios.post).toHaveBeenCalledTimes(boundedRequestCount);
    expect(screen.getByTestId("apex-chart")).toHaveAttribute(
      "data-primary-first-y",
      "12",
    );

    axios.post.mockResolvedValueOnce({
      data: {
        result: {
          metric_name: "latency",
          data: [
            {
              timestamp: "2026-08-03T00:00:00Z",
              value: 24,
              primary_traffic: 2,
            },
          ],
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
        },
      },
    });
    act(() => window.dispatchEvent(new CustomEvent("observe-refresh")));
    await act(async () => vi.advanceTimersByTimeAsync(10));

    expect(axios.post).toHaveBeenCalledTimes(3);
    expect(screen.getByTestId("apex-chart")).toHaveAttribute(
      "data-primary-first-y",
      "24",
    );
    expect(
      screen.queryByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).not.toBeInTheDocument();
  });

  it("starts a fresh transport budget when the graph query changes", async () => {
    vi.useFakeTimers();
    axios.post.mockImplementationOnce(() => new Promise(() => {}));
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <PrimaryGraph
          observeIdOverride="project-override"
          selectedInterval="day"
        />
      </QueryClientProvider>,
    );
    await act(async () => vi.advanceTimersByTimeAsync(10));

    await act(async () =>
      vi.advanceTimersByTimeAsync(AGGREGATION_REQUEST_TIMEOUT_MS),
    );
    expect(
      screen.getByText("We couldn't load this data. Please retry in a moment."),
    ).toBeInTheDocument();

    axios.post.mockResolvedValueOnce({
      data: {
        result: {
          metric_name: "latency",
          data: [
            {
              timestamp: "2026-08-03T00:00:00Z",
              value: 36,
              primary_traffic: 3,
            },
          ],
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
        },
      },
    });
    view.rerender(
      <QueryClientProvider client={queryClient}>
        <PrimaryGraph
          observeIdOverride="project-override"
          selectedInterval="hour"
        />
      </QueryClientProvider>,
    );
    await act(async () => vi.advanceTimersByTimeAsync(10));

    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("apex-chart")).toHaveAttribute(
      "data-primary-first-y",
      "36",
    );
    expect(
      screen.queryByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).not.toBeInTheDocument();
  });

  it("aborts the obsolete transport immediately when the graph scope changes", async () => {
    vi.useFakeTimers();
    axios.post
      .mockImplementationOnce(() => new Promise(() => {}))
      .mockResolvedValueOnce({
        data: {
          result: {
            metric_name: "latency",
            data: [
              {
                timestamp: "2026-08-03T00:00:00Z",
                value: 36,
                primary_traffic: 3,
              },
            ],
            query_complete: true,
            query_status: "complete",
            query_sampled: false,
          },
        },
      });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <PrimaryGraph
          observeIdOverride="project-override"
          selectedInterval="day"
        />
      </QueryClientProvider>,
    );
    await act(async () => vi.advanceTimersByTimeAsync(10));
    const obsoleteSignal = axios.post.mock.calls[0][2].signal;
    expect(obsoleteSignal.aborted).toBe(false);

    view.rerender(
      <QueryClientProvider client={queryClient}>
        <PrimaryGraph
          observeIdOverride="project-override"
          selectedInterval="hour"
        />
      </QueryClientProvider>,
    );
    await act(async () => vi.advanceTimersByTimeAsync(10));

    expect(obsoleteSignal.aborted).toBe(true);
    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("apex-chart")).toHaveAttribute(
      "data-primary-first-y",
      "36",
    );
  });

  it("renders a completed exact response without an intermediate empty label", async () => {
    axios.post.mockResolvedValue({
      data: {
        result: {
          metric_name: "latency",
          data: [
            {
              timestamp: "2026-08-03T00:00:00Z",
              value: 12,
              primary_traffic: 1,
            },
          ],
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
        },
      },
    });

    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );

    expect(await screen.findByTestId("apex-chart")).toBeInTheDocument();
    expect(
      screen.queryByText("No data available for this time range"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("status", { name: GRAPH_LOADING_MESSAGE }),
    ).not.toBeInTheDocument();
  });

  it("requests the metric catalog scoped to the current project", async () => {
    renderWithQueryClient(<PrimaryGraph observeIdOverride="project-alpha" />);

    fireEvent.click(await screen.findByTestId("graph-metric-picker-trigger"));
    await waitFor(() => expect(axios.get).toHaveBeenCalled());

    expect(axios.get).toHaveBeenCalledWith("/dashboard/metrics/", {
      params: {
        exclude_custom_attributes: true,
        page: 1,
        page_size: 200,
        project_ids: "project-alpha",
        per_eval_config: true,
      },
      signal: expect.anything(),
      timeout: 9_000,
    });
  });

  it("does not serve one project's metric catalog to another", async () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    const { unmount } = render(
      <QueryClientProvider client={queryClient}>
        <PrimaryGraph observeIdOverride="project-alpha" />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByTestId("graph-metric-picker-trigger"));
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    unmount();

    render(
      <QueryClientProvider client={queryClient}>
        <PrimaryGraph observeIdOverride="project-beta" />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByTestId("graph-metric-picker-trigger"));

    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
    expect(axios.get).toHaveBeenLastCalledWith("/dashboard/metrics/", {
      params: {
        exclude_custom_attributes: true,
        page: 1,
        page_size: 200,
        project_ids: "project-beta",
        per_eval_config: true,
      },
      signal: expect.anything(),
      timeout: 9_000,
    });
  });

  it("refetches graph data once the catalog resolves the selected metric", async () => {
    axios.get.mockResolvedValue({
      data: {
        result: {
          metrics: [
            {
              category: "system_metric",
              name: "latency",
              displayName: "Latency",
              type: "number",
            },
            {
              category: "eval_metric",
              name: "eval-uuid-1",
              displayName: "My Eval",
              type: "number",
            },
          ],
        },
      },
    });

    renderWithQueryClient(
      <PrimaryGraph
        observeIdOverride="project-alpha"
        defaultMetric="eval-uuid-1"
      />,
    );

    fireEvent.click(await screen.findByTestId("graph-metric-picker-trigger"));
    await waitFor(() => expect(axios.post).toHaveBeenCalled());

    await waitFor(() =>
      expect(axios.post.mock.calls.at(-1)[1].req_data_config).toMatchObject({
        id: "eval-uuid-1",
      }),
    );
  });
});
