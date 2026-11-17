import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import AnalyticsDashboardSection from "../AnalyticsDashboardSection";
import { ANALYTICS_GRANULARITIES, rangeWindow } from "../analyticsRange";
import { grid } from "../../logs/__tests__/fakeAgGrid";
import {
  cutHeaders,
  gridBudget1440,
  minimumGridWidth,
} from "../../logs/__tests__/gridFit";

vi.mock("ag-grid-react", () => import("../../logs/__tests__/fakeAgGrid"));
vi.mock("react-apexcharts", () => ({
  default: (props) => <div data-testid={`chart-${props.type}`} />,
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));

const { mockGet, gatewayReport } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  gatewayReport: { data: {} },
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => gatewayReport,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => mockGet(...args) },
  endpoints: {
    gateway: {
      analyticsOverview: "/agentcc/analytics/overview/",
      analyticsUsage: "/agentcc/analytics/usage-timeseries/",
      analyticsCost: "/agentcc/analytics/cost-breakdown/",
      analyticsLatency: "/agentcc/analytics/latency-stats/",
      analyticsErrors: "/agentcc/analytics/error-breakdown/",
      analyticsModels: "/agentcc/analytics/model-comparison/",
    },
  },
}));

// The local demo stack's answers (2026-10-20), trimmed.
const OVERVIEW = {
  total_requests: { value: 603, trend: null },
  total_tokens: { value: 11708, trend: null },
  total_cost: { value: "0.087701", trend: null },
  avg_latency_ms: { value: 114.0, trend: null },
  error_rate: { value: 12.6, trend: null },
  cache_hit_rate: { value: 42.45, trend: null },
  p95_latency_ms: { value: 837.0, trend: null },
  active_models: { value: 19, trend: null },
};
const MODELS = {
  models: [
    {
      model: "gpt-4o-mini",
      provider: "openai",
      request_count: 289,
      total_tokens: 2758,
      total_cost: "0.006646",
      avg_cost_per_request: "0.000023",
      avg_latency_ms: 13.1,
      p95_latency_ms: 59.0,
      error_rate: 14.53,
      cache_hit_rate: 52.94,
      tokens_per_second: 728.3,
    },
    {
      model: "gpt-4o",
      provider: "flaky-upstream",
      request_count: 160,
      total_tokens: 5799,
      total_cost: "0.048327",
      avg_cost_per_request: "0.000302",
      avg_latency_ms: 251.53,
      p95_latency_ms: 1502.0,
      error_rate: 7.5,
      cache_hit_rate: 53.75,
      tokens_per_second: 144.1,
    },
  ],
};
const ERRORS = {
  group_by: "status_code",
  total_requests: 603,
  total_errors: 76,
  overall_error_rate: 12.6,
  breakdown: [
    {
      name: "403",
      error_count: 42,
      percentage_of_errors: 55.26,
      error_rate: 6.97,
      last_seen: "2026-10-20T09:30:45.621Z",
      sample_error_message: "Request blocked by guardrail: keyword-blocklist",
    },
  ],
  error_timeseries: [],
};

function answer(url) {
  const result = {
    "/agentcc/analytics/overview/": OVERVIEW,
    "/agentcc/analytics/usage-timeseries/": {
      granularity: "hour",
      series: [],
    },
    "/agentcc/analytics/cost-breakdown/": { total_cost: "0", breakdown: [] },
    "/agentcc/analytics/latency-stats/": { summary: {}, timeseries: [] },
    "/agentcc/analytics/error-breakdown/": ERRORS,
    "/agentcc/analytics/model-comparison/": MODELS,
  }[url];
  return Promise.resolve({ data: { status: true, result } });
}

function HistoryControls() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output aria-label="Current URL">
        {location.pathname}
        {location.search}
      </output>
      <button onClick={() => navigate(-1)}>Back</button>
      <button onClick={() => navigate(1)}>Forward</button>
    </>
  );
}

