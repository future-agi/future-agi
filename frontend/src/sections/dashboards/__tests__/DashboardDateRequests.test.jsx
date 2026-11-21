import React from "react";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import DashboardDetailView from "../DashboardDetailView";
import {
  resolveGlobalDateRange,
  toTimeRangePayload,
} from "../dashboardDateRange";

// Keep the real dashboard, calendar, WidgetChart, React Query and Axios request
// path. MSW replaces only the server; metric cards render without a chart stub.
vi.mock("react-router-dom", async (original) => ({
  ...(await original()),
  useParams: () => ({ dashboardId: "date-dashboard" }),
}));
vi.mock("../hooks/useCanEditDashboard", () => ({
  default: () => ({ canUpdate: true, isReadOnly: false }),
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

const server = setupServer();
let client;
let requests;
const widget = {
  id: "date-widget",
  name: "Synthetic latency",
  width: 12,
  position: 0,
  query_config: {
    metrics: [{ type: "system_metric", name: "latency", aggregation: "avg" }],
    time_range: { preset: "7D" },
  },
  chart_config: { chart_type: "metric" },
};

const savedWindow = {
  custom_start: "2026-10-25T23:00:00Z",
  custom_end: "2026-10-26T00:00:00Z",
};
const pinnedWidget = {
  ...widget,
  query_config: {
    metrics: [
      {
        type: "derived_metric",
        name: "Resolution",
        aggregation: "avg",
        derived_run_id: "50e51108-84cd-4485-8ec1-d81e15b1d070",
      },
    ],
    time_range: savedWindow,
  },
};

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  requests = [];
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
  server.use(
    http.post("*/tracer/dashboard/query/", async ({ request }) => {
      const body = await request.json();
      const outsideSavedWindow =
        body.metrics[0].derived_run_id &&
        body.time_range.custom_end !== savedWindow.custom_end;
      requests.push({
        body,
        refresh: new URL(request.url).searchParams.get("refresh"),
      });
      return HttpResponse.json({
        result: {
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
          query_completed_at: "2026-11-01T12:00:00Z",
          metrics: [
            {
              name: "Latency",
              aggregation: "avg",
              query_complete: true,
              query_status: "complete",
              query_sampled: false,
              series: [
                {
                  name: "total",
                  data: outsideSavedWindow
                    ? []
                    : [
                        {
                          timestamp:
                            body.time_range.custom_end ??
                            "2026-11-01T00:00:00Z",
                          value: 42,
                        },
                      ],
                },
              ],
            },
          ],
        },
      });
    }),
  );
});
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  vi.useRealTimers();
});

