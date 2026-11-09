import React, { forwardRef } from "react";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { enqueueSnackbar } from "notistack";

import GatewayOverviewPage from "../GatewayOverviewPage";
import GatewayOverview from "src/pages/dashboard/gateway/GatewayOverview";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../../testing/adminOnly";

const grid = vi.hoisted(() => ({ props: null }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grid.props = props;
    return <div data-testid="ag-grid" />;
  }),
}));

const state = vi.hoisted(() => ({
  gateway: null,
  gatewayLoading: false,
  gatewayError: null,
  providers: null,
  overviewCalls: [],
  overview: null,
  keys: [],
  logCount: 0,
  orgConfig: null,
  config: null,
  managedStatus: { data: { available: true, enabled: true } },
  refreshGateways: null,
  // The organization level (useGatewayAdmin): an Admin unless a case says.
  orgLevel: 8,
}));

const http = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, post: http.post } };
});
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: state.orgLevel }),
}));
vi.mock("../../models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => state.managedStatus,
}));

vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gateway: state.gateway,
    gatewayId: state.gateway?.id || null,
    isLoading: state.gatewayLoading,
    error: state.gatewayError,
    refreshGateways: state.refreshGateways,
  }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: state.config, isLoading: false }),
  useProviderHealth: () => ({
    data: state.providers ? { providers: state.providers } : undefined,
    isLoading: false,
  }),
}));
vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: (params) => {
    state.overviewCalls.push(params);
    return { data: state.overview, isLoading: false };
  },
}));
vi.mock("../../keys/hooks/useApiKeys", () => ({
  useApiKeys: () => ({ data: state.keys, isLoading: false }),
}));
vi.mock("../../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: state.orgConfig, isLoading: false }),
}));
vi.mock("../../onboarding/useRequestCount", () => ({
  useRequestCount: () => ({ data: state.logCount, isLoading: false }),
}));

const GATEWAY = {
  id: "default",
  name: "Agent Command Center Gateway",
  baseUrl: "http://localhost:28490/v1",
  status: "healthy",
  providerCount: 2,
  modelCount: 7,
};

const PROVIDERS = [
  {
    id: "openai",
    name: "openai",
    display_name: "OpenAI (local mock LLM)",
    healthy: true,
    status: "healthy",
    models: ["gpt-4o", "gpt-4o-mini"],
    request_count: 391,
    avg_latency: 33.22,
    error_rate: 11,
  },
];

function renderPage(ui = <GatewayOverviewPage />) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <HelmetProvider>{ui}</HelmetProvider>
    </QueryClientProvider>,
  );
}

