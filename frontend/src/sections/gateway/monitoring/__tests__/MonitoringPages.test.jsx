import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { ThemeProvider } from "@mui/material/styles";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import AlertingMonitoringSection from "../AlertingMonitoringSection";
import CreateAlertRuleDialog from "../CreateAlertRuleDialog";
import { grid } from "../../logs/__tests__/fakeAgGrid";
import {
  cutHeaders,
  gridBudget1440,
  minimumGridWidth,
} from "../../logs/__tests__/gridFit";
import { faTestTheme } from "../../__tests__/faTheme";

vi.mock("ag-grid-react", () => import("../../logs/__tests__/fakeAgGrid"));

// Alert rules and channels are Owner or Admin writes (useGatewayAdmin); these
// cases act as an Admin. AlertingMonitoringSection.test covers the others.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const { mutate, snack, state } = vi.hoisted(() => ({
  mutate: vi.fn(),
  snack: vi.fn(),
  state: {
    overview: null,
    overviewError: null,
    overviewRefetch: vi.fn(),
    providerError: null,
    providerLoading: false,
    providerRefetch: vi.fn(),
    config: null,
    configError: null,
    configOptions: null,
    refetch: vi.fn(),
    providers: null,
    managedOffered: true,
  },
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => snack(...args),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    isLoading: false,
    managedOffered: state.managedOffered,
  }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: (_id, options) => {
    state.configOptions = options;
    return {
      data: state.config,
      isLoading: false,
      error: state.configError,
      refetch: state.refetch,
    };
  },
  useProviderHealth: () => ({
    data: state.providers,
    isLoading: state.providerLoading,
    error: state.providerError,
    refetch: state.providerRefetch,
  }),
  useUpdateConfig: () => ({
    mutate,
    isPending: false,
    isError: false,
    error: null,
  }),
}));
vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: (args) => {
    state.overviewArgs = args;
    return {
      data: state.overview,
      isLoading: false,
      error: state.overviewError,
      refetch: state.overviewRefetch,
      dataUpdatedAt: state.overviewUpdatedAt,
    };
  },
}));

// The local demo stack's answers (2026-10-20), trimmed.
const OVERVIEW = {
  total_requests: { value: 603, trend: null },
  total_cost: { value: "0.087701", trend: null },
  avg_latency_ms: { value: 114.0, trend: null },
  p95_latency_ms: { value: 837.0, trend: null },
  error_rate: { value: 12.6, trend: null },
  cache_hit_rate: { value: 42.45, trend: null },
};
const PROVIDERS = {
  providers: [
    {
      id: "flaky-upstream",
      name: "flaky-upstream",
      display_name: "Flaky upstream",
      status: "healthy",
      healthy: true,
      models: ["gpt-4o", "o1-mini"],
      request_count: 162,
      avg_latency: 334.16,
      error_rate: 20.37,
    },
    {
      id: "openai",
      name: "openai",
      display_name: "OpenAI",
      status: "healthy",
      healthy: true,
      models: ["gpt-4o-mini"],
      request_count: 391,
      avg_latency: 33.22,
      error_rate: 11.0,
    },
  ],
};
const CONFIG = {
  alerting: {
    enabled: true,
    rules: [
      {
        name: "high-error-rate",
        metric: "error_count",
        window: "5m",
        channels: ["ops-webhook"],
        cooldown: "15m",
        condition: ">=",
        threshold: 5,
        severity: "critical",
      },
      {
        name: "spend-spike",
        metric: "cost_total",
        window: "1h",
        channels: ["ops-webhook"],
        cooldown: "1h",
        condition: ">",
        threshold: 5,
        enabled: false,
      },
    ],
    channels: [
      {
        url: "http://hooks.uidemo.test:8080/hooks/alerts",
        name: "ops-webhook",
        type: "webhook",
        severity_filter: "critical",
      },
      {
        url: "https://hooks.slack.test/T0/B0/secret",
        name: "oncall",
        type: "slack",
      },
    ],
  },
};

function renderAt(url) {
  return render(
    <HelmetProvider>
      <ThemeProvider theme={faTestTheme("light")}>
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route
              path="/dashboard/gateway/monitoring"
              element={<AlertingMonitoringSection />}
            />
            <Route
              path="/dashboard/gateway/monitoring/:tab"
              element={<AlertingMonitoringSection />}
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </HelmetProvider>,
  );
}

