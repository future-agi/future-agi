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
  providerQuery: {},
  metricsQuery: {},
  keyQuery: {},
  requestQuery: {},
  gateway: null,
  // Whether this is a hosted deployment (GatewayContext's `hosted`): a hosted
  // page shows the gateway's own URL in its quickstart, a self-hosted one the
  // placeholder.
  hosted: true,
  deploymentModeConfirmed: true,
  retryDeploymentMode: vi.fn(),
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
  // No catalog reply: credit-eligible request targets are covered in
  // OverviewPage.credits.test.jsx.
  useManagedCatalog: () => ({ data: undefined, isLoading: false }),
}));

vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gateway: state.gateway,
    gatewayId: state.gateway?.id || null,
    hosted: state.hosted,
    deploymentModeConfirmed: state.deploymentModeConfirmed,
    retryDeploymentMode: state.retryDeploymentMode,
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
    ...state.providerQuery,
  }),
}));
vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: (params) => {
    state.overviewCalls.push(params);
    return { data: state.overview, isLoading: false, ...state.metricsQuery };
  },
}));
vi.mock("../../keys/hooks/useApiKeys", () => ({
  useApiKeys: () => ({ data: state.keys, isLoading: false, ...state.keyQuery }),
}));
vi.mock("../../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: state.orgConfig, isLoading: false }),
}));
vi.mock("../../onboarding/useRequestCount", () => ({
  useRequestCount: () => ({ data: state.logCount, isLoading: false, ...state.requestQuery }),
}));

// R-LIT synthetic: a visibly fake public gateway URL (RFC 2606 .test host),
// the value AGENTCC_GATEWAY_PUBLIC_URL would give a hosted deployment.
const PUBLIC_URL = "https://gateway.example.test/v1";
// R-LIT synthetic: the local address the platform reports when no public URL
// is set.
const LOCAL_URL = "http://localhost:8090/v1";

const GATEWAY = {
  id: "default",
  name: "Agent Command Center Gateway",
  baseUrl: PUBLIC_URL,
  status: "healthy",
  providerCount: 2,
  modelCount: 7,
  // The gateway serves Try it (GET /agentcc/gateways/ delegated_playground_available).
  delegatedPlaygroundAvailable: true,
};

// R-LIT classified (UI copy): the note shown beside a quickstart built on the
// placeholder when no confirmed public URL is known.
const NO_URL_NOTE =
  "The gateway's public URL isn't available from the console right now, so these requests use the placeholder https://YOUR_GATEWAY_URL/v1. Replace it with your gateway's address before you run them, or reload the page to try again.";
// R-LIT classified (UI copy): the placeholder, and the note a confirmed
// self-hosted deployment with no public address gets beside it (the new-key
// dialog's note).
const PLACEHOLDER_URL = "https://YOUR_GATEWAY_URL/v1";
const PLACEHOLDER_NOTE =
  "The base URL and request here use the placeholder https://YOUR_GATEWAY_URL/v1. Replace it with your gateway's address before you use them.";
// R-LIT classified (UI copy): what works instead of Try it, as the model
// menus say it (noUsableModelSentence), with Future AGI credits off and on.
const PROVIDER_KEY_SENTENCE =
  "Run this with your own provider key (Settings → AI Providers).";
const API_KEY_SENTENCE =
  "Run this with your own provider key (Settings → AI Providers), or call the gateway with an API key on your Future AGI credits (AI Gateway → API keys).";
const LOOPBACK_OR_CLUSTER = /localhost|127\.0\.0\.1|\[::1\]|agentcc-gateway/i;

