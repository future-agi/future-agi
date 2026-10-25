/* eslint-disable react/prop-types */
// Monitoring > Live Metrics cards open the Analytics tab they name
// ('Requests (24h)'), and Overview > Getting Started 'Gateway Connected'
// ticks only for a healthy gateway; Check Health shows the real result.
// Real components and the real route tree (GatewayProvider -> GatewayGuard ->
// page). Mocked: data hooks, analytics chart children, icons, the axios
// instance. No network.
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  within,
  cleanup,
  configure,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  MemoryRouter,
  Routes,
  Route,
  Outlet,
  useLocation,
} from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { enqueueSnackbar } from "notistack";

import { GatewayProvider } from "./context/GatewayContext";
import GatewayGuard from "./components/GatewayGuard";
import GatewayOverviewSection from "./GatewayOverviewSection";
import AlertingMonitoringSection from "./monitoring/AlertingMonitoringSection";
import AnalyticsDashboardSection from "./analytics/AnalyticsDashboardSection";

// Gateway config writes are Owner or Admin only (useGatewayAdmin); these
// cases act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

configure({ asyncUtilTimeout: 20000 });

const h = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  overviewArgs: [],
  overviewData: { current: null },
}));

vi.mock("src/utils/axios", () => ({
  default: { get: h.get, post: h.post },
  endpoints: {
    gateway: {
      list: "/agentcc/gateways/",
      healthCheck: (id) => `/agentcc/gateways/${id}/health_check/`,
      analyticsOverview: "/agentcc/analytics/overview/",
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => (
    <span
      data-testid="iconify"
      data-icon={typeof icon === "string" ? icon : ""}
    />
  ),
}));
vi.mock("@iconify/react", () => ({
  Icon: ({ icon }) => <span data-icon={icon} />,
}));
vi.mock("src/components/svg-color", () => ({ default: () => <span /> }));

vi.mock("./analytics/UsageCharts", () => ({
  default: () => <div data-testid="body-usage" />,
}));
vi.mock("./analytics/CostAnalytics", () => ({
  default: () => <div data-testid="body-cost" />,
}));
vi.mock("./analytics/LatencyAnalytics", () => ({
  default: () => <div data-testid="body-latency" />,
}));
vi.mock("./analytics/ErrorAnalytics", () => ({
  default: () => <div data-testid="body-errors" />,
}));
vi.mock("./analytics/ModelComparison", () => ({
  default: () => <div data-testid="body-models" />,
}));
vi.mock("./analytics/OverviewKPIs", () => ({
  default: () => <div data-testid="kpis" />,
}));
vi.mock("./analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: (args) => {
    h.overviewArgs.push(args);
    return { data: h.overviewData.current };
  },
}));
vi.mock("./providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: {}, isLoading: false }),
  useProviderHealth: () => ({ data: undefined }),
  useUpdateConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("./keys/hooks/useApiKeys", () => ({
  useApiKeys: () => ({ data: [] }),
}));
vi.mock("./monitoring/CreateAlertRuleDialog", () => ({ default: () => null }));
vi.mock("./monitoring/CreateChannelDialog", () => ({ default: () => null }));

// What views/gateway.py list() returns: status "healthy" when the gateway
// answered its health check, else "unreachable".
const gatewayRow = (status) => ({
  id: "default",
  name: "Agent Command Center Gateway",
  base_url: "http://localhost:8080/v1",
  status,
  provider_count: 0,
  model_count: 0,
});
const gatewayStatus = { current: "unreachable" };

const theme = createTheme({ palette: palette("light") });
let currentLocation = null;
const LocationProbe = () => {
  currentLocation = useLocation();
  return null;
};

function renderGatewayApp(initialPath) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={[initialPath]}>
          <LocationProbe />
          <Routes>
            <Route path="/dashboard/gateway" element={<GatewayProvider />}>
              <Route
                element={
                  <GatewayGuard>
                    <Outlet />
                  </GatewayGuard>
                }
              >
                <Route index element={<GatewayOverviewSection />} />
                <Route
                  path="monitoring"
                  element={<AlertingMonitoringSection />}
                />
                <Route
                  path="monitoring/:tab"
                  element={<AlertingMonitoringSection />}
                />
                <Route
                  path="analytics"
                  element={<AnalyticsDashboardSection />}
                />
                <Route
                  path="analytics/:tab"
                  element={<AnalyticsDashboardSection />}
                />
              </Route>
            </Route>
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  h.get.mockReset();
  h.post.mockReset();
  vi.mocked(enqueueSnackbar).mockReset();
  h.overviewArgs.length = 0;
  h.overviewData.current = {
    total_requests: 1234,
    error_rate: 2,
    avg_latency_ms: 321,
    total_cost: 1.5,
  };
  gatewayStatus.current = "unreachable";
  currentLocation = null;
  try {
    localStorage.removeItem("agentcc_getting_started_dismissed");
  } catch {
    // noop
  }
  h.get.mockImplementation(async (url) => {
    if (url === "/agentcc/gateways/") {
      return {
        data: { status: true, result: [gatewayRow(gatewayStatus.current)] },
      };
    }
    throw new Error(`unexpected GET ${url}`);
  });
});

