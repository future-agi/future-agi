import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProviderManagementSection from "../ProviderManagementSection";
import { faTestTheme } from "../../__tests__/faTheme";
import { expectAdminOnly, expectEditable } from "../../testing/adminOnly";

const state = vi.hoisted(() => ({
  role: "Owner",
  orgLevel: 15,
  config: null,
  configError: null,
  configLoading: false,
  health: null,
  credits: null,
  reloadResult: null,
  removeResult: null,
  refetch: vi.fn(),
  reload: vi.fn(),
  remove: vi.fn(),
  snackbars: [],
  billingEnabled: [],
}));

vi.mock("ag-grid-react", async () => await import("./grid-mock"));

vi.mock("../hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: state.configError || state.configLoading ? undefined : state.config,
    isLoading: state.configLoading,
    isError: !!state.configError,
    error: state.configError,
    refetch: state.refetch,
  }),
  useProviderHealth: () => ({ data: state.health, isLoading: false, ...state.healthQuery }),
  useReloadConfig: () => ({
    mutate: (id, opts) => {
      state.reload(id);
      opts?.onSuccess?.(state.reloadResult);
    },
    isPending: false,
  }),
  useRemoveProvider: () => ({
    mutate: (vars, opts) => {
      state.remove(vars);
      opts?.onSuccess?.(state.removeResult);
    },
    isPending: false,
  }),
  useUpdateProvider: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useFetchProviderModels: () => ({ mutate: vi.fn(), isPending: false }),
  gatewayConfigRefreshFailed: () => false,
  isSavedButNotApplied: () => false,
}));

vi.mock("../hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null, isLoading: false }),
  useCreateOrgConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

const gatewayContext = vi.hoisted(() => ({ managedOffered: true }));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    gateway: { id: "default", baseUrl: "http://localhost:28490/v1" },
    isLoading: false,
    managedOffered: gatewayContext.managedOffered,
  }),
}));

vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: undefined }),
}));

vi.mock("../../settings/OrgConfigEditor", () => ({ default: () => null }));

// The write controls read the org level (useGatewayAdmin): Owner 15,
// Admin 8, Member 3, Viewer 1.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: state.orgLevel }),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    role: state.role,
    user: { organization_role: state.role },
  }),
}));

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));

vi.mock("src/sections/settings/BillingV2/useCentralBilling", () => ({
  useCentralBilling: ({ enabled } = {}) => {
    state.billingEnabled.push(enabled);
    return {
      credits: enabled ? state.credits : null,
      creditsAvailable: enabled && !!state.credits,
      visible: enabled && !!state.credits,
      isLoading: false,
    };
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) =>
    state.snackbars.push({ message, variant: options?.variant }),
}));

const CONFIG = {
  routing: {},
  providers: {
    openai: {
      id: "c1",
      name: "openai",
      display_name: "OpenAI (local mock LLM)",
      base_url: "http://mock-llm:8080",
      api_format: "openai",
      api_path_prefix: "/v1",
      models: ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
      default_timeout: 60,
      max_concurrent: 100,
      conn_pool_size: 100,
    },
    "flaky-upstream": {
      id: "c2",
      name: "flaky-upstream",
      display_name: "Flaky upstream (local mock, fails 60%)",
      base_url: "http://demo-upstream:8080/flaky",
      api_format: "openai",
      api_path_prefix: "/v1",
      models: ["gpt-4o"],
      default_timeout: 30,
      max_concurrent: 10,
      conn_pool_size: 100,
    },
  },
};

const HEALTH = {
  providers: [
    {
      id: "openai",
      status: "healthy",
      healthy: true,
      request_count: 391,
      avg_latency: 33.22,
      error_rate: 11.0,
    },
    {
      id: "flaky-upstream",
      status: "degraded",
      healthy: true,
      status_reason: "20.4% of 162 requests failed in 24 h",
      request_count: 162,
      avg_latency: 334.16,
      error_rate: 20.37,
    },
  ],
};

