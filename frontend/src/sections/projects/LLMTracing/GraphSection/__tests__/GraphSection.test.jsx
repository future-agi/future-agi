import React from "react";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import {
  AGGREGATION_POLL_MAX_ATTEMPTS,
  AGGREGATION_POLLING_PAUSED_MESSAGE,
  AGGREGATION_REQUEST_TIMEOUT_MS,
  GRAPH_LOADING_MESSAGE,
  QUERY_FAILED_RETRY_MESSAGE,
} from "src/utils/queryReadState";
import GraphSection from "../GraphSection";

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

vi.mock("../LeftControl", () => ({
  default: ({ onGraphConfigChange }) => (
    <>
      <button
        type="button"
        onClick={() =>
          onGraphConfigChange({
            id: "latency",
            type: "SYSTEM_METRIC",
          })
        }
      >
        Select latency
      </button>
      <button
        type="button"
        onClick={() =>
          onGraphConfigChange({
            id: "tokens",
            type: "SYSTEM_METRIC",
          })
        }
      >
        Select tokens
      </button>
      <button
        type="button"
        onClick={() =>
          onGraphConfigChange({
            id: "config-1",
            type: "EVAL",
            output_type: "SCORE",
          })
        }
      >
        Select eval
      </button>
      <button
        type="button"
        onClick={() =>
          onGraphConfigChange({
            id: "label-1",
            type: "ANNOTATION",
            output_type: "categorical",
          })
        }
      >
        Select annotation
      </button>
    </>
  ),
}));

vi.mock("../RightControl", () => ({ default: () => null }));
vi.mock("../Legend", () => ({ default: () => null }));
vi.mock("../GraphSkeleton", () => ({ default: () => null }));
vi.mock("src/assets/illustrations/empty-graph", () => ({
  default: () => null,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("../../common", () => ({
  FILTER_FOR_HAS_EVAL: { column_id: "has_eval" },
  toBackendFilters: (filters) => filters,
}));
vi.mock("src/components/show", () => ({
  ShowComponent: ({ condition, children }) => (condition ? children : null),
}));
vi.mock("src/sections/projects/LLMTracing/states", () => ({
  useLLMTracingStoreShallow: (selector) =>
    selector({
      primaryCollapsed: false,
      setPrimaryCollapsed: vi.fn(),
    }),
}));
vi.mock("react-router", async (importOriginal) => {
  const original = await importOriginal();
  return { ...original, useParams: () => ({ observeId: "project-1" }) };
});
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: {
    project: {
      getTraceGraphData: () => "/tracer/trace/get_graph_methods/",
      getSpanGraphData: () => "/tracer/observation-span/get_graph_methods/",
    },
  },
}));

function renderGraph({ selectedTab = "trace" } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <GraphSection
        selectedTab={selectedTab}
        filters={[]}
        showCompare={false}
        selectedGraphProperty="latency"
        selectedGraphEvals={[]}
        setSelectedGraphEvals={vi.fn()}
        setSelectedGraphProperty={vi.fn()}
        selectedGraphAttributes={{}}
        setSelectedGraphAttributes={vi.fn()}
        compareType="primary"
        dateFilter={{
          dateFilter: ["2026-08-02T00:00:00Z", "2026-08-03T00:00:00Z"],
          dateOption: "Custom",
        }}
        setDateFilter={vi.fn()}
        selectedInterval="hour"
        setSelectedInterval={vi.fn()}
        lineColor="#3366ff"
        trafficColor="#99aaff"
      />
    </QueryClientProvider>,
  );
}