describe("AI Gateway overview", () => {
  beforeEach(() => {
    state.gateway = GATEWAY;
    state.gatewayLoading = false;
    state.gatewayError = null;
    state.providers = PROVIDERS;
    state.overviewCalls = [];
    state.overview = {
      total_requests: { value: 592 },
      total_cost: { value: "0.087686" },
      avg_latency_ms: { value: 114.76 },
      error_rate: { value: 12.84 },
    };
    state.keys = [{ id: "k1", name: "support-bot" }];
    state.logCount = 592;
    state.orgConfig = { managed_models: { enabled: true } };
    state.config = null;
    state.managedStatus = { data: { available: true, enabled: true } };
    state.refreshGateways = vi.fn();
    state.orgLevel = 8;
    http.post.mockReset();
    vi.mocked(enqueueSnackbar).mockReset();
    grid.props = null;
    try {
      window.localStorage.removeItem("agentcc_getting_started_dismissed");
    } catch {
      // storage may be unavailable
    }
  });

  it("F3 keeps Overview and provider setup without a managed offer for a disabled organization", () => {
    state.providers = [];
    state.keys = [];
    state.logCount = 0;
    state.orgConfig = { managed_models: { enabled: false } };
    state.managedStatus = { data: { available: true, enabled: false } };
    renderPage();
    expect(
      screen.getByRole("heading", { name: "Overview", level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByText("No providers yet")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Add your own provider keys; the gateway routes requests through them.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/or use managed models from the catalog/),
    ).toBeNull();
    expect(
      screen.getByRole("link", { name: "Add a provider" }),
    ).toBeInTheDocument();
  });

  it("is titled Overview, not the gateway record's name", () => {
    renderPage();
    expect(
      screen.getByRole("heading", { level: 1, name: "Overview" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Agent Command Center Gateway"),
    ).not.toBeInTheDocument();
  });

  it("the /dashboard/gateway route's page renders the same page", () => {
    renderPage(<GatewayOverview />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Overview" }),
    ).toBeInTheDocument();
  });

  it("asks analytics for the last 24 hours without gateway_id (the dev guard rejects it, so KPIs read 0)", () => {
    renderPage();
    expect(state.overviewCalls.length).toBeGreaterThan(0);
    state.overviewCalls.forEach((params) => {
      expect(params).not.toHaveProperty("gatewayId");
      expect(params).toHaveProperty("start");
    });
    const kpis = within(screen.getByTestId("overview-kpis"));
    expect(kpis.getByText("592")).toBeInTheDocument();
    // Rounded CostTotal display, not a ledger balance (e9295aaf92, f225ecefc6).
    expect(kpis.getByText("$0.087686")).toBeInTheDocument();
    expect(kpis.getByText("115 ms")).toBeInTheDocument();
    expect(kpis.getByText("12.8%")).toBeInTheDocument();
  });

  // With no request in the last 24 hours there is no latency and no error
  // rate to show: both read "—", as the Analytics page does, never "0.0%".
  it("shows no error rate or latency when there was no request", () => {
    state.overview = {
      total_requests: { value: 0 },
      total_cost: { value: "0" },
      avg_latency_ms: { value: 0 },
      error_rate: { value: 0 },
    };
    renderPage();
    const kpis = within(screen.getByTestId("overview-kpis"));
    expect(kpis.getByText("Error rate").nextSibling).toHaveTextContent(/^—$/);
    expect(kpis.getByText("Avg latency").nextSibling).toHaveTextContent(/^—$/);
    expect(kpis.queryByText("0.0%")).toBeNull();
  });

  // An unpriced request is stored at cost 0, so the Cost
  // figure over unpriced traffic read "$0.00". Unknown is not free.
  it("reads the Cost as unknown when no request had a price, and as a floor when some had none", () => {
    state.overview = {
      ...state.overview,
      total_cost: { value: "0", unpriced_requests: 592 },
    };
    const { unmount } = renderPage();
    let cost = within(screen.getByTestId("overview-kpis")).getByText(
      "Cost",
    ).nextSibling;
    expect(cost).toHaveTextContent(/^—$/);
    expect(within(cost).getByTitle("No price found")).toBeInTheDocument();
    unmount();

    state.overview = {
      ...state.overview,
      total_cost: { value: "0.087686", unpriced_requests: 12 },
    };
    renderPage();
    cost = within(screen.getByTestId("overview-kpis")).getByText(
      "Cost",
    ).nextSibling;
    // Release-lead 595: this lower bound preserves its exact priced amount.
    expect(cost).toHaveTextContent(/^≥\s\$0\.087686$/);
    expect(
      within(cost).getByTitle(
        "12 of 592 requests had no price found, so this total counts only the priced ones.",
      ),
    ).toBeInTheDocument();
  });

  it("shows the endpoint with a copy button and the live status", () => {
    renderPage();
    const endpoint = within(screen.getByTestId("overview-endpoint"));
    expect(
      endpoint.getAllByText(/http:\/\/localhost:28490\/v1/).length,
    ).toBeGreaterThan(0);
    expect(
      endpoint.getByRole("button", { name: "Copy endpoint" }),
    ).toBeInTheDocument();
    expect(endpoint.getByText("Healthy")).toBeInTheDocument();
  });

  it("shows an explicit missing state without a copy action or substitute snippet", () => {
    state.gateway = { ...GATEWAY, baseUrl: "", status: "unconfigured" };
    renderPage();
    const endpoint = within(screen.getByTestId("overview-endpoint"));
    expect(
      endpoint.getByText("Gateway URL not configured"),
    ).toBeInTheDocument();
    expect(
      endpoint.getByText(/set AGENTCC_GATEWAY_PUBLIC_URL/),
    ).toBeInTheDocument();
    expect(
      endpoint.queryByRole("button", { name: "Copy endpoint" }),
    ).toBeNull();
    expect(endpoint.queryByRole("tab", { name: "cURL" })).toBeNull();
    expect(endpoint.queryByRole("link", { name: "Create a key" })).toBeNull();
    expect(document.body.textContent).not.toContain("localhost");
    expect(document.body.textContent).not.toContain(
      "https://gateway.futureagi.com/v1",
    );
    expect(screen.queryByText(/not reachable from the platform/)).toBeNull();
  });

  it.each(["https://gateway.example.com/v1", "http://localhost:8090/v1"])(
    "keeps the configured endpoint and snippet for %s",
    (url) => {
      state.gateway = { ...GATEWAY, baseUrl: url };
      renderPage();
      const endpoint = within(screen.getByTestId("overview-endpoint"));
      expect(endpoint.getByText(url)).toBeInTheDocument();
      expect(endpoint.getByText(/curl /)).toHaveTextContent(url);
      expect(
        endpoint.getByRole("button", { name: "Copy endpoint" }),
      ).toBeInTheDocument();
      expect(endpoint.queryByText("Gateway URL not configured")).toBeNull();
    },
  );

  it("offers a quickstart for a model the org really has, with a key placeholder and a Create key link", async () => {
    const user = userEvent.setup();
    renderPage();
    const endpoint = within(screen.getByTestId("overview-endpoint"));
    expect(endpoint.getByText(/"model": "gpt-4o"/)).toBeInTheDocument();
    expect(endpoint.getByText(/\$AGENTCC_API_KEY/)).toBeInTheDocument();
    expect(
      endpoint.getByRole("link", { name: "Create a key" }),
    ).toHaveAttribute("href", "/dashboard/gateway/keys?create=1");
    await user.click(endpoint.getByRole("tab", { name: "Python" }));
    expect(
      endpoint.getByText(/base_url="http:\/\/localhost:28490\/v1"/),
    ).toBeInTheDocument();
  });

  // A copied example has to work: its model is one a provider measured
  // Healthy serves (5 or more requests in 24 h, status healthy), never one
  // of a provider whose requests fail or that has too few to tell.
  it("fills the quickstart with a model of a provider measured healthy, never a failing one's", () => {
    state.providers = [
      {
        id: "walk-flaky",
        name: "walk-flaky",
        status: "healthy",
        healthy: true,
        models: ["walk-flaky-model"],
        request_count: 1,
        error_rate: 100,
      },
      ...PROVIDERS,
    ];
    renderPage();
    const endpoint = within(screen.getByTestId("overview-endpoint"));
    expect(endpoint.getByText(/"model": "gpt-4o"/)).toBeInTheDocument();
    expect(endpoint.queryByText(/walk-flaky-model/)).toBeNull();
  });

  it("fills the quickstart with YOUR_MODEL when no provider is measured healthy", () => {
    state.providers = [
      {
        id: "walk-flaky",
        name: "walk-flaky",
        status: "healthy",
        healthy: true,
        models: ["walk-flaky-model"],
        request_count: 1,
        error_rate: 100,
      },
      { ...PROVIDERS[0], status: "unhealthy", healthy: false },
    ];
    renderPage();
    const endpoint = within(screen.getByTestId("overview-endpoint"));
    expect(endpoint.getByText(/"model": "YOUR_MODEL"/)).toBeInTheDocument();
  });

  it("promises nothing the page does not show in its description", () => {
    renderPage();
    expect(screen.queryByText(/what to set up next/)).toBeNull();
  });

  // "curl" here, "cURL" on the model API tab.
  it("spells cURL on its code switch as the model API tab does", () => {
    renderPage();
    const endpoint = within(screen.getByTestId("overview-endpoint"));
    expect(endpoint.getByRole("tab", { name: "cURL" })).toBeInTheDocument();
    expect(endpoint.queryByRole("tab", { name: "curl" })).toBeNull();
  });

  it("lists providers with their real 24h numbers and never paints a health colour", () => {
    renderPage();
    const rows = grid.props.rowData;
    expect(rows).toEqual([
      expect.objectContaining({
        name: "OpenAI (local mock LLM)",
        modelCount: 2,
        requests: 391,
        errorRate: 11,
      }),
    ]);
    const headers = grid.props.columnDefs.map((col) => col.headerName);
    expect(headers.join(" ")).not.toMatch(/health|status/i);
  });

  it("builds its provider rows the way the Providers page does", () => {
    // provider-rows.js: the gateway config lists the providers, the providers
    // read adds the 24 h numbers by id, and a provider with no requests shows
    // no latency or error rate (never 0 ms or 0.0 %).
    state.config = {
      providers: {
        openai: {
          display_name: "OpenAI (local mock LLM)",
          models: ["gpt-4o", "gpt-4o-mini"],
        },
        groq: { display_name: "Groq", models: ["llama-3.1-8b-instant"] },
        anthropic: { models: [] },
      },
    };
    state.providers = [
      ...PROVIDERS,
      {
        id: "groq",
        name: "groq",
        models: ["llama-3.1-8b-instant"],
        request_count: 0,
        avg_latency: 0,
        error_rate: 0,
      },
    ];
    renderPage();
    const rows = grid.props.rowData;
    expect(rows.map((row) => row.id)).toEqual(["openai", "groq", "anthropic"]);
    expect(rows[0]).toEqual(
      expect.objectContaining({
        name: "OpenAI (local mock LLM)",
        requests: 391,
        errorRate: 11,
        latency: 33.22,
      }),
    );
    expect(rows[1]).toEqual(
      expect.objectContaining({
        name: "Groq",
        requests: 0,
        errorRate: null,
        latency: null,
      }),
    );
    expect(rows[2]).toEqual(
      expect.objectContaining({
        name: "anthropic",
        requests: 0,
        latency: null,
      }),
    );
  });

  it("drops the old Last check: Never line", () => {
    renderPage();
    expect(screen.queryByText(/Last check/i)).toBeNull();
    expect(screen.queryByText("Never")).toBeNull();
  });

  it("shows one error state with Retry when the gateway cannot be read", () => {
    state.gateway = null;
    state.gatewayError = new Error("boom");
    renderPage();
    expect(
      screen.getByText("Couldn't reach the AI Gateway"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("shows the providers empty state when none is configured", () => {
    state.providers = [];
    renderPage();
    expect(screen.getByText("No providers yet")).toBeInTheDocument();
  });

  // Every hosted organization today: no managed models to turn on.
  it("points a deployment without managed models at Providers, never the catalog", () => {
    state.managedStatus = { data: { available: false, enabled: false } };
    state.providers = [];
    state.orgConfig = { managed_models: { enabled: true } };
    renderPage();
    const start = within(screen.getByTestId("overview-getting-started"));
    expect(start.getAllByText("Add a provider").length).toBe(2);
    expect(start.getByRole("link", { name: "Add a provider" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/providers",
    );
    expect(start.queryByText(/managed models/i)).toBeNull();
    expect(screen.queryByText(/managed models from the catalog/)).toBeNull();
  });
});

// With managed models off, the first request step's Try a model opens the
// model page of the model the quickstart names, on its Try it tab:
// /models/<provider>/<model>, the id the gateway routes to that provider.
describe("AI Gateway overview: Try a model", () => {
  beforeEach(() => {
    state.gateway = GATEWAY;
    state.gatewayLoading = false;
    state.gatewayError = null;
    state.overview = null;
    state.keys = [{ id: "k1", name: "support-bot" }];
    state.logCount = 0;
    state.orgConfig = null;
    state.config = null;
    state.managedStatus = { data: { available: false, enabled: false } };
    state.refreshGateways = vi.fn();
    try {
      window.localStorage.removeItem("agentcc_getting_started_dismissed");
    } catch {
      // storage may be unavailable
    }
  });

  it("points Try a model at the first model a provider serves", () => {
    state.providers = [
      {
        id: "my-vllm",
        name: "my-vllm",
        models: ["bge-embed", "qwen-chat"],
      },
      ...PROVIDERS,
    ];
    renderPage();
    const start = within(screen.getByTestId("overview-getting-started"));
    expect(start.getByRole("link", { name: "Try a model" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/models/my-vllm/qwen-chat?tab=try",
    );
    // The quickstart's example names a model of a provider measured
    // healthy: my-vllm has served no request yet.
    const endpoint = within(screen.getByTestId("overview-endpoint"));
    expect(endpoint.getByText(/"model": "gpt-4o"/)).toBeInTheDocument();
  });

  it("offers no Try a model when no provider serves a model", () => {
    state.providers = [{ id: "my-vllm", name: "my-vllm", models: [] }];
    renderPage();
    const start = within(screen.getByTestId("overview-getting-started"));
    expect(start.queryByRole("link", { name: "Try a model" })).toBeNull();
  });
});

// The Overview says when the platform cannot reach the gateway, and the
// health check it offers reports the real result (views/gateway.py list and
// health_check). It is open to every member: it changes nothing.
describe("AI Gateway overview: gateway health", () => {
  beforeEach(() => {
    state.gateway = { ...GATEWAY, status: "unreachable" };
    state.gatewayLoading = false;
    state.gatewayError = null;
    state.providers = PROVIDERS;
    state.overview = null;
    state.keys = [];
    state.logCount = 0;
    state.orgConfig = null;
    state.config = null;
    state.managedStatus = { data: { available: false, enabled: false } };
    state.refreshGateways = vi.fn();
    state.orgLevel = 8;
    http.post.mockReset();
    vi.mocked(enqueueSnackbar).mockReset();
  });

  it("an unreachable gateway shows a warning with its status, and a health check", () => {
    renderPage();
    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent(
      "The gateway is not reachable from the platform: its last health check reported unreachable.",
    );
    expect(
      within(banner).getByRole("button", { name: "Check health" }),
    ).toBeEnabled();
    expect(
      within(screen.getByTestId("overview-endpoint")).getByText("Unreachable"),
    ).toBeInTheDocument();
  });

  it("the health check of an unreachable gateway reports the real failure, never success", async () => {
    // health_check answers 400 with result {status: "unreachable", error};
    // src/utils/axios rejects with the body.
    http.post.mockRejectedValue({
      status: false,
      statusCode: 400,
      message: "connection refused",
      error: "connection refused",
      result: { status: "unreachable", error: "connection refused" },
    });
    renderPage();
    await userEvent.click(
      within(screen.getByRole("alert")).getByRole("button", {
        name: "Check health",
      }),
    );
    expect(http.post).toHaveBeenCalledWith(
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
    expect(state.refreshGateways).toHaveBeenCalled();
  });

  it("a healthy gateway shows no warning, and its health check says it is healthy", async () => {
    state.gateway = { ...GATEWAY, status: "healthy" };
    http.post.mockResolvedValue({
      data: {
        status: true,
        result: { status: "healthy", provider_count: 2, model_count: 5 },
      },
    });
    renderPage();
    expect(screen.queryByRole("alert")).toBeNull();
    const check = within(screen.getByTestId("overview-endpoint")).getByRole(
      "button",
      { name: "Check health" },
    );
    // Its tooltip says what the check does, and what it does not.
    expect(check.closest("span")).toHaveAttribute(
      "aria-label",
      expect.stringMatching(/can reach the gateway.*not called/),
    );
    await userEvent.click(check);
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith("Gateway healthy", {
        variant: "success",
      }),
    );
  });

  it("never offers a step that is done for every organization", () => {
    state.providers = [];
    renderPage();
    expect(screen.queryByText(/Gateway connected/i)).toBeNull();
    expect(screen.getByText("0 of 3 done")).toBeInTheDocument();
  });
});

// Check health posts to the gateway: an organization Owner or Admin runs it,
// as on Gateway settings (useGatewayAdmin). Anyone else sees it disabled,
// with the role it needs, wherever the page offers it.
describe("AI Gateway overview: who runs a health check", () => {
  beforeEach(() => {
    state.gatewayLoading = false;
    state.gatewayError = null;
    state.providers = PROVIDERS;
    state.overview = null;
    state.keys = [];
    state.logCount = 0;
    state.orgConfig = null;
    state.config = null;
    state.managedStatus = { data: { available: false, enabled: false } };
    state.refreshGateways = vi.fn();
    http.post.mockReset();
    vi.mocked(enqueueSnackbar).mockReset();
  });

  it.each(NOT_ADMIN_LEVELS)(
    "%s sees Check health disabled on a healthy gateway, with the role it needs",
    async (_who, level) => {
      state.orgLevel = level;
      state.gateway = { ...GATEWAY, status: "healthy" };
      renderPage();
      const check = within(screen.getByTestId("overview-endpoint")).getByRole(
        "button",
        { name: "Check health" },
      );
      expectAdminOnly(check);
      await userEvent.click(check, { pointerEventsCheck: 0 });
      expect(http.post).not.toHaveBeenCalled();
    },
  );

  it.each(NOT_ADMIN_LEVELS)(
    "%s sees the unreachable gateway's Check health disabled, with the role it needs",
    (_who, level) => {
      state.orgLevel = level;
      state.gateway = { ...GATEWAY, status: "unreachable" };
      renderPage();
      expectAdminOnly(
        within(screen.getByRole("alert")).getByRole("button", {
          name: "Check health",
        }),
      );
    },
  );

  it.each(ADMIN_LEVELS)(
    "%s can run Check health on a healthy or an unreachable gateway",
    (_who, level) => {
      state.orgLevel = level;
      state.gateway = { ...GATEWAY, status: "healthy" };
      const { unmount } = renderPage();
      expectEditable(
        within(screen.getByTestId("overview-endpoint")).getByRole("button", {
          name: "Check health",
        }),
      );
      unmount();
      state.gateway = { ...GATEWAY, status: "unreachable" };
      renderPage();
      expectEditable(
        within(screen.getByRole("alert")).getByRole("button", {
          name: "Check health",
        }),
      );
    },
  );
});