beforeEach(() => {
  mutate.mockReset();
  snack.mockReset();
  state.overview = OVERVIEW;
  state.overviewUpdatedAt = Date.parse("2026-11-09T12:00:00Z");
  state.overviewError = null;
  state.overviewRefetch.mockReset();
  state.providerError = null;
  state.providerLoading = false;
  state.providerRefetch.mockReset();
  state.config = CONFIG;
  state.configError = null;
  state.refetch.mockReset();
  state.providers = PROVIDERS;
  state.managedOffered = true;
});

afterEach(() => vi.useRealTimers());

describe("Monitoring", () => {
  it("Refresh deliberately advances the overview range while labeling provider reads independently", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-09T12:00:00Z"));
    renderAt("/dashboard/gateway/monitoring");
    expect(state.overviewArgs.end).toBe("2026-11-09T12:00:00.000Z");
    expect(screen.getByLabelText("Overview freshness")).toHaveTextContent(
      "2026-11-09T12:00:00.000Z",
    );
    vi.setSystemTime(new Date("2026-11-09T12:10:00Z"));
    state.overviewError = new Error("Refresh failed");
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(state.overviewArgs).toMatchObject({
      start: "2026-11-08T12:10:00.000Z",
      end: "2026-11-09T12:10:00.000Z",
    });
    expect(screen.getByLabelText("Overview freshness")).not.toHaveTextContent(
      "12:10:00",
    );
    expect(screen.getByLabelText("Overview freshness")).toHaveTextContent(
      "12:00:00",
    );
    expect(
      screen.getByText(/Provider reads refresh independently/),
    ).toBeInTheDocument();
    expect(state.providerRefetch).toHaveBeenCalledOnce();
  });

  it.each([undefined, { providers: [] }, PROVIDERS])(
    "keeps overview values visible and retries only a failed provider read (cached: %j)",
    async (cachedProviders) => {
      state.providers = cachedProviders;
      state.providerError = Object.assign(
        new Error("Request failed with status code 500"),
        {
          response: { status: 500 },
        },
      );
      renderAt("/dashboard/gateway/monitoring");
      const metrics = within(
        screen.getByRole("list", { name: "Last 24 hours" }),
      );
      expect(
        metrics.getByRole("link", { name: /Requests\s*603/ }),
      ).toBeInTheDocument();
      expect(
        metrics.getByRole("link", { name: /Error rate\s*12.6%/ }),
      ).toBeInTheDocument();
      expect(
        metrics.getByRole("link", { name: /P95 latency\s*837 ms/ }),
      ).toBeInTheDocument();
      expect(
        metrics.getByRole("link", { name: /Cost\s*\$0\.087701$/ }),
      ).toBeInTheDocument();
      expect(
        screen.getByText("Providers could not be loaded"),
      ).toBeInTheDocument();
      expect(screen.queryByText("No providers yet")).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(state.providerRefetch).toHaveBeenCalledOnce();
      expect(state.overviewRefetch).not.toHaveBeenCalled();
      expect(state.refetch).not.toHaveBeenCalled();
      expect(mutate).not.toHaveBeenCalled();
    },
  );

  it("does not offer provider setup while the provider read is loading", () => {
    state.providers = undefined;
    state.providerLoading = true;
    renderAt("/dashboard/gateway/monitoring");
    expect(screen.queryByText("No providers yet")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Providers could not be loaded"),
    ).not.toBeInTheDocument();
    expect(grid.props.loading).toBe(true);
  });

  it("offers provider setup after a successful empty provider read", () => {
    state.providers = { providers: [] };
    renderAt("/dashboard/gateway/monitoring");
    expect(screen.getByText("No providers yet")).toBeInTheDocument();
    expect(
      screen.queryByText("Providers could not be loaded"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry" }),
    ).not.toBeInTheDocument();
  });

  it("retries a failed overview without reloading successful providers", async () => {
    state.overview = undefined;
    state.overviewError = new Error("Overview read failed");
    renderAt("/dashboard/gateway/monitoring");
    expect(screen.getByText("Flaky upstream")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(state.overviewRefetch).toHaveBeenCalledOnce();
    expect(state.providerRefetch).not.toHaveBeenCalled();
  });

  it("shows the last 24 hours with each number opening its Analytics tab", () => {
    renderAt("/dashboard/gateway/monitoring");
    expect(
      screen.getByRole("heading", { level: 1, name: "Monitoring" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Request Rate/i)).not.toBeInTheDocument();
    const metrics = within(screen.getByRole("list", { name: "Last 24 hours" }));
    const href = (name) =>
      metrics.getByRole("link", { name }).getAttribute("href");
    for (const [name, tab] of [
      [/Requests\s*603/, "usage"],
      [/Error rate\s*12.6%/, "errors"],
      [/P95 latency\s*837 ms/, "latency"],
      [/Cost\s*\$0\.087701$/, "cost"],
    ]) {
      const link = new URL(href(name), window.location.origin);
      expect(link.pathname).toBe(`/dashboard/gateway/analytics/${tab}`);
      expect(link.searchParams.get("start")).toBe(state.overviewArgs.start);
      expect(link.searchParams.get("end")).toBe(state.overviewArgs.end);
      expect(link.searchParams.has("preset")).toBe(false);
    }
  });

  it("lists providers by what was measured, with no health verdict the gateway does not compute", () => {
    renderAt("/dashboard/gateway/monitoring");
    const table = within(screen.getByTestId("ag-grid"));
    const rows = table.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Flaky upstream");
    expect(rows[0]).toHaveTextContent("162");
    expect(rows[0]).toHaveTextContent("20.4%");
    expect(rows[0]).toHaveTextContent("334 ms");
    expect(screen.queryByText(/healthy/i)).not.toBeInTheDocument();
  });

  // The table lists the organization's own
  // provider keys only (GatewayViewSet.providers joins request stats onto
  // AgentccProviderCredential rows), while the totals above count every
  // request. Unscoped, it read 0 requests on a gateway whose traffic was all
  // on managed credit.
  it("names no managed credit on a deployment that offers no managed models", () => {
    state.managedOffered = false;
    renderAt("/dashboard/gateway/monitoring");
    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "Your own provider keys",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Measured by the gateway over the last 24 hours."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/managed credit/i)).toBeNull();
  });

  it("scopes the providers table to your own provider keys, and says where managed requests count", () => {
    renderAt("/dashboard/gateway/monitoring");
    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "Your own provider keys",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Measured by the gateway over the last 24 hours. Requests on managed credit count in the totals above.",
      ),
    ).toBeInTheDocument();
  });

  // With no requests in the last 24 hours the latency and
  // error-rate figures were never measured; they read "—", not "0 ms" and
  // "0.0%". The request count and the spend stay a true 0.
  it("reads — for latency and error rate when no request was served", () => {
    state.overview = {
      total_requests: { value: 0, trend: -100 },
      total_cost: { value: "0", trend: -100 },
      avg_latency_ms: { value: 0, trend: -100 },
      p95_latency_ms: { value: 0, trend: -100 },
      error_rate: { value: 0, trend: -100 },
      cache_hit_rate: { value: 0, trend: -100 },
    };
    renderAt("/dashboard/gateway/monitoring");
    const metrics = within(screen.getByRole("list", { name: "Last 24 hours" }));
    const read = (name) => metrics.getByRole("link", { name }).textContent;
    expect(read(/^Requests/)).toBe("Requests0");
    expect(read(/^Cost/)).toBe("Cost$0.00");
    expect(read(/^Error rate/)).toBe("Error rate—");
    expect(read(/^Avg latency/)).toBe("Avg latency—");
    expect(read(/^P95 latency/)).toBe("P95 latency—");
  });

  // Unpriced requests are stored at cost 0. A Cost over
  // them is unknown (all) or a floor (some), never "$0.00".
  it("reads the Cost as unknown or a floor when requests had no price", () => {
    state.overview = {
      ...OVERVIEW,
      total_cost: { value: "0", trend: null, unpriced_requests: 603 },
    };
    const { unmount } = renderAt("/dashboard/gateway/monitoring");
    let metrics = within(screen.getByRole("list", { name: "Last 24 hours" }));
    let cost = metrics.getByRole("link", { name: /^Cost/ });
    expect(cost.textContent).toBe("Cost—");
    expect(within(cost).getByTitle("No price found")).toBeInTheDocument();
    unmount();

    state.overview = {
      ...OVERVIEW,
      total_cost: { value: "0.087701", trend: null, unpriced_requests: 3 },
    };
    renderAt("/dashboard/gateway/monitoring");
    metrics = within(screen.getByRole("list", { name: "Last 24 hours" }));
    cost = metrics.getByRole("link", { name: /^Cost/ });
    // This lower bound preserves its exact priced amount.
    expect(cost.textContent).toBe("Cost\u2265\u00a0$0.087701");
    expect(
      within(cost).getByTitle(
        "3 of 603 requests had no price found, so this total counts only the priced ones.",
      ),
    ).toBeInTheDocument();
  });

  // A provider that served nothing has no latency and no
  // error rate. It read "0 ms" and "0.0%" here and "—" on Overview.
  it("a provider with 0 requests shows — for latency and error rate", () => {
    state.providers = {
      providers: [
        ...PROVIDERS.providers,
        {
          id: "anthropic",
          name: "anthropic",
          display_name: "Anthropic",
          api_format: "anthropic",
          models: ["claude-sonnet"],
          request_count: 0,
          avg_latency: 0,
          error_rate: 0,
        },
      ],
    };
    renderAt("/dashboard/gateway/monitoring");
    const row = within(screen.getByTestId("ag-grid"))
      .getAllByRole("row")
      .find((r) => r.textContent.includes("Anthropic"));
    const cells = within(row)
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
    expect(cells).toContain("0");
    expect(cells.filter((text) => text === "—")).toHaveLength(2);
    expect(row).not.toHaveTextContent("0 ms");
    expect(row).not.toHaveTextContent("0.0%");
  });
});