const PROVIDERS = [
  {
    id: "openai",
    name: "openai",
    display_name: "OpenAI (local mock LLM)",
    healthy: true,
    pushed: true,
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
  const wrap = (page) => (
    <QueryClientProvider client={client}>
      <HelmetProvider>{page}</HelmetProvider>
    </QueryClientProvider>
  );
  const result = render(wrap(ui));
  return { ...result, rerender: (page) => result.rerender(wrap(page)) };
}

beforeEach(() => {
  state.providerQuery = {};
  state.metricsQuery = {};
  state.keyQuery = {};
  state.requestQuery = {};
  state.orgLevel = 8;
});

describe("BP-01 first request and independent recovery", () => {
  beforeEach(() => {
    state.gateway = GATEWAY;
    state.hosted = true;
    state.deploymentModeConfirmed = true;
    state.gatewayLoading = false;
    state.gatewayError = null;
    state.providers = PROVIDERS;
    state.config = null;
    state.orgConfig = null;
    state.overview = { total_requests: { value: 592 } };
    state.keys = [{ id: "k1", status: "active" }];
    state.logCount = 0;
    state.managedStatus = { data: { available: false, enabled: false } };
    http.post.mockReset();
  });

  it.each([0, 1, 4])("selects and copies a configured route at %s requests without inference or a health claim", async (count) => {
    state.providers = [{ ...PROVIDERS[0], request_count: count }];
    const user = userEvent.setup();
    renderPage();
    const panel = screen.getByTestId("overview-endpoint");
    expect(panel.querySelector("pre")).toBeNull();
    await user.selectOptions(within(panel).getByRole("combobox", { name: "Configured model" }), "openai/gpt-4o-mini");
    expect(within(panel).getByText(/Too few requests.*health is unmeasured/i)).toBeInTheDocument();
    expect(within(panel).getByText(/chat-shaped example.*compatibility is unverified/i)).toBeInTheDocument();
    for (const language of ["cURL", "Python", "TypeScript"]) {
      await user.click(within(panel).getByRole("tab", { name: language }));
      expect(panel.querySelector("pre").textContent).toContain('"openai/gpt-4o-mini"');
      await user.click(within(panel).getByRole("button", { name: `Copy ${language}` }));
      expect(await navigator.clipboard.readText()).toContain('"openai/gpt-4o-mini"');
    }
    expect(http.post).not.toHaveBeenCalled();
  });

  it("offers setup and discovery, not a model placeholder, with no usable selection", async () => {
    state.providers = [{ ...PROVIDERS[0], pushed: false }];
    renderPage();
    const panel = screen.getByTestId("overview-endpoint");
    expect(panel.querySelector("pre")).toBeNull();
    expect(panel.textContent).not.toContain("YOUR_MODEL");
    expect(within(panel).getByRole("link", { name: "Manage providers" })).toBeInTheDocument();
    await userEvent.click(within(panel).getByRole("button", { name: "Discover models" }));
    expect(panel.querySelector("pre").textContent.match(/\/v1\/models/g)).toHaveLength(1);
    expect(panel.textContent).toContain("gateway API key");
    expect(panel.textContent).toContain("provider key");
    for (const [language, install] of [["Python", "python -m pip install openai"], ["TypeScript", "npm install openai"]]) {
      await userEvent.click(within(panel).getByRole("tab", { name: language }));
      expect(panel.querySelector("pre").textContent).toContain(install);
      expect(panel.querySelector("pre").textContent).toContain("client.models.list()");
    }
    expect(http.post).not.toHaveBeenCalled();
  });

  it.each([
    ["providerQuery", "Could not load providers"],
    ["metricsQuery", "Could not load metrics"],
    ["managedStatus", "Could not load credit availability"],
  ])("recovers %s independently while endpoint and key setup remain usable", async (key, message) => {
    const refetch = vi.fn();
    state[key] = { ...state[key], isError: true, error: new Error("read failed"), refetch };
    const { rerender } = renderPage();
    const panel = within(screen.getByTestId("overview-endpoint"));
    expect(panel.getByRole("button", { name: "Copy endpoint" })).toBeEnabled();
    expect(panel.getByRole("link", { name: "Create a key" })).toBeInTheDocument();
    const alert = screen.getByText(message).closest('[role="alert"]');
    await userEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
    if (key === "providerQuery") {
      expect(screen.queryByText("No providers yet")).toBeNull();
      expect(screen.queryByTestId("ag-grid")).toBeNull();
      expect(panel.queryByRole("combobox")).toBeNull();
    }
    if (key === "metricsQuery") expect(screen.queryByTestId("overview-kpis")).toBeNull();
    state[key] = { ...state[key], isError: false, error: null };
    rerender(<GatewayOverviewPage />);
    expect(screen.queryByText(message)).toBeNull();
    expect(http.post).not.toHaveBeenCalled();
  });

  it.each(["providerQuery", "metricsQuery", "managedStatus"])("does not turn a pending %s read into empty or zero data", (key) => {
    state[key] = { isLoading: true, isPending: true };
    renderPage();
    expect(screen.getByRole("button", { name: "Copy endpoint" })).toBeEnabled();
    expect(screen.queryByText("No providers yet")).toBeNull();
    if (key === "providerQuery") expect(screen.queryByTestId("ag-grid")).toBeNull();
    if (key === "metricsQuery") expect(screen.queryByTestId("overview-kpis")).toBeNull();
    expect(screen.getByTestId("overview-getting-started")).toBeInTheDocument();
  });

  it.each([
    { available: false, enabled: true, balance_usd: "10" },
    { available: true, enabled: false, balance_usd: "10" },
    { available: true, enabled: true, balance_usd: "0" },
    { available: true, enabled: true },
  ])("does not advertise usable credits for %j", (data) => {
    state.providers = [];
    state.managedStatus = { data };
    renderPage();
    expect(screen.queryByText("Future AGI credits are available for eligible catalog models.")).toBeNull();
    expect(screen.queryByText("Browse credit-eligible models")).toBeNull();
    expect(screen.getByTestId("overview-endpoint").querySelector("pre")).toBeNull();
  });

  it("does not reuse a stale positive credit balance after the status read fails", () => {
    state.providers = [];
    state.managedStatus = {
      data: { available: true, enabled: true, balance_usd: "10" },
      isError: true, error: new Error("unavailable"), refetch: vi.fn(),
    };
    renderPage();
    expect(screen.getByText("Could not load credit availability")).toBeInTheDocument();
    expect(screen.queryByText("Browse credit-eligible models")).toBeNull();
    expect(screen.queryByText("Future AGI credits are available for eligible catalog models.")).toBeNull();
    expect(screen.getByRole("listitem", { name: "Add a provider: Not checked" })).toBeInTheDocument();
  });

  it("does not complete key readiness from an expired or unreadable key list", () => {
    state.keys = [{ id: "expired", status: "active", expiresAt: "2000-01-01T00:00:00Z" }];
    const { rerender } = renderPage();
    expect(screen.getByRole("listitem", { name: "Create an API key: Not complete" })).toBeInTheDocument();
    state.keyQuery = { isError: true, error: new Error("read failed"), refetch: vi.fn() };
    rerender(<GatewayOverviewPage />);
    expect(screen.getByRole("listitem", { name: "Create an API key: Not checked" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy endpoint" })).toBeEnabled();
  });

  it("sends a funded managed-only user to Models without inventing a request target", () => {
    state.providers = [];
    state.managedStatus = { data: { available: true, enabled: true, balance_usd: "10" } };
    renderPage();
    expect(screen.getByRole("link", { name: "Browse credit-eligible models" })).toHaveAttribute("href", "/dashboard/gateway/models");
    expect(screen.getByTestId("overview-endpoint").querySelector("pre")).toBeNull();
  });

  it("wires tri-state readiness, request errors and the read-only Check again action", async () => {
    state.providers = [{ ...PROVIDERS[0], pushed: false }];
    state.keys = [{ id: "revoked", status: "revoked" }];
    state.providerQuery = { refetch: vi.fn() };
    state.keyQuery = { refetch: vi.fn() };
    state.managedStatus = { data: { available: false }, refetch: vi.fn() };
    state.requestQuery = { data: undefined, isError: true, refetch: vi.fn() };
    const { rerender } = renderPage();
    const setup = within(screen.getByTestId("overview-getting-started"));
    expect(setup.getByText("0 of 3 done")).toBeInTheDocument();
    expect(setup.getByRole("listitem", { name: "First successful request: Not checked" })).toBeInTheDocument();
    await userEvent.click(setup.getByRole("button", { name: "Check again" }));
    for (const query of [state.providerQuery, state.keyQuery, state.managedStatus, state.requestQuery]) {
      expect(query.refetch).toHaveBeenCalledTimes(1);
    }
    state.requestQuery = { data: 1, isError: false };
    rerender(<GatewayOverviewPage />);
    expect(screen.getByText("1 of 3 done")).toBeInTheDocument();
    expect(http.post).not.toHaveBeenCalled();
  });

  it("shows members Try a model disabled with the reason, not an admin console test", () => {
    state.orgLevel = 3;
    renderPage();
    const setup = within(screen.getByTestId("overview-getting-started"));
    expect(setup.queryByRole("link", { name: "Try a model" })).toBeNull();
    expect(setup.getByRole("button", { name: "Try a model" })).toBeDisabled();
    expect(
      setup.getByText(
        "Only an organization Owner or Admin can run models here.",
      ),
    ).toBeInTheDocument();
    expect(
      setup.getByText(/run it with your personal API key/),
    ).toBeInTheDocument();
  });
});

describe("AI Gateway overview", () => {
  beforeEach(() => {
    state.gateway = GATEWAY;
    state.hosted = true;
    state.deploymentModeConfirmed = true;
    state.retryDeploymentMode.mockReset();
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
    // No traffic, no verdict: "—" is not a healthy error rate.
    expect(kpis.queryByText("Under 5%")).toBeNull();
    expect(kpis.queryByText("Above 5%")).toBeNull();
  });

  // The port of dev 4a1c6e93df: a rate over traffic is marked against 5%,
  // exactly 5% still counts as under.
  it.each([
    [0.4, "Under 5%", "Above 5%"],
    [5, "Under 5%", "Above 5%"],
    [5.1, "Above 5%", "Under 5%"],
    [12.84, "Above 5%", "Under 5%"],
  ])("marks an error rate of %s over traffic as %s", (rate, shown, hidden) => {
    state.overview = { ...state.overview, error_rate: { value: rate } };
    renderPage();
    const kpis = within(screen.getByTestId("overview-kpis"));
    expect(kpis.getByText(shown)).toBeInTheDocument();
    expect(kpis.queryByText(hidden)).toBeNull();
  });

  it("does not judge an error rate the API did not give", () => {
    state.overview = { ...state.overview, error_rate: null };
    renderPage();
    const kpis = within(screen.getByTestId("overview-kpis"));
    expect(kpis.getByText("Error rate").nextSibling).toHaveTextContent(/^—$/);
    expect(kpis.queryByText("Under 5%")).toBeNull();
    expect(kpis.queryByText("Above 5%")).toBeNull();
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
    // This lower bound preserves its exact priced amount.
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
      endpoint.getAllByText(/https:\/\/gateway\.example\.test\/v1/).length,
    ).toBeGreaterThan(0);
    expect(
      endpoint.getByRole("button", { name: "Copy endpoint" }),
    ).toBeInTheDocument();
    expect(endpoint.getByText("Healthy")).toBeInTheDocument();
  });

  it("shows an explicit missing state once, without a copy action, any request on the marked placeholder", () => {
    state.gateway = { ...GATEWAY, baseUrl: "", status: "unconfigured" };
    renderPage();
    const panel = screen.getByTestId("overview-endpoint");
    const endpoint = within(panel);
    // The Endpoint row shows the marked placeholder; the note says why.
    expect(
      endpoint.getByText(PLACEHOLDER_URL, { selector: "code" }),
    ).toBeInTheDocument();
    expect(endpoint.queryByText("Gateway URL not configured")).toBeNull();
    // Said once, in the note, not again as the panel's description.
    expect(
      endpoint.getByText(/set AGENTCC_GATEWAY_PUBLIC_URL/),
    ).toBeInTheDocument();
    expect(
      panel.textContent.split("isn't available from the console").length - 1,
    ).toBe(1);
    expect(
      endpoint.queryByRole("button", { name: "Copy endpoint" }),
    ).toBeNull();
    const code = panel.querySelector("pre");
    if (code) expect(code).toHaveTextContent("https://YOUR_GATEWAY_URL/v1");
    expect(document.body.textContent).not.toContain("localhost");
    expect(document.body.textContent).not.toContain(
      "https://gateway.futureagi.com/v1",
    );
    expect(screen.queryByText(/not reachable from the platform/)).toBeNull();
  });

  it.each(["https://gateway.example.com/v1", "https://gateway.example.test/v1"])(
    "keeps the configured public endpoint and snippet for %s",
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
    expect(endpoint.getByText(/"model": "openai\/gpt-4o"/)).toBeInTheDocument();
    expect(endpoint.getByText(/\$AGENTCC_API_KEY/)).toBeInTheDocument();
    expect(
      endpoint.getByRole("link", { name: "Create a key" }),
    ).toHaveAttribute("href", "/dashboard/gateway/keys?create=1");
    await user.click(endpoint.getByRole("tab", { name: "Python" }));
    expect(
      endpoint.getByText(/base_url="https:\/\/gateway\.example\.test\/v1"/),
    ).toBeInTheDocument();
  });

  it("shows a hosted deployment's quickstart the configured public URL in every language, never the placeholder or localhost", async () => {
    const user = userEvent.setup();
    renderPage();
    const panel = screen.getByTestId("overview-endpoint");
    for (const language of ["cURL", "Python", "TypeScript"]) {
      await user.click(within(panel).getByRole("tab", { name: language }));
      expect(panel.textContent, language).toContain(PUBLIC_URL);
      expect(panel.textContent, language).not.toMatch(
        /YOUR_GATEWAY_URL|localhost/i,
      );
    }
  });

  // R-LIT classified (UI copy): the placeholder is literal text the reader
  // replaces, pinned as written; the reported address is the local default.
  it("shows a self-hosted deployment's quickstart the placeholder in every language, not the address the platform reports", async () => {
    const user = userEvent.setup();
    state.hosted = false;
    state.gateway = { ...GATEWAY, baseUrl: LOCAL_URL };
    renderPage();
    const panel = screen.getByTestId("overview-endpoint");
    for (const language of ["cURL", "Python", "TypeScript"]) {
      await user.click(within(panel).getByRole("tab", { name: language }));
      const code = panel.querySelector("pre").textContent;
      expect(code, language).toContain("https://YOUR_GATEWAY_URL/v1");
      expect(code, language).not.toMatch(/localhost/i);
    }
    // The placeholder says what to put in its place, as the new-key dialog
    // does; the console is not missing anything a reload would bring.
    expect(panel.textContent).toContain(PLACEHOLDER_NOTE);
    expect(panel.textContent).not.toContain(NO_URL_NOTE);
  });

  // The Endpoint row is the address to send requests to: a confirmed
  // self-hosted deployment whose reported address is loopback or in-cluster
  // shows the marked placeholder there, with its note, as its requests do.
  it.each([
    LOCAL_URL,
    "http://127.0.0.1:8090/v1",
    "http://[::1]:8090/v1",
    "http://agentcc-gateway:8090/v1",
  ])(
    "shows a self-hosted Endpoint row the placeholder and its note, never %s",
    (baseUrl) => {
      state.hosted = false;
      state.gateway = { ...GATEWAY, baseUrl };
      renderPage();
      const panel = screen.getByTestId("overview-endpoint");
      const endpoint = within(panel);
      expect(
        endpoint.getByText(PLACEHOLDER_URL, { selector: "code" }),
      ).toBeInTheDocument();
      expect(
        endpoint.queryByRole("button", { name: "Copy endpoint" }),
      ).toBeNull();
      expect(
        endpoint.queryByText(/Send OpenAI-compatible requests here/),
      ).toBeNull();
      expect(panel.textContent.split(PLACEHOLDER_NOTE).length - 1).toBe(1);
      expect(panel.textContent).not.toMatch(LOOPBACK_OR_CLUSTER);
    },
  );

  it("keeps a self-hosted deployment's public address in the Endpoint row, with its copy action and no note", () => {
    const url = "https://gateway.corp.example.test/v1";
    state.hosted = false;
    state.gateway = { ...GATEWAY, baseUrl: url };
    renderPage();
    const panel = screen.getByTestId("overview-endpoint");
    const endpoint = within(panel);
    expect(endpoint.getByText(url, { selector: "code" })).toBeInTheDocument();
    expect(
      endpoint.getByRole("button", { name: "Copy endpoint" }),
    ).toBeInTheDocument();
    expect(
      endpoint.getByText(/Send OpenAI-compatible requests here/),
    ).toBeInTheDocument();
    expect(panel.textContent).not.toContain(PLACEHOLDER_NOTE);
    expect(panel.textContent).not.toContain(NO_URL_NOTE);
  });

  it("gives a hosted deployment with no public URL the note and a quickstart on the marked placeholder", () => {
    state.gateway = { ...GATEWAY, baseUrl: "" };
    renderPage();
    const panel = screen.getByTestId("overview-endpoint");
    expect(panel.textContent).toContain(NO_URL_NOTE);
    const code = panel.querySelector("pre")?.textContent || "";
    expect(code).toContain("https://YOUR_GATEWAY_URL/v1");
    expect(code).not.toMatch(/localhost/i);
    // The Endpoint row never offers the placeholder as the gateway's address.
    expect(within(panel).queryByRole("button", { name: "Copy endpoint" })).toBeNull();
  });

  it.each([
    LOCAL_URL,
    "http://127.0.0.1:8090/v1",
    "http://[::1]:8090/v1",
    "https://YOUR_GATEWAY_URL/v1",
  ])("withholds the hosted endpoint and builds the quickstart on the marked placeholder at %s", (baseUrl) => {
    state.gateway = { ...GATEWAY, baseUrl };
    renderPage();
    const panel = screen.getByTestId("overview-endpoint");
    expect(panel.textContent).toContain(NO_URL_NOTE);
    expect(within(panel).queryByRole("button", { name: "Copy endpoint" })).toBeNull();
    expect(panel.querySelector("pre")?.textContent).toContain("https://YOUR_GATEWAY_URL/v1");
    expect(panel.textContent).not.toMatch(/localhost|127\.0\.0\.1|\[::1\]/i);
  });

  it("offers a deployment-mode retry and a quickstart on the marked placeholder while mode is unconfirmed", async () => {
    state.hosted = false;
    state.deploymentModeConfirmed = false;
    renderPage();
    const panel = screen.getByTestId("overview-endpoint");
    expect(panel.textContent).toContain(NO_URL_NOTE);
    // Said once: the note, not the panel's description as well.
    expect(panel.textContent.split(NO_URL_NOTE).length - 1).toBe(1);
    expect(panel.querySelector("pre")?.textContent).toContain("https://YOUR_GATEWAY_URL/v1");
    expect(
      within(panel).getByText(PLACEHOLDER_URL, { selector: "code" }),
    ).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Copy endpoint" })).toBeNull();
    await userEvent.click(
      within(panel).getByRole("button", { name: "Retry deployment mode" }),
    );
    expect(state.retryDeploymentMode).toHaveBeenCalledTimes(1);
  });

  // Prefer measured provider health without mistaking it for model compatibility.
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
    expect(endpoint.getByText(/"model": "openai\/gpt-4o"/)).toBeInTheDocument();
    expect(endpoint.queryByText(/walk-flaky-model/)).toBeNull();
  });

  it("shows setup instead of a placeholder when no provider is confirmed usable", () => {
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
    expect(endpoint.queryByText(/YOUR_MODEL/)).toBeNull();
    expect(endpoint.getByText(/No usable configured model is selected/)).toBeInTheDocument();
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
        requests: null,
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
    state.hosted = true;
    state.deploymentModeConfirmed = true;
    state.retryDeploymentMode.mockReset();
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

  it("keeps Try a model aligned with the selected qualified request target", async () => {
    state.providers = [
      { id: "my-vllm", name: "my-vllm", pushed: true, models: ["bge-embed", "qwen-chat"] },
      ...PROVIDERS,
    ];
    renderPage();
    const start = within(screen.getByTestId("overview-getting-started"));
    expect(start.getByRole("link", { name: "Try a model" })).toHaveAttribute(
      "href", "/dashboard/gateway/models/openai/gpt-4o?tab=try",
    );
    const endpoint = within(screen.getByTestId("overview-endpoint"));
    await userEvent.selectOptions(endpoint.getByRole("combobox", { name: "Configured model" }), "my-vllm/qwen-chat");
    expect(start.getByRole("link", { name: "Try a model" })).toHaveAttribute(
      "href", "/dashboard/gateway/models/my-vllm/qwen-chat?tab=try",
    );
    expect(endpoint.getByText(/"model": "my-vllm\/qwen-chat"/)).toBeInTheDocument();
  });

  it("offers no Try a model when no provider serves a model", () => {
    state.providers = [{ id: "my-vllm", name: "my-vllm", models: [] }];
    renderPage();
    const start = within(screen.getByTestId("overview-getting-started"));
    expect(start.queryByRole("link", { name: "Try a model" })).toBeNull();
  });

  // The Try it tab runs only where the gateway says it serves Try it
  // (delegated_playground_available true). Anywhere else Try a model stays
  // in the step, disabled, with what works instead beside it.
  it.each([
    ["false", false],
    ["not reported", undefined],
  ])(
    "shows Try a model disabled with what works instead when Try it is %s",
    (_label, available) => {
      state.providers = PROVIDERS;
      state.gateway = { ...GATEWAY, delegatedPlaygroundAvailable: available };
      renderPage();
      const start = within(screen.getByTestId("overview-getting-started"));
      expect(start.queryByRole("link", { name: "Try a model" })).toBeNull();
      const tryModel = start.getByRole("button", { name: "Try a model" });
      expect(tryModel).toBeDisabled();
      expect(tryModel).toHaveAccessibleDescription(PROVIDER_KEY_SENTENCE);
      expect(start.getByText(PROVIDER_KEY_SENTENCE)).toBeVisible();
      // The step points at the snippet, not at a console run.
      expect(start.queryByText(/try a model in the console/)).toBeNull();
      expect(
        start.getByText(/run it with your personal API key/),
      ).toBeInTheDocument();
    },
  );

  it("names a gateway API key on Future AGI credits when credits are on and Try it is off", () => {
    state.providers = PROVIDERS;
    state.managedStatus = { data: { available: true, enabled: true } };
    state.gateway = { ...GATEWAY, delegatedPlaygroundAvailable: false };
    renderPage();
    const start = within(screen.getByTestId("overview-getting-started"));
    expect(start.queryByRole("link", { name: "Try a model" })).toBeNull();
    const tryModel = start.getByRole("button", { name: "Try a model" });
    expect(tryModel).toBeDisabled();
    expect(tryModel).toHaveAccessibleDescription(API_KEY_SENTENCE);
  });

  it("gives a member the Try it reason, not the role reason, when Try it is off", () => {
    state.orgLevel = 3;
    state.providers = PROVIDERS;
    state.gateway = { ...GATEWAY, delegatedPlaygroundAvailable: false };
    renderPage();
    const start = within(screen.getByTestId("overview-getting-started"));
    expect(
      start.getByRole("button", { name: "Try a model" }),
    ).toHaveAccessibleDescription(PROVIDER_KEY_SENTENCE);
    expect(
      start.queryByText(
        "Only an organization Owner or Admin can run models here.",
      ),
    ).toBeNull();
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