describe("GraphSection exact graph boundary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => vi.useRealTimers());

  it("does not chart points carried by a degraded response", async () => {
    axios.post.mockResolvedValue({
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
          query_status: "degraded",
          query_error_code: "sample_limit",
        },
      },
    });

    renderGraph();
    fireEvent.click(screen.getByRole("button", { name: "Select latency" }));

    expect(
      await screen.findByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(axios.post).toHaveBeenCalledOnce());
    expect(screen.queryByTestId("apex-chart")).not.toBeInTheDocument();
  });

  it("does not chart explicitly sampled points", async () => {
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
          query_error_code: "sample_limit",
          query_sampling_strategy: "time_stratified_latest_state",
          query_sampling_strata: 8,
          query_sampling_strata_completed: 8,
        },
      },
    });

    renderGraph();
    fireEvent.click(screen.getByRole("button", { name: "Select latency" }));

    expect(
      await screen.findByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(axios.post).toHaveBeenCalledOnce());
    expect(axios.post).toHaveBeenCalledWith(
      "/tracer/trace/get_graph_methods/",
      expect.any(Object),
      expect.objectContaining({ params: undefined }),
    );
    expect(screen.queryByTestId("apex-chart")).not.toBeInTheDocument();
    expect(screen.queryByText(/sampled estimates/i)).not.toBeInTheDocument();
  });

  it("shows a terminal request failure as a generic retry, not loading", async () => {
    axios.post.mockRejectedValue(
      new Error("Code: 159 DB::Exception: Timeout exceeded Stack trace"),
    );

    renderGraph();
    fireEvent.click(screen.getByRole("button", { name: "Select latency" }));

    expect(
      await screen.findByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("status", { name: GRAPH_LOADING_MESSAGE }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/DB::Exception/i)).not.toBeInTheDocument();
  });

  it("keeps the accessible graph skeleton while an exact request is pending", async () => {
    axios.post.mockResolvedValue({
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
    });

    renderGraph();
    fireEvent.click(screen.getByRole("button", { name: "Select latency" }));

    await waitFor(() => expect(axios.post).toHaveBeenCalledOnce());
    expect(
      screen.getByRole("status", { name: GRAPH_LOADING_MESSAGE }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("apex-chart")).not.toBeInTheDocument();
  });

  it("renders a completed exact response without waiting for snapshot persistence", async () => {
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

    renderGraph();
    fireEvent.click(screen.getByRole("button", { name: "Select latency" }));

    expect(await screen.findByTestId("apex-chart")).toBeInTheDocument();
    expect(
      screen.queryByRole("status", { name: GRAPH_LOADING_MESSAGE }),
    ).not.toBeInTheDocument();
  });

  it("renders a complete empty graph as empty, not loading or a chart", async () => {
    axios.post.mockResolvedValue({
      data: {
        status: true,
        result: {
          metric_name: "latency",
          data: [],
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
        },
      },
    });

    renderGraph();
    fireEvent.click(screen.getByRole("button", { name: "Select latency" }));

    expect(
      await screen.findByText("No data available for this time range"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("apex-chart")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("status", { name: GRAPH_LOADING_MESSAGE }),
    ).not.toBeInTheDocument();
  });

  it("treats nullable exact buckets without values as an empty graph", async () => {
    axios.post.mockResolvedValue({
      data: {
        status: true,
        result: {
          metric_name: "latency",
          data: [
            {
              timestamp: "2026-08-03T00:00:00Z",
              value: null,
              primary_traffic: null,
            },
          ],
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
        },
      },
    });

    renderGraph();
    fireEvent.click(screen.getByRole("button", { name: "Select latency" }));

    expect(
      await screen.findByText("No data available for this time range"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("apex-chart")).not.toBeInTheDocument();
  });

  it("refetches a span graph with the newly selected metric configuration", async () => {
    axios.post.mockResolvedValue({
      data: {
        result: {
          metric_name: "latency",
          data: [],
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
        },
      },
    });

    renderGraph({ selectedTab: "spans" });
    fireEvent.click(screen.getByRole("button", { name: "Select latency" }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(axios.post).toHaveBeenLastCalledWith(
      "/tracer/observation-span/get_graph_methods/",
      expect.objectContaining({
        req_data_config: {
          id: "latency",
          type: "SYSTEM_METRIC",
          property_id: "system_attribute:spans:latency",
          source: "traces",
        },
      }),
      expect.objectContaining({ params: undefined }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Select tokens" }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(axios.post).toHaveBeenLastCalledWith(
      "/tracer/observation-span/get_graph_methods/",
      expect.objectContaining({
        req_data_config: {
          id: "tokens",
          type: "SYSTEM_METRIC",
          property_id: "system_attribute:spans:tokens",
          source: "traces",
        },
      }),
      expect.objectContaining({ params: undefined }),
    );
  });

  it("sends unambiguous eval-config and annotation registry identities", async () => {
    axios.post.mockResolvedValue({
      data: {
        result: {
          metric_name: "config-1",
          data: [],
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
        },
      },
    });

    renderGraph();
    fireEvent.click(screen.getByRole("button", { name: "Select eval" }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(axios.post.mock.calls.at(-1)[1].req_data_config).toEqual(
      expect.objectContaining({
        id: "config-1",
        type: "EVAL",
        property_id: "eval_config:config-1",
        source: "traces",
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Select annotation" }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(axios.post.mock.calls.at(-1)[1].req_data_config).toEqual(
      expect.objectContaining({
        id: "label-1",
        type: "ANNOTATION",
        property_id: "annotation:label-1",
        source: "traces",
      }),
    );
  });

  it("stops a pending graph at the finite budget and resumes only after explicit refresh", async () => {
    vi.useFakeTimers();
    axios.post.mockResolvedValue({
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
    });

    renderGraph();
    fireEvent.click(screen.getByRole("button", { name: "Select latency" }));
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

    await act(async () => vi.advanceTimersByTimeAsync(500_000));
    expect(axios.post).toHaveBeenCalledTimes(boundedRequestCount);

    axios.post.mockResolvedValueOnce({
      data: {
        result: {
          metric_name: "latency",
          data: [
            {
              timestamp: "2026-08-03T00:00:00Z",
              value: 18,
              primary_traffic: 2,
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
      "18",
    );
    expect(
      screen.queryByRole("status", { name: GRAPH_LOADING_MESSAGE }),
    ).not.toBeInTheDocument();
  });

  it("terminalizes a failed exact refresh instead of keeping the graph skeleton", async () => {
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

    renderGraph();
    fireEvent.click(screen.getByRole("button", { name: "Select latency" }));
    await act(async () => vi.advanceTimersByTimeAsync(10_000));

    expect(axios.post).toHaveBeenCalledOnce();
    expect(screen.getByText(QUERY_FAILED_RETRY_MESSAGE)).toBeVisible();
    expect(
      screen.queryByRole("status", { name: GRAPH_LOADING_MESSAGE }),
    ).not.toBeInTheDocument();
  });

  it.each(["trace", "spans"])(
    "publishes a terminal non-refreshing state after three %s polling transport failures",
    async (selectedTab) => {
      vi.useFakeTimers();
      const refreshStates = [];
      const recordRefreshState = (event) => {
        if (event.detail?.observeId === "project-1") {
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

      renderGraph({ selectedTab });
      fireEvent.click(screen.getByRole("button", { name: "Select latency" }));
      await act(async () => vi.advanceTimersByTimeAsync(10));

      expect(refreshStates.at(-1)).toBe(true);

      // Split timer advancement across event-loop turns so React commits the
      // terminal state before any later interval can be scheduled.
      await act(async () => vi.advanceTimersByTimeAsync(1_000));
      expect(axios.post).toHaveBeenCalledTimes(2);
      await act(async () => vi.advanceTimersByTimeAsync(2_000));
      expect(axios.post).toHaveBeenCalledTimes(3);
      await act(async () => vi.advanceTimersByTimeAsync(4_000));

      expect(axios.post).toHaveBeenCalledTimes(4);
      expect(
        screen.getByText(
          "We couldn't load this data. Please retry in a moment.",
        ),
      ).toBeInTheDocument();
      expect(refreshStates.at(-1)).toBe(false);

      await act(async () => vi.advanceTimersByTimeAsync(60_000));
      expect(axios.post).toHaveBeenCalledTimes(4);
      expect(refreshStates.at(-1)).toBe(false);
      window.removeEventListener(
        "observe-aggregation-refresh-state",
        recordRefreshState,
      );
    },
  );

  it("bounds a never-resolving transport, ignores its late response, and restarts on refresh", async () => {
    vi.useFakeTimers();
    let resolveLateRequest;
    axios.post.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveLateRequest = resolve;
        }),
    );

    renderGraph();
    fireEvent.click(screen.getByRole("button", { name: "Select latency" }));
    await act(async () => vi.advanceTimersByTimeAsync(10));
    expect(axios.post).toHaveBeenCalledOnce();
    expect(
      screen.queryByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).not.toBeInTheDocument();

    await act(async () =>
      vi.advanceTimersByTimeAsync(AGGREGATION_REQUEST_TIMEOUT_MS),
    );
    expect(
      screen.getByText("We couldn't load this data. Please retry in a moment."),
    ).toBeInTheDocument();
    expect(axios.post).toHaveBeenCalledOnce();

    resolveLateRequest({
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
    expect(screen.queryByTestId("apex-chart")).not.toBeInTheDocument();

    axios.post.mockResolvedValueOnce({
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
    act(() => window.dispatchEvent(new CustomEvent("observe-refresh")));
    await act(async () => vi.advanceTimersByTimeAsync(10));

    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("apex-chart")).toHaveAttribute(
      "data-primary-first-y",
      "12",
    );
    expect(
      screen.queryByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).not.toBeInTheDocument();
  });
});
