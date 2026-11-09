import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { render, screen, waitFor, within } from "@testing-library/react";
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
    "/agentcc/analytics/usage-timeseries/": { granularity: "hour", series: [] },
    "/agentcc/analytics/cost-breakdown/": { total_cost: "0", breakdown: [] },
    "/agentcc/analytics/latency-stats/": { summary: {}, timeseries: [] },
    "/agentcc/analytics/error-breakdown/": ERRORS,
    "/agentcc/analytics/model-comparison/": MODELS,
  }[url];
  return Promise.resolve({ data: { status: true, result } });
}

function renderAt(url) {
  return render(
    <HelmetProvider>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ThemeProvider
          theme={createTheme({ palette: palette("light"), typography })}
        >
          <MemoryRouter initialEntries={[url]}>
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