describe("Alerts", () => {
  // The gateway pushes and sends a rule's severity, and applies 15m to a
  // rule saved without a cooldown; the status is what the gateway reports.
  it("lists the rules as the gateway runs them, with the severity and status it applies", () => {
    state.config = { ...CONFIG, org_sections_enforced: ["alerting"] };
    renderAt("/dashboard/gateway/monitoring/rules");
    expect(
      screen.getByRole("heading", { level: 1, name: "Alerts" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/runs only on a gateway that enforces org alerting/),
    ).toBeInTheDocument();
    const rows = within(screen.getByTestId("ag-grid"))
      .getAllByRole("row")
      .slice(1);
    expect(rows[0]).toHaveTextContent("high-error-rate");
    expect(rows[0]).toHaveTextContent("Error Count >= 5 over 5m");
    expect(rows[0]).toHaveTextContent("15m");
    expect(rows[0]).toHaveTextContent("ops-webhook");
    expect(rows[0]).toHaveTextContent("critical");
    expect(rows[0]).toHaveTextContent("Active");
    // Saved without a severity: the gateway sends warning.
    expect(rows[1]).toHaveTextContent("warning");
    expect(rows[1]).toHaveTextContent("Disabled");
    const cta = screen.getByRole("button", { name: "Create rule" });
    // The Future AGI primary at the native 38 px.
    expect(cta).toHaveClass("MuiButton-containedPrimary");
    expect(cta).toHaveClass("MuiButton-sizeMedium");
  });

  it("asks before deleting a rule, naming it, and Cancel deletes nothing", async () => {
    const user = userEvent.setup();
    renderAt("/dashboard/gateway/monitoring/rules");
    await user.click(
      screen.getByRole("button", { name: "Delete rule high-error-rate" }),
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Delete rule high-error-rate?");
    expect(mutate).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(mutate).not.toHaveBeenCalled();
  });

  it("warns, not congratulates, when a delete saved but did not reach the gateway", async () => {
    const user = userEvent.setup();
    renderAt("/dashboard/gateway/monitoring/rules");
    await user.click(
      screen.getByRole("button", { name: "Delete rule high-error-rate" }),
    );
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Delete rule",
      }),
    );
    expect(mutate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      config: { alerting: { rules: [CONFIG.alerting.rules[1]] } },
    });
    mutate.mock.calls[0][1].onSuccess({
      gateway_synced: false,
      gateway_warning: "Gateway unreachable",
    });
    expect(snack).toHaveBeenCalledWith("Gateway unreachable", {
      variant: "warning",
    });
    expect(snack).not.toHaveBeenCalledWith(expect.anything(), {
      variant: "success",
    });
  });

  it("lists channels with masked URLs and which rules use them", async () => {
    const user = userEvent.setup();
    renderAt("/dashboard/gateway/monitoring/channels");
    const rows = within(screen.getByTestId("ag-grid"))
      .getAllByRole("row")
      .slice(1);
    expect(rows[0]).toHaveTextContent("ops-webhook");
    expect(rows[0]).toHaveTextContent("Webhook");
    expect(rows[0]).toHaveTextContent("2 rules");
    expect(rows[1]).not.toHaveTextContent("secret");
    expect(screen.queryByText(/critical/i)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Add channel" }),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Delete channel oncall" }),
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Delete channel oncall?");
    expect(mutate).not.toHaveBeenCalled();
    await user.click(
      within(dialog).getByRole("button", { name: "Delete channel" }),
    );
    expect(mutate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      config: { alerting: { channels: [CONFIG.alerting.channels[0]] } },
    });
  });

  it.each(["rules", "channels"])(
    "shows a failed config read once with Retry on %s",
    async (tab) => {
      state.config = undefined;
      state.configError = new Error("Config read failed");
      renderAt(`/dashboard/gateway/monitoring/${tab}`);
      expect(
        screen.getAllByText("Couldn't load your alert rules"),
      ).toHaveLength(1);
      expect(screen.queryByText("No alert rules yet")).toBeNull();
      expect(screen.queryByText("No channels yet")).toBeNull();
      expect(
        screen.getByRole("button", {
          name: tab === "rules" ? "Create rule" : "Add channel",
        }),
      ).toBeDisabled();
      expect(state.configOptions).toEqual({ meta: { errorHandled: true } });
      expect(snack).not.toHaveBeenCalled();
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(state.refetch).toHaveBeenCalledOnce();
    },
  );

  it("explains an empty rule list", () => {
    state.config = { alerting: { channels: [] } };
    renderAt("/dashboard/gateway/monitoring/rules");
    expect(screen.getByText("No alert rules yet")).toBeInTheDocument();
  });
});

