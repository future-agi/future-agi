import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AGGREGATION_POLL_MAX_ATTEMPTS,
  AGGREGATION_POLL_TIMEOUT_MS,
  AGGREGATION_POLLING_PAUSED_MESSAGE,
  AGGREGATION_PREPARING_MESSAGE,
  getAggregationPollDelay,
  QUERY_FAILED_RETRY_MESSAGE,
} from "src/utils/queryReadState";
import ChartsWithFetch from "../ChartsWithFetch";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  autoRefresh: false,
  context: { selectedInterval: "Hour", filters: [], handleZoomChange: vi.fn() },
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: { project: { getEvalGraph: "/tracer/charts/fetch_graph/" } },
}));
vi.mock("src/hooks/use-local-storage", () => ({
  getStorage: () => mocks.autoRefresh,
}));
vi.mock("../ChartsViewProvider/ChartsViewContext", () => ({
  useChartsViewContext: () => mocks.context,
}));
vi.mock("../ChartsGenerator", async () => {
  const { createElement } = await import("react");
  const { default: PropTypes } = await import("prop-types");
  function MockChartsGenerator({ series }) {
    return createElement(
      "output",
      { "data-testid": "chart-series" },
      JSON.stringify(series),
    );
  }
  MockChartsGenerator.propTypes = { series: PropTypes.array.isRequired };
  return { default: MockChartsGenerator };
});

const evaluation = { id: "eval-polling", name: "Quality", outputType: "score" };
const pendingResponse = (metadata = {}) => ({
  data: {
    status: true,
    result: [
      {
        name: "Quality",
        data: [],
        query_complete: false,
        query_status: "pending",
        query_sampled: false,
        query_refreshing: true,
        query_refresh_failed: false,
        ...metadata,
      },
    ],
  },
});
const exactResponse = (value = 75, metadata = {}) => ({
  data: {
    status: true,
    result: [
      {
        name: "Quality",
        data: [{ timestamp: "2026-11-01T00:00:00Z", value }],
        query_complete: true,
        query_status: "complete",
        query_sampled: false,
        query_refreshing: false,
        query_refresh_failed: false,
        ...metadata,
      },
    ],
  },
});

let queryClient;
function mountChart(props = {}) {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  const element = (overrides = {}) =>
    React.createElement(
      QueryClientProvider,
      { client: queryClient },
      React.createElement(ChartsWithFetch, {
        evaluation,
        observeId: "project-polling",
        inView: true,
        ...props,
        ...overrides,
      }),
    );
  const view = render(element());
  return {
    ...view,
    rerenderChart: (overrides) => view.rerender(element(overrides)),
  };
}

async function tick(milliseconds = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds);
  });
  // Flush React Query's batched observer notifications after the request settles.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
}
async function exhaustPolling() {
  for (
    let attempt = 0;
    attempt <= AGGREGATION_POLL_MAX_ATTEMPTS;
    attempt += 1
  ) {
    await tick(getAggregationPollDelay(attempt) + 10);
  }
}
const plottedSeries = () =>
  JSON.parse(screen.getByTestId("chart-series").textContent);