function renderAt(
  url,
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  return render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <ThemeProvider
          theme={createTheme({ palette: palette("light"), typography })}
        >
          <MemoryRouter initialEntries={[url]}>
            <HistoryControls />
            <Routes>
              <Route
                path="/dashboard/gateway/analytics"
                element={<AnalyticsDashboardSection />}
              />
              <Route
                path="/dashboard/gateway/analytics/:tab"
                element={<AnalyticsDashboardSection />}
              />
            </Routes>
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>,
  );
}

const callsTo = (url) =>
  mockGet.mock.calls
    .filter(([u]) => u === url)
    .map(([, cfg]) => cfg?.params || {});

beforeEach(() => {
  gatewayReport.data = {};
  mockGet.mockReset();
  mockGet.mockImplementation(answer);
});

describe("analytics range", () => {
  it("applies a fixed custom interval without losing Cost grouping, and refresh keeps it fixed", async () => {
    const user = userEvent.setup();
    renderAt("/dashboard/gateway/analytics/cost?preset=24h&cost_group=user_id");
    await user.click(screen.getByRole("button", { name: "Custom interval" }));
    fireEvent.change(screen.getByLabelText("From"), {
      target: { value: "2026-11-02T09:12" },
    });
    fireEvent.change(screen.getByLabelText("To"), {
      target: { value: "2026-11-02T09:28" },
    });
    await user.click(screen.getByRole("button", { name: "Apply interval" }));
    const current = () =>
      new URL(
        screen.getByLabelText("Current URL").textContent,
        "https://console.test",
      );
    await waitFor(() =>
      expect(current().searchParams.has("preset")).toBe(false),
    );
    expect(current().searchParams.get("start")).toBe(
      new Date("2026-11-02T09:12").toISOString(),
    );
    expect(current().searchParams.get("end")).toBe(
      new Date("2026-11-02T09:28").toISOString(),
    );
    expect(current().searchParams.get("cost_group")).toBe("user_id");
    const fixed = current().search;
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(current().search).toBe(fixed);
  });

  it("asks only for bucket sizes the API has, a few hundred points at most", () => {
    const now = new Date("2026-10-20T12:00:00Z");
    const expected = {
      "1h": "minute",
      "6h": "minute",
      "24h": "hour",
      "7d": "hour",
      "30d": "day",
      "90d": "day",
    };
    Object.entries(expected).forEach(([preset, granularity]) => {
      const w = rangeWindow(preset, now);
      expect(w.granularity).toBe(granularity);
      expect(ANALYTICS_GRANULARITIES).toContain(w.granularity);
      expect(w.end).toBe(now.toISOString());
      const span = Date.parse(w.end) - Date.parse(w.start);
      const step = { minute: 6e4, hour: 36e5, day: 864e5 }[granularity];
      expect(span / step).toBeLessThanOrEqual(400);
    });
  });
});