describe("Create alert rule", () => {
  it("sends the cooldown and the severity the gateway applies", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider theme={faTestTheme("light")}>
        <CreateAlertRuleDialog
          open
          onClose={() => {}}
          gatewayId="default"
          existingRules={[]}
          channelNames={["ops-webhook"]}
        />
      </ThemeProvider>,
    );
    expect(screen.getByLabelText(/Severity/)).toBeInTheDocument();
    await user.type(screen.getByLabelText(/Rule name/), "too-many-errors");
    await user.type(screen.getByLabelText(/Threshold/), "3");
    const cooldown = screen.getByLabelText(/Cooldown/);
    expect(cooldown).toHaveValue("15m");
    await user.clear(cooldown);
    await user.type(cooldown, "30m");
    await user.click(screen.getByRole("button", { name: "Create rule" }));
    const [{ config }, handlers] = mutate.mock.calls[0];
    const rule = config.alerting.rules[0];
    expect(rule).toMatchObject({
      name: "too-many-errors",
      threshold: 3,
      cooldown: "30m",
      severity: "warning",
    });
    handlers.onSuccess({ gateway_synced: true });
    expect(snack).toHaveBeenCalledWith('Alert rule "too-many-errors" created', {
      variant: "success",
    });
  });
});

describe("Monitoring and Alerts grid widths", () => {
  it.each([
    ["providers", "/dashboard/gateway/monitoring"],
    ["rules", "/dashboard/gateway/monitoring/rules"],
    ["channels", "/dashboard/gateway/monitoring/channels"],
  ])("cuts off no %s header and fits at 1440 px", (_name, url) => {
    renderAt(url);
    expect(screen.getByTestId("ag-grid")).toBeInTheDocument();
    expect(cutHeaders(grid.props)).toEqual([]);
    expect(minimumGridWidth(grid.props)).toBeLessThanOrEqual(gridBudget1440());
  });
});