beforeEach(() => {
  vi.useFakeTimers();
  mocks.get.mockReset();
  mocks.autoRefresh = false;
  mocks.context = {
    selectedInterval: "Hour",
    filters: [],
    handleZoomChange: vi.fn(),
  };
});
afterEach(() => {
  cleanup();
  queryClient?.clear();
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe("ChartsWithFetch bounded snapshot polling", () => {
  it.each([false, undefined])(
    "loads a cold snapshot with autoRefresh=%s and stops after completion",
    async (autoRefresh) => {
      mocks.autoRefresh = autoRefresh;
      mocks.get
        .mockResolvedValueOnce(pendingResponse())
        .mockResolvedValue(exactResponse());
      mountChart();
      await tick();
      expect(screen.getByText(AGGREGATION_PREPARING_MESSAGE)).toBeVisible();
      expect(plottedSeries()).toEqual([]);

      await tick(getAggregationPollDelay(0) + 10);
      expect(mocks.get).toHaveBeenCalledTimes(2);
      expect(plottedSeries()[0].data[0].y).toBe(75);
      expect(
        screen.queryByText(AGGREGATION_PREPARING_MESSAGE),
      ).not.toBeInTheDocument();
      expect(
        mocks.get.mock.calls.every(
          ([, config]) => config.params.refresh === undefined,
        ),
      ).toBe(true);
      await tick(AGGREGATION_POLL_TIMEOUT_MS * 2);
      expect(mocks.get).toHaveBeenCalledTimes(2);
    },
  );

  it("polls query_refreshing while retaining exact series", async () => {
    mocks.get
      .mockResolvedValueOnce(exactResponse(25, { query_refreshing: true }))
      .mockResolvedValue(exactResponse(90));
    mountChart();
    await tick();
    expect(plottedSeries()[0].data[0].y).toBe(25);
    await tick(getAggregationPollDelay(0) + 10);
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(plottedSeries()[0].data[0].y).toBe(90);
    await tick(AGGREGATION_POLL_TIMEOUT_MS * 2);
    expect(mocks.get).toHaveBeenCalledTimes(2);
  });

  it.each([false, true])(
    "pauses at the finite budget with autoRefresh=%s and Retry starts a fresh budget",
    async (autoRefresh) => {
      mocks.autoRefresh = autoRefresh;
      mocks.get.mockResolvedValue(pendingResponse());
      mountChart();
      await tick();
      await exhaustPolling();
      const count = mocks.get.mock.calls.length;
      expect(count).toBeGreaterThan(1);
      expect(count).toBeLessThanOrEqual(AGGREGATION_POLL_MAX_ATTEMPTS + 1);
      expect(
        screen.getByText(AGGREGATION_POLLING_PAUSED_MESSAGE),
      ).toBeVisible();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      await tick(AGGREGATION_POLL_TIMEOUT_MS * 2);
      expect(mocks.get).toHaveBeenCalledTimes(count);

      mocks.autoRefresh = false;
      mocks.get
        .mockResolvedValueOnce(pendingResponse())
        .mockResolvedValue(exactResponse());
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      await tick();
      expect(mocks.get).toHaveBeenCalledTimes(count + 1);
      expect(
        screen.queryByText(AGGREGATION_POLLING_PAUSED_MESSAGE),
      ).not.toBeInTheDocument();
      await tick(getAggregationPollDelay(0) + 10);
      expect(mocks.get).toHaveBeenCalledTimes(count + 2);
      expect(plottedSeries()[0].data[0].y).toBe(75);
    },
  );

  it.each(["pending", "exact"])(
    "stops a failed %s refresh and Retry explicitly restarts the server job",
    async (kind) => {
      mocks.autoRefresh = true;
      const metadata = { query_refreshing: false, query_refresh_failed: true };
      mocks.get.mockResolvedValue(
        kind === "pending"
          ? pendingResponse(metadata)
          : exactResponse(25, metadata),
      );
      mountChart();
      await tick();
      expect(screen.getByRole("alert")).toHaveTextContent(
        QUERY_FAILED_RETRY_MESSAGE,
      );
      expect(plottedSeries()).toEqual(
        kind === "pending" ? [] : expect.any(Array),
      );
      if (kind === "exact") expect(plottedSeries()[0].data[0].y).toBe(25);
      await tick(AGGREGATION_POLL_TIMEOUT_MS * 2);
      expect(mocks.get).toHaveBeenCalledTimes(1);

      mocks.autoRefresh = false;
      mocks.get
        .mockResolvedValueOnce(pendingResponse())
        .mockResolvedValue(exactResponse(90));
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      await tick();
      expect(mocks.get.mock.calls[1][1].params.refresh).toBe(true);
      await tick(getAggregationPollDelay(0) + 10);
      expect(mocks.get.mock.calls[2][1].params.refresh).toBeUndefined();
      expect(plottedSeries()[0].data[0].y).toBe(90);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    },
  );

  it("stops on a polling transport failure instead of falling back to auto-refresh", async () => {
    mocks.autoRefresh = true;
    mocks.get
      .mockResolvedValueOnce(pendingResponse())
      .mockRejectedValue(new Error("request failed"));
    mountChart();
    await tick();
    await tick(getAggregationPollDelay(0) + 10);
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("alert")).toHaveTextContent(
      QUERY_FAILED_RETRY_MESSAGE,
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    await tick(AGGREGATION_POLL_TIMEOUT_MS * 2);
    expect(mocks.get).toHaveBeenCalledTimes(2);
  });

  it("gives a changed filter its own budget after the previous filter paused", async () => {
    mocks.get.mockResolvedValue(pendingResponse());
    const view = mountChart();
    await tick();
    await exhaustPolling();
    expect(screen.getByText(AGGREGATION_POLLING_PAUSED_MESSAGE)).toBeVisible();
    const count = mocks.get.mock.calls.length;

    mocks.context = {
      ...mocks.context,
      filters: [{ key: "name", value: "new-window" }],
    };
    mocks.get
      .mockResolvedValueOnce(pendingResponse())
      .mockResolvedValue(exactResponse(90));
    view.rerenderChart();
    await tick();
    expect(mocks.get).toHaveBeenCalledTimes(count + 1);
    expect(
      screen.queryByText(AGGREGATION_POLLING_PAUSED_MESSAGE),
    ).not.toBeInTheDocument();
    await tick(getAggregationPollDelay(0) + 10);
    expect(mocks.get).toHaveBeenCalledTimes(count + 2);
    expect(plottedSeries()[0].data[0].y).toBe(90);
  });

  it("lets the page Refresh invalidation restart a paused observation window", async () => {
    mocks.get.mockResolvedValue(pendingResponse());
    mountChart();
    await tick();
    await exhaustPolling();
    expect(screen.getByText(AGGREGATION_POLLING_PAUSED_MESSAGE)).toBeVisible();
    const count = mocks.get.mock.calls.length;
    mocks.get
      .mockResolvedValueOnce(pendingResponse())
      .mockResolvedValue(exactResponse());
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["chart-data"] });
    });
    await tick();
    expect(
      screen.queryByText(AGGREGATION_POLLING_PAUSED_MESSAGE),
    ).not.toBeInTheDocument();
    await tick(getAggregationPollDelay(0) + 10);
    expect(mocks.get).toHaveBeenCalledTimes(count + 2);
    expect(plottedSeries()[0].data[0].y).toBe(75);
  });

  it("does not poll off-screen and resumes the same pending snapshot on return", async () => {
    mocks.get
      .mockResolvedValueOnce(pendingResponse())
      .mockResolvedValue(exactResponse());
    const view = mountChart();
    await tick();
    view.rerenderChart({ inView: false });
    await tick(getAggregationPollDelay(0) + 10);
    expect(mocks.get).toHaveBeenCalledTimes(1);
    view.rerenderChart({ inView: true });
    await tick(getAggregationPollDelay(0) + 10);
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(plottedSeries()[0].data[0].y).toBe(75);
  });

  it("cancels the pending timer on unmount", async () => {
    mocks.get.mockResolvedValue(pendingResponse());
    const view = mountChart();
    await tick();
    view.unmount();
    await tick(AGGREGATION_POLL_TIMEOUT_MS * 2);
    expect(mocks.get).toHaveBeenCalledTimes(1);
  });

  it("preserves opt-in ten-second refreshes for completed charts", async () => {
    mocks.autoRefresh = true;
    mocks.get
      .mockResolvedValueOnce(exactResponse(25))
      .mockResolvedValue(exactResponse(90));
    mountChart();
    await tick();
    await tick(10_010);
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(plottedSeries()[0].data[0].y).toBe(90);
  });
});