describe("Analytics page", () => {
  it("is on the gateway page frame and reads each overview metric's value", async () => {
    renderAt("/dashboard/gateway/analytics");
    expect(
      screen.getByRole("heading", { level: 1, name: "Analytics" }),
    ).toBeInTheDocument();
    const kpis = within(
      await screen.findByRole("list", { name: "Key metrics" }),
    );
    await waitFor(() => expect(kpis.getByText("603")).toBeInTheDocument());
    expect(kpis.getByText("11,708")).toBeInTheDocument();
    expect(kpis.getByText("$0.087701")).toBeInTheDocument();
    expect(kpis.getByText("114 ms")).toBeInTheDocument();
    expect(kpis.getByText("837 ms")).toBeInTheDocument();
    expect(kpis.getByText("12.6%")).toBeInTheDocument();
    expect(kpis.getByText("42.5%")).toBeInTheDocument();
    expect(kpis.getByText("19")).toBeInTheDocument();
    expect(screen.queryByText("--")).not.toBeInTheDocument();
  });

  // The charts plot the viewer's zone and say so, as Logs does.
  it("names the time zone its charts plot", () => {
    renderAt("/dashboard/gateway/analytics");
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    expect(
      screen.getByText(new RegExp(`Times are in ${zone}\\.`)),
    ).toBeInTheDocument();
  });

  it("asks the overview only for parameters its contract has (no gateway_id)", async () => {
    renderAt("/dashboard/gateway/analytics");
    await waitFor(() =>
      expect(callsTo("/agentcc/analytics/overview/").length).toBeGreaterThan(0),
    );
    callsTo("/agentcc/analytics/overview/").forEach((params) => {
      expect(Object.keys(params).sort()).toEqual(["end", "start"]);
    });
  });

  it("sends the chosen range's bucket size to the charts", async () => {
    const user = userEvent.setup();
    renderAt("/dashboard/gateway/analytics");
    await waitFor(() =>
      expect(
        callsTo("/agentcc/analytics/usage-timeseries/").length,
      ).toBeGreaterThan(0),
    );
    expect(
      callsTo("/agentcc/analytics/usage-timeseries/").at(-1).granularity,
    ).toBe("hour");
    await user.click(screen.getByRole("button", { name: "30D" }));
    await waitFor(() =>
      expect(
        callsTo("/agentcc/analytics/usage-timeseries/").at(-1).granularity,
      ).toBe("day"),
    );
    await user.click(screen.getByRole("button", { name: "1H" }));
    await waitFor(() =>
      expect(
        callsTo("/agentcc/analytics/usage-timeseries/").at(-1).granularity,
      ).toBe("minute"),
    );
    mockGet.mock.calls.forEach(([, cfg]) => {
      if (cfg?.params?.granularity) {
        expect(ANALYTICS_GRANULARITIES).toContain(cfg.params.granularity);
      }
    });
  });

  it("shows an error with Retry when the overview cannot be read", async () => {
    const user = userEvent.setup();
    mockGet.mockImplementation((url) =>
      url === "/agentcc/analytics/overview/"
        ? Promise.reject({ message: "Gateway API unreachable" })
        : answer(url),
    );
    renderAt("/dashboard/gateway/analytics");
    expect(
      await screen.findByText("Analytics could not be loaded"),
    ).toBeInTheDocument();
    mockGet.mockImplementation(answer);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    const kpis = within(
      await screen.findByRole("list", { name: "Key metrics" }),
    );
    expect(await kpis.findByText("603")).toBeInTheDocument();
  });

  it("compares models in a sortable grid", async () => {
    renderAt("/dashboard/gateway/analytics/models");
    const table = within(await screen.findByTestId("ag-grid"));
    expect(await table.findByText("gpt-4o-mini")).toBeInTheDocument();
    const rows = table.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("openai");
    expect(rows[0]).toHaveTextContent("289");
    expect(rows[0]).toHaveTextContent("14.5%");
    expect(rows[1]).toHaveTextContent("1.50 s");
    expect(grid.props.defaultColDef).toMatchObject({
      sortable: true,
      resizable: true,
    });
    expect(
      grid.props.columnDefs.find((c) => c.field === "request_count").sort,
    ).toBe("desc");
  });

  it.each([
    { cache: { enabled: false } },
    { org_sections_enforced: [] },
    {
      org_sections_enforced: ["cache"],
      org_sections_not_enforced: { cache: "disabled" },
    },
  ])(
    "917 r2: keeps the Models cache column and historical rates while off: %j",
    async (data) => {
      gatewayReport.data = data;
      renderAt("/dashboard/gateway/analytics/models");
      const table = within(await screen.findByTestId("ag-grid"));
      await table.findByText("gpt-4o-mini");
      expect(
        table.getByRole("columnheader", { name: /Cache hits/ }),
      ).toBeInTheDocument();
      expect(table.getByText("52.9%")).toBeInTheDocument();
      expect(table.getByText("53.8%")).toBeInTheDocument();
      const cache = grid.props.columnDefs.find(
        (column) => column.field === "cache_hit_rate",
      );
      expect(cache.headerTooltip).toBe(
        "Response caching is off on this gateway. Cache hits reflect the selected period.",
      );
      expect(cache.valueFormatter({ value: null })).toBe("—");
      expect(cache.valueFormatter({ value: 0 })).toBe("0.0%");
      expect(cache.valueGetter({ data: { cache_hit_rate: "52.94" } })).toBe(
        52.94,
      );
      expect(cache.comparator(52.94, 53.75)).toBeLessThan(0);
    },
  );

  it.each([{}, { org_sections_enforced: ["cache"] }])(
    "does not claim caching is off for an unknown or active report: %j",
    async (data) => {
      gatewayReport.data = data;
      renderAt("/dashboard/gateway/analytics/models");
      await within(await screen.findByTestId("ag-grid")).findByText(
        "gpt-4o-mini",
      );
      const cache = grid.props.columnDefs.find(
        (column) => column.field === "cache_hit_rate",
      );
      expect(cache).toBeDefined();
      expect(cache.headerTooltip).toBeUndefined();
    },
  );

  it("lists the top errors in a grid with a sample message", async () => {
    renderAt("/dashboard/gateway/analytics/errors");
    const table = within(await screen.findByTestId("ag-grid"));
    expect(await table.findByText("403")).toBeInTheDocument();
    expect(table.getByText(/keyword-blocklist/)).toBeInTheDocument();
  });
});