function renderPage(path = "/dashboard/gateway/providers", mode = "light") {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <HelmetProvider>
        <ThemeProvider theme={faTestTheme(mode)}>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route
                path="/dashboard/gateway/providers"
                element={<ProviderManagementSection />}
              />
              <Route
                path="/dashboard/gateway/providers/:tab"
                element={<ProviderManagementSection />}
              />
              <Route path="*" element={<div>elsewhere</div>} />
            </Routes>
          </MemoryRouter>
        </ThemeProvider>
      </HelmetProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  state.role = "Owner";
  state.orgLevel = 15;
  state.config = CONFIG;
  state.configError = null;
  state.configLoading = false;
  state.health = HEALTH;
  state.healthQuery = {};
  state.credits = { available_usd: 34.999336 };
  state.reloadResult = { status: "ok", gateway_synced: true };
  state.removeResult = { gateway_synced: true };
  state.refetch.mockReset();
  state.reload.mockReset();
  state.remove.mockReset();
  state.snackbars.length = 0;
  state.billingEnabled.length = 0;
  gatewayContext.managedOffered = true;
});

describe("Providers page", () => {
  it.each([null, HEALTH])("R3-KEYS-ONBOARDING-07 hides unavailable or stale statistics without hiding saved providers (%j)", async (data) => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    state.health = data;
    state.healthQuery = { isError: true, refetch };
    renderPage();
    expect(screen.getAllByTestId("ag-row")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Edit provider openai" })).toBeEnabled();
    const row = screen.getAllByTestId("ag-row")[0];
    expect(within(row).getByText("Unknown")).toBeInTheDocument();
    expect(within(row).getAllByText("—")).toHaveLength(3);
    expect(within(row).queryByText("391")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Retry statistics" }));
    expect(refetch).toHaveBeenCalledOnce();
    expect(state.reload).not.toHaveBeenCalled();
    await user.click(row);
    const drawer = within(await screen.findByRole("dialog"));
    expect(drawer.getByText("Requests").parentElement).toHaveTextContent("Requests—");
    expect(drawer.getByText("Unknown")).toBeInTheDocument();
  });

  it("R3-KEYS-ONBOARDING-07 distinguishes a pending stats read from a confirmed zero", () => {
    state.health = undefined;
    state.healthQuery = { isPending: true };
    const { unmount } = renderPage();
    expect(screen.getByText("Loading provider statistics…")).toBeInTheDocument();
    expect(within(screen.getAllByTestId("ag-row")[0]).getAllByText("—")).toHaveLength(3);
    unmount();
    state.healthQuery = {};
    state.health = { providers: [{ id: "openai", request_count: 0, status: "healthy" }] };
    renderPage();
    const row = within(screen.getAllByTestId("ag-row")[0]);
    expect(row.getByText("0")).toBeInTheDocument();
    expect(row.getByText("Too few requests")).toBeInTheDocument();
  });
  it.each([undefined, "cache"])(
    "keeps the Cache tab and one off-state notice at tab %s",
    (tab) => {
      state.config = { ...CONFIG, org_sections_enforced: ["routing"] };
      renderPage(`/dashboard/gateway/providers${tab ? `/${tab}` : ""}`);
      expect(screen.getByRole("tab", { name: "Cache" })).toBeVisible();
      expect(
        screen.getByText("Response caching is off on this gateway."),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Configure cache" }),
      ).toBeNull();
      expect(screen.queryByRole("button", { name: "Enable cache" })).toBeNull();
    },
  );

  it.each([
    { cache: { enabled: false } },
    { cache: { enabled: false }, org_sections_enforced: ["cache"] },
    {
      org_sections_enforced: ["cache"],
      org_sections_not_enforced: { cache: "disabled by node" },
    },
  ])(
    "honors explicit negative cache reports on old cache URLs: %j",
    (config) => {
      state.config = { ...CONFIG, ...config };
      renderPage("/dashboard/gateway/providers/cache");
      expect(screen.getByRole("tab", { name: "Cache" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      expect(
        screen.getByText("Response caching is off on this gateway."),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Configure cache" }),
      ).toBeNull();
      expect(screen.queryByRole("button", { name: "Enable cache" })).toBeNull();
    },
  );

  it("offers Cache when the node reports it enforced", () => {
    state.config = { ...CONFIG, org_sections_enforced: ["cache"] };
    renderPage();
    expect(screen.getByRole("tab", { name: "Cache" })).toBeInTheDocument();
  });
  // The deployment's managed state (GatewayContext) decides whether the
  // status rule names managed models.
  it("names no managed models in the status rule on a deployment that offers none", () => {
    gatewayContext.managedOffered = false;
    renderPage();
    const rule = screen.getByText(
      /Status comes from your organization's own requests/,
    );
    expect(rule).not.toHaveTextContent(/managed/i);
    expect(rule).toHaveTextContent("Guardrail blocks are not counted.");
  });

  it("is one FA page: an 18 px semibold title over one provider list", () => {
    renderPage();
    const title = screen.getByRole("heading", { level: 1, name: "Providers" });
    expect(window.getComputedStyle(title).fontSize).toBe("0.9375rem");
    expect(window.getComputedStyle(title).fontWeight).toBe("600");
    expect(screen.getAllByTestId("ag-row")).toHaveLength(2);
    expect(screen.getByRole("tab", { name: "Providers" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Routing" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Cache" })).toBeInTheDocument();
    // The old health and config tabs were the same list twice.
    expect(screen.queryByRole("tab", { name: /Provider Health/ })).toBeNull();
    expect(screen.queryByRole("tab", { name: /Provider Config/ })).toBeNull();
  });

  it("shows each provider's real 24 h numbers and the status they give", () => {
    renderPage();
    const flaky = screen
      .getAllByTestId("ag-row")
      .find((row) => within(row).queryByText(/Flaky upstream/));
    expect(within(flaky).getByText("162")).toBeInTheDocument();
    expect(within(flaky).getByText("334 ms")).toBeInTheDocument();
    expect(within(flaky).getByText("20.4%")).toBeInTheDocument();
    expect(
      within(flaky).getByText("demo-upstream:8080/flaky"),
    ).toBeInTheDocument();
    // The status comes from the org's own requests in 24 h, which
    // the providers read derives; the operator's breaker is never shown.
    expect(within(flaky).getByText("Degraded")).toBeInTheDocument();
    expect(
      within(flaky).getByText("20.4% of 162 requests failed in 24 h"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/last 24 h, from your own requests/i),
    ).toBeInTheDocument();
  });

  it("keeps the old health and config URLs on the one list", () => {
    renderPage("/dashboard/gateway/providers/config");
    expect(screen.getAllByTestId("ag-row")).toHaveLength(2);
    expect(screen.getByRole("tab", { name: "Providers" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("opens a provider's detail with its connection, models and stored key", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(
      screen
        .getAllByTestId("ag-row")
        .find((row) => within(row).queryByText(/OpenAI \(local/)),
    );
    const drawer = await screen.findByRole("dialog");
    expect(
      within(drawer).getByText("http://mock-llm:8080"),
    ).toBeInTheDocument();
    expect(within(drawer).getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(
      within(drawer).getByText(/Stored encrypted\. Never shown again\./),
    ).toBeInTheDocument();
    // Analytics reads no provider parameter, so the link
    // opens it as it is and does not claim a filter.
    expect(
      within(drawer).queryByRole("link", { name: /View in analytics/ }),
    ).toBeNull();
    expect(
      within(drawer).getByRole("link", { name: /Open Analytics/ }),
    ).toHaveAttribute("href", "/dashboard/gateway/analytics");
  });

  it("R3-KEYS-ONBOARDING-09 links provider requests with exact identity, a bounded 24-hour window and an errors variant", async () => {
    const user = userEvent.setup();
    const before = Date.now();
    renderPage();
    await user.click(screen.getAllByTestId("ag-row").find((row) => within(row).queryByText(/Flaky upstream/)));
    const drawer = within(await screen.findByRole("dialog"));
    const all = new URL(drawer.getByRole("link", { name: "View provider requests" }).href);
    const errors = new URL(drawer.getByRole("link", { name: "View failed requests" }).href);
    expect(all.pathname).toBe("/dashboard/gateway/logs");
    expect(all.searchParams.get("provider")).toBe("flaky-upstream");
    expect(all.searchParams.has("is_error")).toBe(false);
    expect(errors.searchParams.get("provider")).toBe("flaky-upstream");
    expect(errors.searchParams.get("is_error")).toBe("true");
    const end = Date.parse(all.searchParams.get("started_before"));
    const start = Date.parse(all.searchParams.get("started_after"));
    expect(end - start).toBe(24 * 60 * 60 * 1000);
    expect(end).toBeGreaterThanOrEqual(before);
    expect(end).toBeLessThanOrEqual(Date.now());
    expect(errors.searchParams.get("started_after")).toBe(all.searchParams.get("started_after"));
    expect(errors.searchParams.get("started_before")).toBe(all.searchParams.get("started_before"));
    expect(drawer.getByRole("link", { name: /Open Analytics/ })).toHaveAttribute("href", "/dashboard/gateway/analytics");
    expect(state.reload).not.toHaveBeenCalled();
    expect(state.remove).not.toHaveBeenCalled();
  });

  // Each model the provider serves opens its model page, where Try it calls
  // it through that provider: the page id is "<provider>/<model>". The page
  // reads the providers list (GET /agentcc/gateways/<id>/providers/), so a
  // model that list does not give the provider has no page and no link.
  it("links each model its provider serves to that model's page", async () => {
    const user = userEvent.setup();
    state.health = {
      providers: [
        { ...HEALTH.providers[0], models: ["gpt-4o", "gpt-4o-mini"] },
        { ...HEALTH.providers[1], models: ["gpt-4o"] },
      ],
    };
    renderPage();
    await user.click(
      screen
        .getAllByTestId("ag-row")
        .find((row) => within(row).queryByText(/OpenAI \(local/)),
    );
    const drawer = await screen.findByRole("dialog");
    expect(
      within(drawer).getByRole("link", { name: "gpt-4o-mini" }),
    ).toHaveAttribute("href", "/dashboard/gateway/models/openai/gpt-4o-mini");
    expect(
      within(drawer).getByRole("link", { name: "gpt-4o" }),
    ).toHaveAttribute("href", "/dashboard/gateway/models/openai/gpt-4o");
    // Listed in the gateway config but not served to this org: shown, with
    // no link.
    expect(within(drawer).getByText("gpt-3.5-turbo")).toBeInTheDocument();
    expect(
      within(drawer).queryByRole("link", { name: "gpt-3.5-turbo" }),
    ).toBeNull();
  });

  it("links no model while the providers list does not name the provider", async () => {
    const user = userEvent.setup();
    state.health = { providers: [] };
    renderPage();
    await user.click(
      screen
        .getAllByTestId("ag-row")
        .find((row) => within(row).queryByText(/OpenAI \(local/)),
    );
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(within(drawer).queryByRole("link", { name: /^gpt-/ })).toBeNull();
  });

  // Purple (color="primary") at the native 38 px, as on
  // Datasets, Keys, Members and Evals; the theme default draws it black.
  it("draws Add provider as the Future AGI primary button at the native size", () => {
    renderPage();
    const cta = screen.getAllByRole("button", { name: "Add provider" })[0];
    expect(cta).toHaveClass("MuiButton-containedPrimary");
    expect(cta).toHaveClass("MuiButton-sizeMedium");
  });

  it("shows the one empty state with Add provider when there is none", () => {
    state.config = { providers: {} };
    state.health = { providers: [] };
    renderPage();
    expect(screen.getByText("No providers yet")).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: "Add provider" }).length,
    ).toBeGreaterThan(0);
  });

  it("says the list could not be loaded, with Retry, when the read fails", async () => {
    const user = userEvent.setup();
    state.configError = new Error("Network Error");
    renderPage();
    expect(
      screen.getByText("Providers could not be loaded"),
    ).toBeInTheDocument();
    expect(screen.queryByText("No providers yet")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(state.refetch).toHaveBeenCalled();
  });

  it("reports a reload the gateway took as done", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "Reload config" }));
    expect(state.snackbars).toEqual([
      { message: "Configuration reloaded", variant: "success" },
    ]);
  });

  // The write controls, Reload config among them, show disabled with
  // "Owner or Admin only" below Admin; every read stays open.
  it("shows a member Add provider, Reload config and row edits disabled", () => {
    state.role = "Member";
    state.orgLevel = 3;
    renderPage();
    expectAdminOnly(screen.getAllByRole("button", { name: "Add provider" })[0]);
    expectAdminOnly(
      screen.getByRole("button", { name: "Edit provider openai" }),
    );
    expectAdminOnly(
      screen.getByRole("button", { name: "Remove provider openai" }),
    );
    expectAdminOnly(screen.getByRole("button", { name: "Reload config" }));
  });

  it("lets an admin add, edit and remove", () => {
    state.role = "Admin";
    state.orgLevel = 8;
    renderPage();
    expectEditable(screen.getAllByRole("button", { name: "Add provider" })[0]);
    expectEditable(
      screen.getByRole("button", { name: "Edit provider openai" }),
    );
    expectEditable(
      screen.getByRole("button", { name: "Remove provider openai" }),
    );
  });

  // Routing and Cache are forms over the stored config: until it is read
  // they show nothing to edit, so no default can pass for a stored value
  // and no edit is wiped when the read lands.
  it.each([
    ["routing", "Failover Configuration"],
    ["cache", "Enable cache"],
  ])("shows no %s form while the gateway config loads", (slug, control) => {
    state.configLoading = true;
    renderPage(`/dashboard/gateway/providers/${slug}`);
    expect(screen.getByTestId("provider-config-loading")).toBeInTheDocument();
    expect(screen.queryByText(control)).toBeNull();
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("says the routing config could not be loaded, with Retry, when the read fails", async () => {
    state.configError = { message: "connection refused" };
    const user = userEvent.setup();
    renderPage("/dashboard/gateway/providers/routing");
    expect(screen.queryByText("Failover Configuration")).toBeNull();
    expect(
      screen.getByText("The gateway config could not be loaded"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(state.refetch).toHaveBeenCalledTimes(1);
  });

  it("shows the stored failover settings once the config is read", () => {
    state.config = {
      ...CONFIG,
      routing: { failover: { enabled: true, max_attempts: 2 } },
    };
    renderPage("/dashboard/gateway/providers/routing");
    expect(screen.getByLabelText("Max Attempts")).toHaveValue(2);
    expect(screen.getByLabelText("Max Attempts")).toBeEnabled();
  });

  it("keeps the Routing and Cache writes Owner or Admin only for a member", async () => {
    state.role = "Member";
    state.orgLevel = 3;
    state.config = { ...CONFIG, cache: {}, org_sections_enforced: ["cache"] };
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("tab", { name: "Cache" }));
    expectAdminOnly(
      await screen.findByRole("button", { name: "Enable cache" }),
    );
    expect(
      screen.getByText(
        "Only an organization owner or admin can change the cache.",
      ),
    ).toBeInTheDocument();
  });

  it("shows the credit balance, linked to central Billing, to a billing role", () => {
    renderPage();
    const chip = screen.getByRole("link", { name: /Credits \$34\.99/ });
    expect(chip).toHaveAttribute(
      "href",
      "/dashboard/settings/billing#ai-gateway-credits",
    );
  });

  it("never reads the credit ledger for a member", () => {
    state.role = "Member";
    state.orgLevel = 3;
    renderPage();
    expect(screen.queryByRole("link", { name: /Credits/ })).toBeNull();
    expect(state.billingEnabled.every((enabled) => enabled === false)).toBe(
      true,
    );
  });

  it("pins the row actions on a desktop but not at phone width", () => {
    const { unmount } = renderPage();
    expect(
      document.querySelector('th[data-col="actions"]').dataset.pinned,
    ).toBe("right");
    unmount();
    const matchMedia = window.matchMedia;
    window.matchMedia = (query) => ({
      matches: query.includes("max-width"),
      media: query,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
    });
    try {
      renderPage();
      expect(
        document.querySelector('th[data-col="actions"]').dataset.pinned,
      ).toBe("");
    } finally {
      window.matchMedia = matchMedia;
    }
  });
});
