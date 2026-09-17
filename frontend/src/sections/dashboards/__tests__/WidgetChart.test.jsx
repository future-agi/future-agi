import React from "react";
import { afterEach, describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "src/utils/test-utils";
import WidgetChart from "../WidgetChart";
import {
  AGGREGATION_POLL_MAX_ATTEMPTS,
  AGGREGATION_POLLING_PAUSED_MESSAGE,
  AGGREGATION_REQUEST_TIMEOUT_MS,
} from "src/utils/queryReadState";
import WidgetPieCharts from "../WidgetPieCharts";
import { NO_DATA_FOR_RANGE_MESSAGE } from "../constants";

const h = vi.hoisted(() => ({
  query: { data: null, isPending: false, isError: false, mutate: vi.fn() },
  apex: vi.fn(),
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardQuery: () => h.query,
}));

vi.mock("react-apexcharts", () => ({
  default: (props) => {
    h.apex(props);
    return (
      <div
        data-testid={`apex-${props.type}`}
        data-series={JSON.stringify(props.series)}
        data-labels={JSON.stringify(props.options?.labels ?? null)}
        data-colors={JSON.stringify(props.options?.colors ?? null)}
      />
    );
  },
}));

vi.mock("../ChartLegend", () => ({
  default: (props) => (
    <div
      data-testid="chart-legend"
      data-items={JSON.stringify(props.items)}
      data-colors={JSON.stringify(props.colors)}
    />
  ),
}));

const baseWidget = {
  id: "w-1",
  query_config: {
    metrics: [{ name: "Latency", aggregation: "avg" }],
  },
  chart_config: { chart_type: "line" },
};

const queryResult = (points) => ({
  data: {
    result: {
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
      query_completed_at: "2026-08-03T02:00:00Z",
      metrics: [
        {
          name: "Latency",
          aggregation: "avg",
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
          series: [{ name: "total", data: points }],
        },
      ],
    },
  },
});

const NO_DATA_MESSAGE = /No data available for this time period/i;
const PREPARING_MESSAGE = /Loading results/i;

describe("WidgetChart — empty time-range state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.query.isPending = false;
    h.query.isError = false;
    h.query.data = null;
  });

  it("shows the empty-range message when the metric's series has zero data points", () => {
    h.query.data = queryResult([]);
    render(<WidgetChart widget={baseWidget} globalDateRange={null} />);

    expect(screen.getByText(NO_DATA_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByTestId("apex-line")).not.toBeInTheDocument();
  });

  it("renders the chart, not the empty-range message, once the series has data points", () => {
    h.query.data = queryResult([
      { timestamp: "2026-07-09T00:00:00Z", value: 12 },
      { timestamp: "2026-07-09T01:00:00Z", value: 18 },
    ]);
    render(<WidgetChart widget={baseWidget} globalDateRange={null} />);

    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
    expect(screen.queryByText(NO_DATA_MESSAGE)).not.toBeInTheDocument();
  });

  it("restores a saved stable-key series selection through the exact-result series builder", () => {
    const response = queryResult([
      { timestamp: "2026-07-09T00:00:00Z", value: 12 },
    ]);
    response.data.result.metrics[0].id = "latency-metric";
    response.data.result.metrics[0].series = [
      {
        name: "us",
        data: [{ timestamp: "2026-07-09T00:00:00Z", value: 12 }],
      },
      {
        name: "eu",
        data: [{ timestamp: "2026-07-09T00:00:00Z", value: 18 }],
      },
    ];
    h.query.data = response;

    render(
      <WidgetChart
        widget={{
          ...baseWidget,
          chart_config: {
            chart_type: "line",
            visible_series: ["latency-metric|avg|eu"],
          },
        }}
        globalDateRange={null}
      />,
    );

    expect(h.apex.mock.calls.at(-1)[0].series.map(({ name }) => name)).toEqual([
      "eu",
    ]);
  });

  it("connects exact line points across null buckets without coercing zeroes", () => {
    h.query.data = queryResult([
      { timestamp: "2026-07-09T00:00:00Z", value: 12 },
      { timestamp: "2026-07-09T01:00:00Z", value: null },
      { timestamp: "2026-07-09T02:00:00Z", value: 0 },
      { timestamp: "2026-07-09T03:00:00Z", value: 18 },
    ]);

    render(<WidgetChart widget={baseWidget} globalDateRange={null} />);

    const renderedData = h.apex.mock.calls.at(-1)[0].series[0].data;
    expect(renderedData.map((point) => point.y)).toEqual([12, 0, 18]);
    expect(renderedData.map((point) => point.x)).toEqual([
      new Date("2026-07-09T00:00:00Z").getTime(),
      new Date("2026-07-09T02:00:00Z").getTime(),
      new Date("2026-07-09T03:00:00Z").getTime(),
    ]);
    expect(
      h.query.data.data.result.metrics[0].series[0].data[1].value,
    ).toBeNull();
  });

  it("keeps null buckets in table data and renders them as a dash", () => {
    h.query.data = queryResult([
      { timestamp: "2026-07-09T00:00:00Z", value: 12 },
      { timestamp: "2026-07-09T01:00:00Z", value: null },
    ]);

    render(
      <WidgetChart
        widget={{ ...baseWidget, chart_config: { chart_type: "table" } }}
        globalDateRange={null}
      />,
    );

    expect(screen.getByText("-")).toBeInTheDocument();
    expect(
      h.query.data.data.result.metrics[0].series[0].data[1].value,
    ).toBeNull();
    expect(h.apex).not.toHaveBeenCalled();
  });

  // Regression guard: hasNoDataForRange must stay ABOVE the metric-card/table/pie/
  // horizontal early returns so those widget types show this message too, instead of
  // falling into their own type-specific render with an empty series.
  it("shows the empty-range message for a pie widget with zero data points, not the pie render", () => {
    h.query.data = queryResult([]);
    const pieWidget = { ...baseWidget, chart_config: { chart_type: "pie" } };
    render(<WidgetChart widget={pieWidget} globalDateRange={null} />);

    expect(screen.getByText(NO_DATA_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByTestId("apex-pie")).not.toBeInTheDocument();
  });
});