afterEach(() => cleanup());

describe("Monitoring > Live Metrics cards open the Analytics tab they name", () => {
  for (const [card, slug] of [
    ["Requests (24h)", "usage"],
    ["Error Rate", "errors"],
    ["Avg Latency", "latency"],
    ["Cost (24h)", "cost"],
  ]) {
    it(`the '${card}' card opens /analytics/${slug} with that tab selected`, async () => {
      renderGatewayApp("/dashboard/gateway/monitoring");
      const button = await screen.findByRole("button", {
        name: new RegExp(card.replace(/[()]/g, "\\$&")),
      });
      await userEvent.click(button);
      await screen.findByTestId(`body-${slug}`);
      expect(currentLocation.pathname).toBe(
        `/dashboard/gateway/analytics/${slug}`,
      );
      expect(currentLocation.search).toBe("");
      const tabName = {
        usage: "Usage",
        errors: "Errors",
        latency: "Latency",
        cost: "Cost",
      }[slug];
      expect(screen.getByRole("tab", { name: tabName })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });
  }

  it("the request card is labelled as the 24 h count it shows, not a rate", async () => {
    renderGatewayApp("/dashboard/gateway/monitoring");
    const card = await screen.findByRole("button", {
      name: /Requests \(24h\)/,
    });
    expect(within(card).getByText("1,234")).toBeInTheDocument();
    expect(screen.queryByText("Request Rate")).not.toBeInTheDocument();
    expect(within(card).queryByText("total")).not.toBeInTheDocument();
    const args = h.overviewArgs[h.overviewArgs.length - 1];
    const spanHours = (new Date(args.end) - new Date(args.start)) / 3600000;
    expect(Math.round(spanHours)).toBe(24);
  });
});

describe("Overview > Getting Started 'Gateway Connected'", () => {
  it("is not ticked while the gateway is unreachable", async () => {
    h.overviewData.current = {
      total_requests: 0,
      total_cost: 0,
      avg_latency_ms: null,
    };
    renderGatewayApp("/dashboard/gateway");
    const stepLabel = await screen.findByText("Gateway Connected");
    expect(screen.getAllByText("unreachable").length).toBeGreaterThan(0);
    const row = stepLabel.parentElement;
    expect(within(row).getByTestId("iconify")).toHaveAttribute(
      "data-icon",
      "mdi:check-network",
    );
    expect(screen.getByText("0/4")).toBeInTheDocument();
  });

  it("is ticked when the gateway is healthy", async () => {
    gatewayStatus.current = "healthy";
    h.overviewData.current = {
      total_requests: 0,
      total_cost: 0,
      avg_latency_ms: null,
    };
    renderGatewayApp("/dashboard/gateway");
    const stepLabel = await screen.findByText("Gateway Connected");
    const row = stepLabel.parentElement;
    expect(within(row).getByTestId("iconify")).toHaveAttribute(
      "data-icon",
      "mdi:check",
    );
    expect(screen.getByText("1/4")).toBeInTheDocument();
  });

  it("an unreachable gateway offers Check Health, which reports the real failure", async () => {
    // views/gateway.py health_check answers 400 with result
    // {status: "unreachable", error}; src/utils/axios rejects with the body.
    h.post.mockRejectedValue({
      status: false,
      statusCode: 400,
      message: "connection refused",
      error: "connection refused",
      result: { status: "unreachable", error: "connection refused" },
    });
    renderGatewayApp("/dashboard/gateway");
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/not reachable/i);
    await userEvent.click(
      within(banner).getByRole("button", { name: /Check Health/ }),
    );
    expect(h.post).toHaveBeenCalledWith(
      "/agentcc/gateways/default/health_check/",
      {},
    );
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Gateway unreachable: connection refused",
        { variant: "error" },
      ),
    );
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ variant: "success" }),
    );
  });

  it("Check Health on a healthy gateway says it is healthy", async () => {
    gatewayStatus.current = "healthy";
    h.post.mockResolvedValue({
      data: {
        status: true,
        result: { status: "healthy", provider_count: 2, model_count: 5 },
      },
    });
    renderGatewayApp("/dashboard/gateway");
    expect(await screen.findByText("Gateway Connected")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // The button's name is its tooltip, which says what the check does.
    await userEvent.click(
      screen.getByRole("button", { name: /can reach the gateway/ }),
    );
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith("Gateway healthy", {
        variant: "success",
      }),
    );
  });
});