const TAB_READS = [
  {
    tab: "usage",
    title: "Usage",
    endpoint: "usage-timeseries",
    query: "usage",
    data: {
      series: [
        {
          bucket: "2026-10-20T00:00:00Z",
          request_count: 7,
          total_tokens: 70,
        },
      ],
    },
    chart: "chart-area",
    empty: "No requests in this range.",
  },
  {
    tab: "cost",
    title: "Cost",
    endpoint: "cost-breakdown",
    query: "cost",
    data: {
      total_cost: "12",
      breakdown: [{ name: "test-model", total_cost: "12", request_count: 7 }],
    },
    chart: "chart-bar",
    empty: "No cost data available for this time range.",
  },
  {
    tab: "latency",
    title: "Latency",
    endpoint: "latency-stats",
    query: "latency",
    data: {
      summary: { total_requests: 7, p50_ms: 150 },
      timeseries: [{ bucket: "2026-10-20T00:00:00Z", p50_ms: 150 }],
    },
    chart: "chart-line",
    empty: "No requests in this range.",
  },
  {
    tab: "errors",
    title: "Errors",
    endpoint: "error-breakdown",
    query: "errors",
    data: { ...ERRORS, breakdown: [{ ...ERRORS.breakdown[0], name: "429" }] },
    chart: "chart-bar",
    empty: "No errors in this range.",
  },
];
const success = (result) => Promise.resolve({ data: { status: true, result } });