describe("WidgetChart — queued exact refresh", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.query.isPending = false;
    h.query.isError = false;
    h.query.data = null;
  });

  afterEach(() => vi.useRealTimers());

  it("never presents the pre-request frame as a completed empty widget", () => {
    h.query.mutate.mockImplementation(() => {});

    render(
      <WidgetChart
        widget={baseWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
      />,
    );

    expect(screen.getByText(PREPARING_MESSAGE)).toBeInTheDocument();
    expect(
      screen.queryByText("No output for the selected inputs."),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(NO_DATA_MESSAGE)).not.toBeInTheDocument();
  });

  it("polls a cold pending read without refresh and settles only on exact completion", async () => {
    vi.useFakeTimers();
    const pendingResponse = {
      data: {
        result: {
          metrics: [],
          query_complete: false,
          query_status: "pending",
          query_sampled: false,
          query_refreshing: true,
        },
      },
    };
    const completedResponse = queryResult([
      { timestamp: "2026-07-09T00:00:00Z", value: 12 },
    ]);
    const onQuerySettled = vi.fn();
    h.query.mutate
      .mockImplementationOnce((_request, options) =>
        options?.onSuccess?.(pendingResponse),
      )
      .mockImplementationOnce((_request, options) =>
        options?.onSuccess?.(completedResponse),
      );

    render(
      <WidgetChart
        widget={baseWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
        onQuerySettled={onQuerySettled}
      />,
    );

    expect(h.query.mutate).toHaveBeenCalledOnce();
    expect(screen.getByText(PREPARING_MESSAGE)).toBeInTheDocument();
    expect(onQuerySettled).not.toHaveBeenCalled();

    await act(async () => vi.advanceTimersByTime(1000));

    expect(h.query.mutate).toHaveBeenCalledTimes(2);
    expect(h.query.mutate.mock.calls[1][0]).toEqual(
      expect.objectContaining({
        queryConfig: baseWidget.query_config,
        refresh: false,
        signal: expect.any(Object),
      }),
    );
    expect(onQuerySettled).toHaveBeenCalledOnce();
    expect(onQuerySettled).toHaveBeenCalledWith(
      expect.objectContaining({
        dashboardId: "dashboard-1",
        exact: true,
        updatedAt: new Date("2026-08-03T02:00:00Z"),
      }),
    );
    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
  });

  it("keeps one stable pending state while a background poll is in flight", async () => {
    vi.useFakeTimers();
    const pendingResponse = {
      data: {
        result: {
          metrics: [],
          query_complete: false,
          query_status: "pending",
          query_sampled: false,
          query_refreshing: true,
        },
      },
    };
    h.query.mutate
      .mockImplementationOnce((_request, options) =>
        options?.onSuccess?.(pendingResponse),
      )
      .mockImplementationOnce(() => {});

    const { rerender } = render(
      <WidgetChart
        widget={baseWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
      />,
    );

    expect(screen.getByText(PREPARING_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();

    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(h.query.mutate).toHaveBeenCalledTimes(2);

    h.query.isPending = true;
    rerender(
      <WidgetChart
        widget={baseWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
      />,
    );

    expect(screen.getByText(PREPARING_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("renders a complete rollup without reporting it as exact", () => {
    const rollupResponse = queryResult([
      { timestamp: "2026-07-09T00:00:00Z", value: 12 },
    ]);
    Object.assign(rollupResponse.data.result, {
      query_exact: false,
      query_provenance: "materialized_rollup",
    });
    const onQuerySettled = vi.fn();
    h.query.mutate.mockImplementation((_request, options) =>
      options?.onSuccess?.(rollupResponse),
    );

    render(
      <WidgetChart
        widget={baseWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
        onQuerySettled={onQuerySettled}
      />,
    );

    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
    expect(onQuerySettled).toHaveBeenCalledWith(
      expect.objectContaining({ exact: false, updatedAt: null }),
    );
  });

  it("keeps cached exact data visible and treats terminal refresh failure as unsettled", async () => {
    vi.useFakeTimers();
    const cachedResponse = queryResult([
      { timestamp: "2026-07-09T00:00:00Z", value: 12 },
    ]);
    cachedResponse.data.result.query_refreshing = true;
    const failedResponse = structuredClone(cachedResponse);
    failedResponse.data.result.query_refreshing = false;
    failedResponse.data.result.query_refresh_failed = true;
    const onQuerySettled = vi.fn();
    h.query.data = cachedResponse;
    h.query.mutate
      .mockImplementationOnce((_request, options) =>
        options?.onSuccess?.(cachedResponse),
      )
      .mockImplementationOnce((_request, options) =>
        options?.onSuccess?.(failedResponse),
      );

    render(
      <WidgetChart
        widget={baseWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
        onQuerySettled={onQuerySettled}
      />,
    );

    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
    expect(onQuerySettled).not.toHaveBeenCalled();

    await act(async () => vi.advanceTimersByTime(1000));

    expect(onQuerySettled).toHaveBeenCalledWith(
      expect.objectContaining({ exact: false, updatedAt: null }),
    );
    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
    expect(screen.queryByText(PREPARING_MESSAGE)).not.toBeInTheDocument();
    expect(
      screen.getByText("We couldn't load this data. Please retry in a moment."),
    ).toBeInTheDocument();
  });

  it("stops retrying after three consecutive polling transport failures", async () => {
    vi.useFakeTimers();
    const pendingResponse = {
      data: {
        result: {
          metrics: [],
          query_complete: false,
          query_status: "pending",
          query_sampled: false,
          query_refreshing: true,
        },
      },
    };
    const onQuerySettled = vi.fn();
    h.query.mutate
      .mockImplementationOnce((_request, options) =>
        options?.onSuccess?.(pendingResponse),
      )
      .mockImplementation((_request, options) =>
        options?.onError?.(new Error("transport failed")),
      );

    render(
      <WidgetChart
        widget={baseWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
        onQuerySettled={onQuerySettled}
      />,
    );

    await act(async () => vi.advanceTimersByTimeAsync(7_010));

    expect(h.query.mutate).toHaveBeenCalledTimes(4);
    expect(onQuerySettled).toHaveBeenCalledOnce();
    expect(onQuerySettled).toHaveBeenCalledWith(
      expect.objectContaining({ exact: false }),
    );
    expect(
      screen.getByText("We couldn't load this data. Please retry in a moment."),
    ).toBeInTheDocument();

    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(h.query.mutate).toHaveBeenCalledTimes(4);
  });

  it("shows a finite retry state for an immediate transport failure", () => {
    const onQuerySettled = vi.fn();
    h.query.mutate.mockImplementation((_request, options) =>
      options?.onError?.(new Error("transport failed")),
    );

    render(
      <WidgetChart
        widget={baseWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
        onQuerySettled={onQuerySettled}
      />,
    );

    expect(screen.queryByText(PREPARING_MESSAGE)).not.toBeInTheDocument();
    expect(
      screen.getByText("We couldn't load this data. Please retry in a moment."),
    ).toBeInTheDocument();
    expect(onQuerySettled).toHaveBeenCalledOnce();
    expect(onQuerySettled).toHaveBeenCalledWith(
      expect.objectContaining({ exact: false, updatedAt: null }),
    );
  });

  it("leaves a cold spinner after the component deadline even when the mutation adapter stays pending", async () => {
    vi.useFakeTimers();
    h.query.isPending = true;
    h.query.mutate.mockImplementation(() => {});
    const onQuerySettled = vi.fn();

    render(
      <WidgetChart
        widget={baseWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
        onQuerySettled={onQuerySettled}
      />,
    );

    expect(screen.getByRole("progressbar")).toBeInTheDocument();

    await act(async () =>
      vi.advanceTimersByTimeAsync(AGGREGATION_REQUEST_TIMEOUT_MS),
    );

    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(
      screen.getByText("We couldn't load this data. Please retry in a moment."),
    ).toBeInTheDocument();
    expect(onQuerySettled).toHaveBeenCalledOnce();
    expect(onQuerySettled).toHaveBeenCalledWith(
      expect.objectContaining({ exact: false, updatedAt: null }),
    );
  });

  it("keeps cached exact data, stops at the finite budget, and resumes on explicit refresh", async () => {
    vi.useFakeTimers();
    const cachedResponse = queryResult([
      { timestamp: "2026-07-09T00:00:00Z", value: 12 },
    ]);
    const pendingResponse = {
      data: {
        result: {
          metrics: [],
          query_complete: false,
          query_status: "pending",
          query_sampled: false,
          query_refreshing: true,
        },
      },
    };
    const onQuerySettled = vi.fn();
    const completedResponse = queryResult([
      { timestamp: "2026-07-09T00:00:00Z", value: 24 },
    ]);
    h.query.data = cachedResponse;
    h.query.mutate.mockImplementation((_request, options) =>
      options?.onSuccess?.(pendingResponse),
    );

    const { rerender } = render(
      <WidgetChart
        widget={baseWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
        onQuerySettled={onQuerySettled}
        refreshRequestId={0}
      />,
    );

    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
    expect(h.query.mutate).toHaveBeenCalledOnce();
    await act(async () => vi.advanceTimersByTimeAsync(500_000));

    const boundedRequestCount = h.query.mutate.mock.calls.length;
    expect(boundedRequestCount).toBeLessThanOrEqual(
      AGGREGATION_POLL_MAX_ATTEMPTS + 1,
    );
    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
    expect(screen.getByText(AGGREGATION_POLLING_PAUSED_MESSAGE)).toBeVisible();
    expect(
      screen.queryByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).not.toBeInTheDocument();
    expect(onQuerySettled).toHaveBeenCalledWith(
      expect.objectContaining({ exact: false, pollingPaused: true }),
    );

    await act(async () => vi.advanceTimersByTimeAsync(500_000));
    expect(h.query.mutate).toHaveBeenCalledTimes(boundedRequestCount);

    h.query.mutate.mockImplementationOnce((_request, options) =>
      options?.onSuccess?.(completedResponse),
    );
    rerender(
      <WidgetChart
        widget={baseWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
        onQuerySettled={onQuerySettled}
        refreshRequestId={1}
      />,
    );
    await act(async () => vi.advanceTimersByTimeAsync(10));

    expect(h.query.mutate).toHaveBeenCalledTimes(boundedRequestCount + 1);
    expect(onQuerySettled).toHaveBeenCalledWith(
      expect.objectContaining({ exact: true }),
    );
    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
  });

  it("pauses a cold pending widget without turning it into a load failure", async () => {
    vi.useFakeTimers();
    const pendingResponse = {
      data: {
        result: {
          metrics: [],
          query_complete: false,
          query_status: "pending",
          query_sampled: false,
          query_refreshing: true,
        },
      },
    };
    const onQuerySettled = vi.fn();
    h.query.mutate.mockImplementation((_request, options) =>
      options?.onSuccess?.(pendingResponse),
    );

    render(
      <WidgetChart
        widget={baseWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
        onQuerySettled={onQuerySettled}
      />,
    );

    await act(async () => vi.advanceTimersByTimeAsync(500_000));

    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByText(AGGREGATION_POLLING_PAUSED_MESSAGE)).toBeVisible();
    expect(
      screen.queryByText(
        "We couldn't load this data. Please retry in a moment.",
      ),
    ).not.toBeInTheDocument();
    expect(onQuerySettled).toHaveBeenCalledWith(
      expect.objectContaining({ exact: false, pollingPaused: true }),
    );
  });

  it("times out an unresolved request while preserving the previous exact snapshot", async () => {
    vi.useFakeTimers();
    const cachedResponse = queryResult([
      { timestamp: "2026-07-09T00:00:00Z", value: 12 },
    ]);
    const onQuerySettled = vi.fn();
    h.query.data = cachedResponse;
    h.query.mutate.mockImplementation(() => {});

    render(
      <WidgetChart
        widget={baseWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
        onQuerySettled={onQuerySettled}
      />,
    );

    expect(h.query.mutate).toHaveBeenCalledOnce();
    const requestSignal = h.query.mutate.mock.calls[0][0].signal;
    expect(requestSignal.aborted).toBe(false);
    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
    expect(onQuerySettled).not.toHaveBeenCalled();

    await act(async () =>
      vi.advanceTimersByTimeAsync(AGGREGATION_REQUEST_TIMEOUT_MS),
    );

    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
    expect(
      screen.getByText("We couldn't load this data. Please retry in a moment."),
    ).toBeInTheDocument();
    expect(onQuerySettled).toHaveBeenCalledOnce();
    expect(onQuerySettled).toHaveBeenCalledWith(
      expect.objectContaining({ exact: false, updatedAt: null }),
    );
    expect(requestSignal.aborted).toBe(true);

    await act(async () =>
      vi.advanceTimersByTimeAsync(AGGREGATION_REQUEST_TIMEOUT_MS * 2),
    );
    expect(h.query.mutate).toHaveBeenCalledOnce();
    expect(onQuerySettled).toHaveBeenCalledOnce();
  });

  it("aborts a cold hung request and leaves a finite retry state", async () => {
    vi.useFakeTimers();
    const onQuerySettled = vi.fn();
    h.query.mutate.mockImplementation(() => {});

    render(
      <WidgetChart
        widget={baseWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
        onQuerySettled={onQuerySettled}
      />,
    );

    const requestSignal = h.query.mutate.mock.calls[0][0].signal;
    expect(requestSignal.aborted).toBe(false);
    expect(screen.getByText(PREPARING_MESSAGE)).toBeInTheDocument();

    await act(async () =>
      vi.advanceTimersByTimeAsync(AGGREGATION_REQUEST_TIMEOUT_MS),
    );

    expect(requestSignal.aborted).toBe(true);
    expect(screen.queryByText(PREPARING_MESSAGE)).not.toBeInTheDocument();
    expect(
      screen.getByText("We couldn't load this data. Please retry in a moment."),
    ).toBeInTheDocument();
    expect(onQuerySettled).toHaveBeenCalledOnce();
  });

  it("aborts an obsolete request and ignores its late response after the query scope changes", () => {
    const requests = [];
    const callbacks = [];
    const onQuerySettled = vi.fn();
    h.query.mutate.mockImplementation((request, options) => {
      requests.push(request);
      callbacks.push(options);
    });

    const view = render(
      <WidgetChart
        widget={baseWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
        onQuerySettled={onQuerySettled}
      />,
    );
    expect(requests).toHaveLength(1);

    const nextWidget = {
      ...baseWidget,
      query_config: {
        metrics: [{ name: "Cost", aggregation: "sum" }],
      },
    };
    view.rerender(
      <WidgetChart
        widget={nextWidget}
        dashboardId="dashboard-1"
        globalDateRange={null}
        onQuerySettled={onQuerySettled}
      />,
    );

    expect(requests).toHaveLength(2);
    expect(requests[0].signal.aborted).toBe(true);
    expect(requests[1].signal.aborted).toBe(false);

    act(() => {
      callbacks[0].onSuccess(
        queryResult([{ timestamp: "2026-07-09T00:00:00Z", value: 999 }]),
      );
    });
    expect(onQuerySettled).not.toHaveBeenCalled();

    act(() => {
      callbacks[1].onSuccess(
        queryResult([{ timestamp: "2026-07-09T00:00:00Z", value: 24 }]),
      );
    });
    expect(onQuerySettled).toHaveBeenCalledOnce();
    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
  });
});

describe("WidgetChart — bounded dashboard read state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.query.isPending = false;
    h.query.isError = false;
    h.query.data = null;
  });

  it("fails a bounded sampled metric closed instead of rendering estimates", () => {
    h.query.data = {
      data: {
        result: {
          metrics: [
            {
              name: "final_status",
              aggregation: "count_distinct",
              query_complete: false,
              query_status: "sampled",
              query_error_code: "sample_limit",
              query_sampling_strategy: "bounded_physical_rows_per_time_bucket",
              query_sampling_interval_seconds: 86400,
              query_sample_limit: 8192,
              query_sample_per_bucket: 128,
              series: [
                {
                  name: "total",
                  data: [{ timestamp: "2026-07-09T00:00:00Z", value: 12 }],
                },
              ],
            },
          ],
        },
      },
    };

    render(<WidgetChart widget={baseWidget} globalDateRange={null} />);

    expect(screen.getByText(PREPARING_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByTestId("apex-line")).not.toBeInTheDocument();
    expect(h.apex).not.toHaveBeenCalled();
  });

  it.each(["metric", "table", "pie", "bar"])(
    "fails sampled payloads closed for the %s render path",
    (chartType) => {
      h.query.data = {
        data: {
          result: {
            metrics: [
              {
                name: "final_status",
                aggregation: "count_distinct",
                query_complete: false,
                query_status: "sampled",
                query_error_code: "sample_limit",
                query_sampling_strategy:
                  "bounded_physical_rows_per_time_bucket",
                query_sampling_interval_seconds: 86400,
                query_sample_limit: 8192,
                query_sample_per_bucket: 128,
                series: [
                  {
                    name: "total",
                    data: [{ timestamp: "2026-07-09T00:00:00Z", value: 12 }],
                  },
                ],
              },
            ],
          },
        },
      };

      render(
        <WidgetChart
          widget={{ ...baseWidget, chart_config: { chart_type: chartType } }}
          globalDateRange={null}
        />,
      );

      expect(screen.getByText(PREPARING_MESSAGE)).toBeInTheDocument();
      expect(h.apex).not.toHaveBeenCalled();
    },
  );

  it("does not plot a malformed sampled metric even when it contains points", () => {
    h.query.data = {
      data: {
        result: {
          metrics: [
            {
              name: "Latency",
              aggregation: "avg",
              query_complete: false,
              query_status: "sampled",
              query_error_code: "query_failed",
              query_sampling_strategy: "bounded_physical_rows_per_time_bucket",
              query_sampling_interval_seconds: 86400,
              query_sample_limit: 8192,
              query_sample_per_bucket: 128,
              series: [
                {
                  name: "total",
                  data: [{ timestamp: "2026-07-09T00:00:00Z", value: 999 }],
                },
              ],
            },
          ],
        },
      },
    };

    render(<WidgetChart widget={baseWidget} globalDateRange={null} />);

    expect(screen.getByText(PREPARING_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByTestId("apex-line")).not.toBeInTheDocument();
    expect(h.apex).not.toHaveBeenCalled();
  });

  it("does not plot a degraded read-budget metric as exact data", () => {
    h.query.data = {
      data: {
        result: {
          metrics: [
            {
              name: "Latency",
              aggregation: "avg",
              query_complete: false,
              query_status: "degraded",
              query_error_code: "read_budget_exceeded",
              series: [
                {
                  name: "total",
                  data: [{ timestamp: "2026-07-09T00:00:00Z", value: 999 }],
                },
              ],
            },
          ],
        },
      },
    };

    render(<WidgetChart widget={baseWidget} globalDateRange={null} />);

    expect(screen.getByText(PREPARING_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByTestId("apex-line")).not.toBeInTheDocument();
    expect(h.apex).not.toHaveBeenCalled();
  });

  it.each([
    [
      "sampled",
      {
        query_complete: false,
        query_status: "sampled",
        query_error_code: "sample_limit",
        query_sampling_strategy: "bounded_physical_rows_per_time_bucket",
        query_sampling_interval_seconds: 86400,
        query_sample_limit: 8192,
        query_sample_per_bucket: 128,
      },
    ],
    [
      "degraded",
      {
        query_complete: false,
        query_status: "degraded",
        query_error_code: "read_budget_exceeded",
      },
    ],
    ["error", { queryReadState: "error" }],
  ])(
    "fails the whole widget closed for complete + %s metrics",
    (_, unavailableState) => {
      const metricPoint = {
        name: "total",
        data: [{ timestamp: "2026-07-09T00:00:00Z", value: 12 }],
      };
      h.query.data = {
        data: {
          result: {
            metrics: [
              {
                name: "Latency",
                aggregation: "avg",
                query_complete: true,
                query_status: "complete",
                query_sampled: false,
                series: [metricPoint],
              },
              {
                name: "Cost",
                aggregation: "sum",
                ...unavailableState,
                series: [
                  {
                    ...metricPoint,
                    data: [{ ...metricPoint.data[0], value: 999 }],
                  },
                ],
              },
            ],
          },
        },
      };

      render(<WidgetChart widget={baseWidget} globalDateRange={null} />);

      expect(screen.getByText(PREPARING_MESSAGE)).toBeInTheDocument();
      expect(screen.queryByTestId("apex-line")).not.toBeInTheDocument();
      expect(h.apex).not.toHaveBeenCalled();
    },
  );

  it("keeps the last exact chart when a manual refresh returns sampled data", async () => {
    const exactResponse = queryResult([
      { timestamp: "2026-07-09T00:00:00Z", value: 12 },
      { timestamp: "2026-07-09T01:00:00Z", value: null },
    ]);
    const sampledResponse = {
      data: {
        result: {
          metrics: [
            {
              name: "Latency",
              aggregation: "avg",
              query_complete: false,
              query_status: "sampled",
              query_error_code: "sample_limit",
              query_sampling_strategy: "bounded_physical_rows_per_time_bucket",
              query_sampling_interval_seconds: 3600,
              query_sample_limit: 8192,
              query_sample_per_bucket: 128,
              series: [
                {
                  name: "total",
                  data: [{ timestamp: "2026-07-09T00:00:00Z", value: 999 }],
                },
              ],
            },
          ],
        },
      },
    };
    let response = exactResponse;
    h.query.data = exactResponse;
    h.query.mutate.mockImplementation((_request, options) => {
      options?.onSuccess?.(response);
    });

    const { rerender } = render(
      <WidgetChart
        widget={baseWidget}
        globalDateRange={null}
        refreshRequestId={0}
      />,
    );
    expect(h.apex.mock.calls.at(-1)[0].series[0].data[0].y).toBe(12);

    response = sampledResponse;
    rerender(
      <WidgetChart
        widget={baseWidget}
        globalDateRange={null}
        refreshRequestId={1}
      />,
    );

    await waitFor(() => expect(h.query.mutate).toHaveBeenCalledTimes(2));
    expect(h.apex.mock.calls.at(-1)[0].series[0].data[0].y).toBe(12);
    expect(h.apex.mock.calls.at(-1)[0].series[0].data).toHaveLength(1);
    expect(
      exactResponse.data.result.metrics[0].series[0].data[1].value,
    ).toBeNull();
    expect(h.query.mutate.mock.calls.at(-1)[0]).toEqual(
      expect.objectContaining({
        queryConfig: baseWidget.query_config,
        refresh: true,
        signal: expect.any(Object),
      }),
    );
    await waitFor(() =>
      expect(screen.queryByText(/sampled estimates/i)).not.toBeInTheDocument(),
    );
    expect(screen.queryByText(PREPARING_MESSAGE)).not.toBeInTheDocument();
  });
});

// A pie must never combine unrelated metrics into one donut. With a breakdown
// each metric gets its own pie; without one, render the per-metric scalar cards.
describe("WidgetChart — pie with multiple metrics", () => {
  const pt = (value, hour = 0) => ({
    timestamp: `2026-07-09T0${hour}:00:00Z`,
    value,
  });

  const multiMetricResult = (metrics) => ({
    data: {
      result: {
        query_complete: true,
        query_status: "complete",
        query_sampled: false,
        query_completed_at: "2026-08-26T00:00:00Z",
        metrics: metrics.map((metric) => ({
          ...metric,
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
        })),
      },
    },
  });

  const pieWidget = (metrics, breakdowns = []) => ({
    id: "w-pie",
    query_config: { metrics, breakdowns },
    chart_config: { chart_type: "pie" },
  });

  beforeEach(() => {
    vi.clearAllMocks();
    h.query.isPending = false;
    h.query.isError = false;
    h.query.data = null;
  });

  it("renders one donut per metric when three metrics are broken down", () => {
    h.query.data = multiMetricResult([
      {
        name: "Tokens",
        aggregation: "avg",
        unit: "tokens",
        series: [
          { name: "proj-a", data: [pt(10), pt(20, 1)] },
          { name: "proj-b", data: [pt(30), pt(40, 1)] },
        ],
      },
      {
        name: "Input Tokens",
        aggregation: "avg",
        unit: "tokens",
        series: [
          { name: "proj-a", data: [pt(5), pt(7, 1)] },
          { name: "proj-b", data: [pt(9), pt(11, 1)] },
        ],
      },
      {
        name: "Latency",
        aggregation: "max",
        unit: "ms",
        series: [
          { name: "proj-a", data: [pt(100), pt(200, 1)] },
          { name: "proj-b", data: [pt(300), pt(400, 1)] },
        ],
      },
    ]);
    render(
      <WidgetChart
        widget={pieWidget(
          [
            { name: "Tokens", aggregation: "avg" },
            { name: "Input Tokens", aggregation: "avg" },
            { name: "Latency", aggregation: "max" },
          ],
          [{ name: "project" }],
        )}
        globalDateRange={null}
      />,
    );

    const donuts = screen.getAllByTestId("apex-donut");
    expect(donuts).toHaveLength(3);

    // Each donut holds ONLY its own metric's slices, valued by that metric's
    // own aggregation: avg for the token metrics, max for latency.
    const seriesOf = (el) => JSON.parse(el.getAttribute("data-series"));
    const labelsOf = (el) => JSON.parse(el.getAttribute("data-labels"));
    expect(donuts.map(seriesOf)).toEqual([
      [15, 35],
      [6, 10],
      [200, 400],
    ]);
    donuts.forEach((d) => expect(labelsOf(d)).toEqual(["proj-a", "proj-b"]));
  });

  it("renders a single donut for one metric broken down, the case that is already correct", () => {
    h.query.data = multiMetricResult([
      {
        name: "Latency",
        aggregation: "avg",
        unit: "ms",
        series: [
          { name: "proj-a", data: [pt(100), pt(200, 1)] },
          { name: "proj-b", data: [pt(300), pt(400, 1)] },
        ],
      },
    ]);
    render(
      <WidgetChart
        widget={pieWidget(
          [{ name: "Latency", aggregation: "avg" }],
          [{ name: "project" }],
        )}
        globalDateRange={null}
      />,
    );

    const donuts = screen.getAllByTestId("apex-donut");
    expect(donuts).toHaveLength(1);
    expect(JSON.parse(donuts[0].getAttribute("data-series"))).toEqual([
      150, 350,
    ]);
  });

  it("falls back to per-metric numbers instead of full circles when there is no breakdown", () => {
    h.query.data = multiMetricResult([
      {
        name: "Tokens",
        aggregation: "sum",
        unit: "tokens",
        series: [{ name: "total", data: [pt(10), pt(20, 1)] }],
      },
      {
        name: "Latency",
        aggregation: "max",
        unit: "ms",
        series: [{ name: "total", data: [pt(100), pt(200, 1)] }],
      },
    ]);
    render(
      <WidgetChart
        widget={pieWidget([
          { name: "Tokens", aggregation: "sum" },
          { name: "Latency", aggregation: "max" },
        ])}
        globalDateRange={null}
      />,
    );

    expect(screen.queryByTestId("apex-donut")).not.toBeInTheDocument();
    // sum of buckets for Tokens, max of buckets for Latency — each formatted
    // in its OWN unit, not a blanked shared mixed-unit config.
    expect(screen.getByText("30.00 tokens")).toBeInTheDocument();
    expect(screen.getByText("200.00 ms")).toBeInTheDocument();
  });

  it("keeps every metric's slices when the flat series list exceeds the global top-10 cap", () => {
    // 2 metrics x 6 breakdown values = 12 series. A global top-10 filter would
    // starve the lower-valued metric; the per-metric cap must apply instead.
    const bd = (n) => `p${n}`;
    h.query.data = multiMetricResult([
      {
        name: "Big",
        aggregation: "sum",
        unit: "tokens",
        series: Array.from({ length: 6 }, (_, i) => ({
          name: bd(i),
          data: [pt((i + 1) * 100)],
        })),
      },
      {
        name: "Small",
        aggregation: "sum",
        unit: "tokens",
        series: Array.from({ length: 6 }, (_, i) => ({
          name: bd(i),
          data: [pt(i + 1)],
        })),
      },
    ]);
    render(
      <WidgetChart
        widget={pieWidget(
          [
            { name: "Big", aggregation: "sum" },
            { name: "Small", aggregation: "sum" },
          ],
          [{ name: "project" }],
        )}
        globalDateRange={null}
      />,
    );

    const donuts = screen.getAllByTestId("apex-donut");
    expect(donuts).toHaveLength(2);
    const seriesOf = (el) => JSON.parse(el.getAttribute("data-series"));
    expect(seriesOf(donuts[0])).toHaveLength(6);
    expect(seriesOf(donuts[1])).toHaveLength(6);
    expect(seriesOf(donuts[1])).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("keeps a panel for an all-zero metric instead of silently dropping it", () => {
    // A metric the user added must never just vanish — that reads as the add
    // having failed. Real case: traces that record no time-to-first-token.
    h.query.data = multiMetricResult([
      {
        name: "Latency",
        aggregation: "avg",
        unit: "ms",
        series: [
          { name: "proj-a", data: [pt(100)] },
          { name: "proj-b", data: [pt(300)] },
        ],
      },
      {
        name: "Time to First Token",
        aggregation: "median",
        unit: "ms",
        series: [
          { name: "proj-a", data: [pt(0)] },
          { name: "proj-b", data: [pt(0)] },
        ],
      },
    ]);
    render(
      <WidgetChart
        widget={pieWidget(
          [
            { name: "Latency", aggregation: "avg" },
            { name: "Time to First Token", aggregation: "median" },
          ],
          [{ name: "project" }],
        )}
        globalDateRange={null}
      />,
    );

    // One drawable ring, and the zero metric still has its own labelled panel.
    expect(screen.getAllByTestId("apex-donut")).toHaveLength(1);
    expect(
      screen.getByText("Nothing to chart for this metric"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Time to First Token \(median\)/),
    ).toBeInTheDocument();
  });

  it("shows the no-data message rather than a blank canvas when every bucket is null", () => {
    h.query.data = multiMetricResult([
      {
        name: "Tokens",
        aggregation: "avg",
        unit: "tokens",
        series: [
          { name: "proj-a", data: [pt(null), pt(null, 1)] },
          { name: "proj-b", data: [pt(null), pt(null, 1)] },
        ],
      },
    ]);
    render(
      <WidgetChart
        widget={pieWidget(
          [{ name: "Tokens", aggregation: "avg" }],
          [{ name: "project" }],
        )}
        globalDateRange={null}
      />,
    );

    expect(screen.queryByTestId("apex-donut")).not.toBeInTheDocument();
    expect(screen.getByText(NO_DATA_MESSAGE)).toBeInTheDocument();
  });
});

// Review comment 4 on PR #2074: the guard lived at one of the two call sites,
// so the editor and the saved widget answered all-null data differently.
describe("WidgetPieCharts — nothing to draw in any metric", () => {
  const group = (metricIndex, metricName, hasValues, slices = []) => ({
    metricIndex,
    metricName,
    aggregation: "sum",
    unit: "",
    hasValues,
    slices,
  });
  const renderPies = (groups) =>
    render(
      <WidgetPieCharts
        groups={groups}
        colorFor={() => "#000000"}
        baseFormatConfig={{}}
        fallbackDecimals={2}
      />,
    );

  it("shows a single no-data message, not one panel per metric", () => {
    renderPies([group(0, "Tokens", false), group(1, "Latency", false)]);
    expect(screen.getByText(NO_DATA_FOR_RANGE_MESSAGE)).toBeInTheDocument();
    expect(
      screen.queryAllByText(/Nothing to chart for this metric/),
    ).toHaveLength(0);
  });

  it("still renders per-metric panels when one metric has data", () => {
    renderPies([
      group(0, "Tokens", true, [{ name: "alpha", value: 5 }]),
      group(1, "Latency", false),
    ]);
    expect(screen.queryByText(NO_DATA_FOR_RANGE_MESSAGE)).toBeNull();
    expect(
      screen.getByText(/Nothing to chart for this metric/),
    ).toBeInTheDocument();
  });
});

// Regression guard for TH-7679. The legend used to be handed the raw palette and
// index it positionally (COLORS[i]), while the lines were coloured by a hash of
// the series name — so swatch and line agreed only by coincidence. Both must now
// come from the same per-name lookup.
describe("WidgetChart — legend swatches match the plotted line colours", () => {
  const multiSeriesResult = (aggregations) => ({
    data: {
      result: {
        query_complete: true,
        query_status: "complete",
        query_sampled: false,
        query_completed_at: "2026-08-03T02:00:00Z",
        metrics: aggregations.map((aggregation) => ({
          name: "Latency",
          aggregation,
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
          series: [
            {
              name: "total",
              data: [
                { timestamp: "2026-07-09T00:00:00Z", value: 12 },
                { timestamp: "2026-07-09T01:00:00Z", value: 18 },
              ],
            },
          ],
        })),
      },
    },
  });

  const multiWidget = (aggregations) => ({
    ...baseWidget,
    query_config: {
      metrics: aggregations.map((aggregation) => ({
        name: "Latency",
        aggregation,
      })),
    },
  });

  beforeEach(() => {
    vi.clearAllMocks();
    h.query.isPending = false;
    h.query.isError = false;
    h.query.data = null;
  });

  it("gives the legend exactly the colours the chart draws the lines with", () => {
    const aggs = ["p95", "p99", "p50"];
    h.query.data = multiSeriesResult(aggs);
    render(<WidgetChart widget={multiWidget(aggs)} globalDateRange={null} />);

    const legend = screen.getByTestId("chart-legend");
    const chart = screen.getByTestId("apex-line");

    const legendColors = JSON.parse(legend.getAttribute("data-colors"));
    const lineColors = JSON.parse(chart.getAttribute("data-colors"));

    expect(legendColors).toEqual(lineColors);
  });

  it("keeps swatch and line aligned per series name, not per position", () => {
    const aggs = ["p95", "p99", "p50"];
    h.query.data = multiSeriesResult(aggs);
    render(<WidgetChart widget={multiWidget(aggs)} globalDateRange={null} />);

    const legend = screen.getByTestId("chart-legend");
    const items = JSON.parse(legend.getAttribute("data-items"));
    const legendColors = JSON.parse(legend.getAttribute("data-colors"));
    const lineColors = JSON.parse(
      screen.getByTestId("apex-line").getAttribute("data-colors"),
    );

    expect(items).toEqual(["Latency (p95)", "Latency (p99)", "Latency (p50)"]);
    items.forEach((_, i) => {
      expect(legendColors[i]).toBe(lineColors[i]);
    });
    // The names above hash away from the identity mapping, so a positional
    // legend would disagree here — that is exactly the bug being guarded.
    expect(new Set(legendColors).size).toBe(items.length);
  });
});
