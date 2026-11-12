import React, { forwardRef } from "react";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import GatewayOverviewPage from "../GatewayOverviewPage";

// An organization on Future AGI credits with no own
// provider sees step 1 done, so the Endpoint card's First request must offer
// the credit-eligible chat models instead of asking for a provider.

vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(_props, _ref) {
    return <div data-testid="ag-grid" />;
  }),
}));

const state = vi.hoisted(() => ({
  providers: [],
  managedStatus: { data: undefined },
  catalog: { data: undefined },
  keys: [],
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
  useManagedCatalog: () => state.catalog,
}));

// R-LIT synthetic: a visibly fake public gateway URL (RFC 2606 .test host).
const PUBLIC_URL = "https://gateway.example.test/v1";
// The gateway serves Try it, so Try a model is offered as a link.
const GATEWAY = {
  id: "default",
  name: "Gateway",
  baseUrl: PUBLIC_URL,
  status: "healthy",
  delegatedPlaygroundAvailable: true,
};

vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gateway: GATEWAY,
    gatewayId: GATEWAY.id,
    hosted: true,
    deploymentModeConfirmed: true,
    retryDeploymentMode: vi.fn(),
    isLoading: false,
    error: null,
    refreshGateways: vi.fn(),
  }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: null, isLoading: false }),
  useProviderHealth: () => ({ data: { providers: state.providers }, isLoading: false }),
}));
vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: null, isLoading: false }),
}));
vi.mock("../../keys/hooks/useApiKeys", () => ({
  useApiKeys: () => ({ data: state.keys, isLoading: false }),
}));
vi.mock("../../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null, isLoading: false }),
}));
vi.mock("../../onboarding/useRequestCount", () => ({
  useRequestCount: () => ({ data: 0, isLoading: false }),
}));

// A sellable chat model, an image model and an unpriced chat model: only the
// first can run on Future AGI credits (catalogRows.js managedVerdict).
const CREDIT_CHAT = {
  id: "openai/gpt-4o-mini",
  vendor: "openai",
  provider: "openrouter",
  output_modalities: ["text"],
  state: "active",
  billed_input_per_mtok: 0.16,
  billed_output_per_mtok: 0.63,
};
const IMAGE_MODEL = {
  id: "openai/image-fixture",
  vendor: "openai",
  provider: "openrouter",
  output_modalities: ["image"],
  state: "active",
  billed_input_per_mtok: 1,
  billed_output_per_mtok: 1,
};
const UNPRICED_CHAT = {
  id: "openai/unpriced-fixture",
  vendor: "openai",
  provider: "openrouter",
  output_modalities: ["text"],
  state: "active",
  unpriced: true,
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <HelmetProvider>
        <GatewayOverviewPage />
      </HelmetProvider>
    </QueryClientProvider>,
  );
}

describe("credit-only first request", () => {
  beforeEach(() => {
    state.providers = [];
    state.keys = [{ id: "k1", status: "active" }];
    state.orgLevel = 8;
    state.managedStatus = {
      data: { available: true, enabled: true, balance_usd: "10" },
    };
    state.catalog = { data: { models: [IMAGE_MODEL, CREDIT_CHAT, UNPRICED_CHAT] } };
    http.post.mockReset();
    try {
      window.localStorage.clear();
    } catch {
      // storage may be unavailable
    }
  });

  it("offers credit-eligible chat models and builds the request and Try a model from the chosen one", async () => {
    const user = userEvent.setup();
    renderPage();
    const setup = within(screen.getByTestId("overview-getting-started"));
    expect(
      setup.getByRole("listitem", {
        name: "Add a provider or use Future AGI credits: Complete",
      }),
    ).toBeInTheDocument();

    const panel = within(screen.getByTestId("overview-endpoint"));
    expect(panel.queryByText(/Add and apply a provider/)).toBeNull();
    const select = panel.getByRole("combobox", { name: "Configured model" });
    const options = within(select).getAllByRole("option");
    expect(options.map((option) => option.value)).toEqual(["", CREDIT_CHAT.id]);
    expect(options[1]).toHaveTextContent(`${CREDIT_CHAT.id} — Future AGI credits`);

    await user.selectOptions(select, CREDIT_CHAT.id);
    expect(panel.getByText(/Runs on Future AGI credits/)).toBeInTheDocument();
    expect(
      screen.getByTestId("overview-endpoint").querySelector("pre").textContent,
    ).toContain(`"model": "${CREDIT_CHAT.id}"`);
    expect(setup.getByRole("link", { name: "Try a model" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/models/openai/gpt-4o-mini?tab=try",
    );
    expect(http.post).not.toHaveBeenCalled();
  });

  it("does not tell a credit user to add a provider while the catalog lists no eligible model", () => {
    state.catalog = { data: { models: [IMAGE_MODEL, UNPRICED_CHAT] } };
    renderPage();
    const panel = within(screen.getByTestId("overview-endpoint"));
    expect(panel.queryByRole("combobox", { name: "Configured model" })).toBeNull();
    expect(panel.queryByText(/No usable configured model is selected/)).toBeNull();
    expect(panel.getByText(/Credit-eligible models could not be listed/)).toBeInTheDocument();
    expect(panel.getByRole("link", { name: "Browse models" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/models",
    );
  });
});
