import React from "react";
import { afterEach, describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "src/utils/test-utils";
import WidgetChart from "../WidgetChart";
import { AGGREGATION_POLL_TIMEOUT_MS } from "src/utils/queryReadState";

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
    return <div data-testid={`apex-${props.type}`} />;
  },
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
    expect(h.query.mutate.mock.calls[1][0]).toEqual({
      queryConfig: baseWidget.query_config,
      refresh: false,
    });
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

  it("stops a never-settling job, preserves cached exact data, and exposes a retry state", async () => {
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
    h.query.data = cachedResponse;
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

    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
    expect(h.query.mutate).toHaveBeenCalledOnce();
    await act(async () =>
      vi.advanceTimersByTimeAsync(AGGREGATION_POLL_TIMEOUT_MS),
    );

    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
    expect(
      screen.getByText("We couldn't load this data. Please retry in a moment."),
    ).toBeInTheDocument();
    expect(onQuerySettled).toHaveBeenCalledOnce();
    expect(onQuerySettled).toHaveBeenCalledWith(
      expect.objectContaining({ exact: false, updatedAt: null }),
    );
    const boundedRequestCount = h.query.mutate.mock.calls.length;

    await act(async () =>
      vi.advanceTimersByTimeAsync(AGGREGATION_POLL_TIMEOUT_MS * 2),
    );
    expect(h.query.mutate).toHaveBeenCalledTimes(boundedRequestCount);
    expect(boundedRequestCount).toBeLessThanOrEqual(12);
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
    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
    expect(onQuerySettled).not.toHaveBeenCalled();

    await act(async () =>
      vi.advanceTimersByTimeAsync(AGGREGATION_POLL_TIMEOUT_MS),
    );

    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
    expect(
      screen.getByText("We couldn't load this data. Please retry in a moment."),
    ).toBeInTheDocument();
    expect(onQuerySettled).toHaveBeenCalledOnce();
    expect(onQuerySettled).toHaveBeenCalledWith(
      expect.objectContaining({ exact: false, updatedAt: null }),
    );

    await act(async () =>
      vi.advanceTimersByTimeAsync(AGGREGATION_POLL_TIMEOUT_MS * 2),
    );
    expect(h.query.mutate).toHaveBeenCalledOnce();
    expect(onQuerySettled).toHaveBeenCalledOnce();
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
    expect(h.query.mutate.mock.calls.at(-1)[0]).toEqual({
      queryConfig: baseWidget.query_config,
      refresh: true,
    });
    await waitFor(() =>
      expect(screen.queryByText(/sampled estimates/i)).not.toBeInTheDocument(),
    );
    expect(screen.queryByText(PREPARING_MESSAGE)).not.toBeInTheDocument();
  });
});
