// A model id two of the org's providers list, on the Providers page.
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { ThemeProvider } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProviderManagementSection from "../ProviderManagementSection";
import { faTestTheme } from "../../__tests__/faTheme";

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
  useProviderHealth: () => ({
    data: state.health,
    isLoading: false,
    ...state.healthQuery,
  }),
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

// Two providers list gpt-4o (openai and flaky-upstream, CONFIG above). The
// page says which provider a plain "gpt-4o" reaches and how to pick one.
describe("Providers: a model id more than one provider lists", () => {
  it("names the providers, where a plain id goes and how to pick", async () => {
    renderPage();
    const notice = await screen.findByTestId("duplicate-model-notice");
    expect(notice).toHaveTextContent(
      '"gpt-4o" is listed by flaky-upstream and openai.',
    );
    expect(notice).toHaveTextContent(
      'A plain "gpt-4o" reaches flaky-upstream, the provider whose id sorts first.',
    );
    expect(notice).toHaveTextContent(
      'To pick a provider, send "<provider>/<model>" (for example "openai/gpt-4o"), or add an entry under Gateway settings › Org config › Model Map.',
    );
    // Both providers stay listed.
    expect(
      screen.getAllByText(/OpenAI \(local mock LLM\)|Flaky upstream/).length,
    ).toBeGreaterThan(0);
  });

  it("follows the org's model map entry", async () => {
    state.config = { ...CONFIG, model_map: { "gpt-4o": "openai" } };
    renderPage();
    expect(
      await screen.findByTestId("duplicate-model-notice"),
    ).toHaveTextContent(
      'Your Model Map entry sends a plain "gpt-4o" to openai.',
    );
  });

  it("leaves out a provider the gateway does not have", async () => {
    state.health = {
      providers: [
        HEALTH.providers[0],
        { ...HEALTH.providers[1], pushed: false, status_reason: "not pushed" },
      ],
    };
    renderPage();
    await screen.findAllByText(/OpenAI \(local mock LLM\)/);
    expect(screen.queryByTestId("duplicate-model-notice")).toBeNull();
  });

  it("says nothing when every id is listed once", async () => {
    state.config = {
      ...CONFIG,
      providers: {
        openai: CONFIG.providers.openai,
        "flaky-upstream": {
          ...CONFIG.providers["flaky-upstream"],
          models: ["other"],
        },
      },
    };
    renderPage();
    await screen.findAllByText(/OpenAI \(local mock LLM\)/);
    expect(screen.queryByTestId("duplicate-model-notice")).toBeNull();
  });
});