describe("Independent analytics recovery", () => {
  it.each(TAB_READS)(
    "$title fails visibly, retries, and then displays its data",
    async ({ tab, title, endpoint, data, chart, empty }) => {
      const user = userEvent.setup();
      const path = `/agentcc/analytics/${endpoint}/`;
      mockGet.mockImplementation((url) =>
        url === path ? Promise.reject(new Error("Read failed")) : answer(url),
      );
      renderAt(`/dashboard/gateway/analytics/${tab}`);
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(`${title} could not be loaded`);
      expect(screen.queryByText(empty)).not.toBeInTheDocument();
      expect(screen.queryByText(/spent in this range/)).not.toBeInTheDocument();
      expect(screen.queryByText("0 errors")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("list", { name: "Latency summary" }),
      ).not.toBeInTheDocument();
      expect(
        within(screen.getByRole("list", { name: "Key metrics" })).getByText(
          "603",
        ),
      ).toBeInTheDocument();
      mockGet.mockImplementation((url) =>
        url === path ? success(data) : answer(url),
      );
      await user.click(within(alert).getByRole("button", { name: "Retry" }));
      await waitFor(() =>
        expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
      );
      expect(screen.getAllByTestId(chart).length).toBeGreaterThan(0);
    },
  );

  it.each(TAB_READS)(
    "$title keeps same-range data with a stale warning after a failed refresh",
    async ({ tab, title, endpoint, query, data, chart }) => {
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const path = `/agentcc/analytics/${endpoint}/`;
      mockGet.mockImplementation((url) =>
        url === path ? success(data) : answer(url),
      );
      renderAt(`/dashboard/gateway/analytics/${tab}`, client);
      await screen.findAllByTestId(chart);
      mockGet.mockImplementation((url) =>
        url === path
          ? Promise.reject(new Error("Refresh failed"))
          : answer(url),
      );
      await act(async () => {
        await client.refetchQueries({ queryKey: ["analytics", query] });
      });
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(`${title} could not be refreshed`);
      expect(alert).toHaveTextContent(
        "Showing stale data from the last successful load.",
      );
      expect(screen.getAllByTestId(chart).length).toBeGreaterThan(0);
      mockGet.mockImplementation((url) =>
        url === path ? success(data) : answer(url),
      );
      await userEvent
        .setup()
        .click(within(alert).getByRole("button", { name: "Retry" }));
      await waitFor(() =>
        expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
      );
    },
  );

  it.each(TAB_READS)(
    "$title distinguishes a successful empty response from a failure",
    async ({ tab, endpoint, empty }) => {
      mockGet.mockImplementation((url) =>
        url === `/agentcc/analytics/${endpoint}/`
          ? success({
              series: [],
              timeseries: [],
              breakdown: [],
              error_timeseries: [],
              total_errors: 0,
              total_cost: "0",
              summary: { total_requests: 0 },
            })
          : answer(url),
      );
      renderAt(`/dashboard/gateway/analytics/${tab}`);
      await screen.findAllByText(empty);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Retry" }),
      ).not.toBeInTheDocument();
    },
  );

  it("uses the displayed error range and 429 filter in a native request link", async () => {
    mockGet.mockImplementation((url) =>
      url === "/agentcc/analytics/error-breakdown/"
        ? success(TAB_READS[3].data)
        : answer(url),
    );
    renderAt("/dashboard/gateway/analytics/errors");
    const link = await screen.findByRole("link", {
      name: "View requests for 429",
    });
    const url = new URL(link.href);
    const { start, end } = callsTo("/agentcc/analytics/error-breakdown/").at(
      -1,
    );
    expect(url.pathname).toBe("/dashboard/gateway/logs");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      started_after: start,
      started_before: end,
      gateway_id: "gw-1",
      is_error: "true",
      status_code: "429",
    });
    const before = mockGet.mock.calls.length;
    link.focus();
    expect(link).toHaveFocus();
    link.dispatchEvent(
      new MouseEvent("click", {
        bubbles: true,
        cancelable: true,
        ctrlKey: true,
      }),
    );
    expect(mockGet.mock.calls).toHaveLength(before);
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const currentURL = () =>
  new URL(
    screen.getByLabelText("Current URL").textContent,
    window.location.origin,
  );
const overviewPath = "/agentcc/analytics/overview/";
const usagePath = "/agentcc/analytics/usage-timeseries/";

describe("Shared investigation window", () => {
  it("copies 7D/provider grouping into a fresh router and preserves it through tabs and Back/Forward", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-09T12:00:00Z"));
    const user = userEvent.setup();
    const clipboard = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockResolvedValue();
    const first = renderAt("/dashboard/gateway/analytics");
    await screen.findByText("603");
    await user.click(screen.getByRole("button", { name: "7D" }));
    await user.click(screen.getByRole("button", { name: "Provider" }));
    await waitFor(() =>
      expect(callsTo(usagePath).at(-1).group_by).toBe("provider"),
    );
    const windowParams = {
      start: currentURL().searchParams.get("start"),
      end: currentURL().searchParams.get("end"),
    };
    expect(callsTo(overviewPath).at(-1)).toEqual(windowParams);
    await user.click(screen.getByRole("button", { name: "Copy view link" }));
    const copied = clipboard.mock.calls.at(-1)[0];
    expect(new URL(copied).searchParams.get("usage_group")).toBe("provider");
    await user.click(screen.getByRole("tab", { name: "Cost" }));
    expect(currentURL().searchParams.get("start")).toBe(windowParams.start);
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("button", { name: "Provider" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "Forward" }));
    expect(currentURL().pathname).toMatch(/cost$/);
    first.unmount();
    vi.setSystemTime(new Date("2026-11-09T13:00:00Z"));
    mockGet.mockClear();
    renderAt(new URL(copied).pathname + new URL(copied).search);
    await waitFor(() =>
      expect(callsTo(usagePath).at(-1)).toMatchObject({
        ...windowParams,
        group_by: "provider",
      }),
    );
    expect(callsTo(overviewPath)).toEqual([windowParams]);
  });

  it("Refresh advances one rolling window shared by the URL, overview, active panel and Logs link", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-09T12:00:00Z"));
    const user = userEvent.setup();
    renderAt("/dashboard/gateway/analytics/errors?preset=7d");
    await screen.findByRole("link", { name: "View requests for 403" });
    mockGet.mockClear();
    vi.setSystemTime(new Date("2026-11-09T12:10:00Z"));
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByRole("link", { name: "View requests for 403" });
    const bounds = {
      start: "2026-11-02T12:10:00.000Z",
      end: "2026-11-09T12:10:00.000Z",
    };
    await waitFor(() => expect(callsTo(overviewPath)).toEqual([bounds]));
    expect(callsTo("/agentcc/analytics/error-breakdown/")).toHaveLength(1);
    expect(callsTo("/agentcc/analytics/error-breakdown/")[0]).toMatchObject(
      bounds,
    );
    expect(currentURL().searchParams.get("end")).toBe(bounds.end);
    const link = new URL(
      screen.getByRole("link", { name: "View requests for 403" }).href,
    );
    expect(link.searchParams.get("started_after")).toBe(bounds.start);
    expect(link.searchParams.get("started_before")).toBe(bounds.end);
  });

  it("snapshot Refresh retains bounds and a failed read never advances the overview successful-read time", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const before = new Date("2026-11-09T12:00:00Z");
    vi.setSystemTime(before);
    const user = userEvent.setup();
    renderAt(
      "/dashboard/gateway/analytics?start=2026-11-01T00:00:00Z&end=2026-11-02T00:00:00Z",
    );
    await screen.findByText("603");
    const label = screen.getByLabelText("Overview freshness").textContent;
    expect(label).toContain(before.toISOString());
    expect(label).toMatch(/Panels load independently/);
    const bounds = callsTo(overviewPath).at(-1);
    vi.setSystemTime(new Date(before.getTime() + 600000));
    mockGet.mockClear();
    mockGet.mockRejectedValue(new Error("Refresh failed"));
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("Usage could not be refreshed");
    expect(screen.getByLabelText("Overview freshness")).toHaveTextContent(
      before.toISOString(),
    );
    expect(screen.getByLabelText("Overview freshness")).not.toHaveTextContent(
      "12:10:00",
    );
    expect(callsTo(overviewPath)).toEqual([bounds]);
    expect(callsTo(usagePath)).toHaveLength(1);
    expect(callsTo(usagePath)[0]).toMatchObject(bounds);
    expect(
      screen.getByText(/Overview could not be refreshed/),
    ).toBeInTheDocument();
  });

  it("malformed URLs canonicalize once without invalid API parameters", async () => {
    renderAt(
      "/dashboard/gateway/analytics?start=broken&end=bad&preset=unknown&usage_group=private",
    );
    await screen.findByText("603");
    expect(callsTo(overviewPath)).toHaveLength(1);
    expect(callsTo(usagePath)).toHaveLength(1);
    for (const field of ["start", "end"]) {
      expect(Number.isFinite(Date.parse(callsTo(overviewPath)[0][field]))).toBe(
        true,
      );
      expect(currentURL().searchParams.get(field)).toBe(
        callsTo(overviewPath)[0][field],
      );
    }
    expect(callsTo(usagePath)[0].group_by).toBeUndefined();
  });
});

describe("Analytics grid widths", () => {
  // Eleven measures side by side: the models grid keeps Model pinned and
  // scrolls sideways at 1440 rather than squeeze them; no header is cut.
  it("cuts off no header in the models grid", async () => {
    renderAt("/dashboard/gateway/analytics/models");
    await within(await screen.findByTestId("ag-grid")).findByText(
      "gpt-4o-mini",
    );
    expect(cutHeaders(grid.props)).toEqual([]);
  });

  it("cuts off no header in the top errors grid, which fits at 1440 px", async () => {
    renderAt("/dashboard/gateway/analytics/errors");
    await within(await screen.findByTestId("ag-grid")).findByText("403");
    expect(cutHeaders(grid.props)).toEqual([]);
    expect(minimumGridWidth(grid.props)).toBeLessThanOrEqual(
      gridBudget1440("analytics"),
    );
  });
});