async function openDashboard(dashboardWidget = widget) {
  client.setQueryData(["dashboards", "detail", "date-dashboard"], {
    data: {
      result: {
        id: "date-dashboard",
        name: "Date selection",
        widgets: [dashboardWidget],
      },
    },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <DashboardDetailView />
    </QueryClientProvider>,
  );
  await waitFor(() => {
    const error = client
      .getMutationCache()
      .getAll()
      .find((mutation) => mutation.state.error)?.state.error;
    if (error) throw error;
    expect(requests).toHaveLength(1);
  });
  await waitFor(() =>
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("42"),
  );
  expect(requests).toHaveLength(1);
  vi.useFakeTimers();
  return view;
}

async function advance(ms) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function setCustomDay() {
  fireEvent.click(screen.getByText("Custom", { exact: true }));
  for (const label of ["Start Date", "End Date"]) {
    fireEvent.change(screen.getByLabelText(label), {
      target: { value: "2026-11-01" },
    });
  }
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
}

const customDay = {
  custom_start: new Date(2026, 10, 1).toISOString(),
  custom_end: new Date(2026, 10, 2).toISOString(),
};

describe("dashboard date request integration", () => {
  it("highlights each preset immediately and sends only the final range after 500 ms", async () => {
    await openDashboard();
    for (const label of ["Today", "7D", "30D"]) {
      fireEvent.click(screen.getByText(label, { exact: true }));
      expect(screen.getByText(label).closest(".MuiChip-root")).toHaveClass(
        "MuiChip-filled",
      );
      await advance(100);
      expect(requests).toHaveLength(1);
      expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent(
        /^42(?:\.0+)?$/,
      );
    }
    await advance(399);
    expect(requests).toHaveLength(1);
    await advance(1);
    expect(requests).toHaveLength(2);
    expect(requests[1].body.time_range).toEqual(
      toTimeRangePayload(resolveGlobalDateRange("30D", null)),
    );
    await advance(500);
    expect(requests).toHaveLength(2);
  });

  it.each([false, true])(
    "restores the saved query window (another preset pending: %s)",
    async (pending) => {
      await openDashboard(pinnedWidget);
      vi.setSystemTime(new Date("2026-11-21T12:00:00Z"));
      expect(requests[0].body.time_range).toEqual(savedWindow);
      fireEvent.click(screen.getByText("Today", { exact: true }));
      await advance(500);
      expect(requests[1].body.time_range).toEqual(
        toTimeRangePayload(resolveGlobalDateRange("today", null)),
      );
      await advance(10);
      const restore = screen.getByRole("button", {
        name: "Restore saved window",
      });
      if (pending) {
        fireEvent.click(screen.getByText("7D", { exact: true }));
        await advance(100);
        expect(requests).toHaveLength(2);
      }
      fireEvent.click(restore);
      await advance(pending ? 600 : 0);
      expect(requests.slice(2).map(({ body }) => body.time_range)).toEqual([
        savedWindow,
      ]);
      expect(screen.getByText("Default").closest(".MuiChip-root")).toHaveClass(
        "MuiChip-filled",
      );
    },
  );

  it("applies custom Done immediately and cancels a pending preset", async () => {
    await openDashboard();
    fireEvent.click(screen.getByText("30D", { exact: true }));
    await advance(100);
    expect(requests).toHaveLength(1);
    setCustomDay();
    await advance(0);
    expect(requests).toHaveLength(2);
    expect(requests[1].body.time_range).toEqual(customDay);
    await advance(500);
    expect(requests).toHaveLength(2);
    fireEvent.click(screen.getByText("Nov 01 - Nov 01", { exact: true }));
    expect(screen.getByLabelText("End Date")).toHaveValue("2026-11-01");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await advance(0);
    expect(requests).toHaveLength(3);
    expect(requests[2].body.time_range).toEqual(customDay);
  });

  it("does not query an intermediate preset when the pending selection returns to Default", async () => {
    await openDashboard();
    fireEvent.click(screen.getByText("30D", { exact: true }));
    await advance(100);
    fireEvent.click(screen.getByText("Default", { exact: true }));
    await advance(500);
    expect(requests).toHaveLength(1);
  });

  it("refreshes immediately without committing a pending preset early", async () => {
    await openDashboard();
    fireEvent.click(screen.getByText("30D", { exact: true }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await advance(0);
    expect(requests).toHaveLength(2);
    expect(requests[1]).toMatchObject({
      body: { time_range: { preset: "7D" } },
      refresh: "true",
    });
    await advance(500);
    expect(requests).toHaveLength(3);
    expect(requests[2].body.time_range).toEqual(
      toTimeRangePayload(resolveGlobalDateRange("30D", null)),
    );
  });

  it("cancels pending presets on unmount and reopens using the saved range", async () => {
    const view = await openDashboard();
    fireEvent.click(screen.getByText("30D", { exact: true }));
    await advance(100);
    expect(requests).toHaveLength(1);
    view.unmount();
    await advance(500);
    expect(requests).toHaveLength(1);
    render(
      <QueryClientProvider client={client}>
        <DashboardDetailView />
      </QueryClientProvider>,
    );
    await advance(0);
    expect(requests).toHaveLength(2);
    expect(requests[1].body.time_range).toEqual({ preset: "7D" });
  });
});
