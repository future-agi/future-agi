import React, { forwardRef } from "react";
import { HelmetProvider } from "react-helmet-async";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import GatewayOverviewPage from "./overview/GatewayOverviewPage";

const state = vi.hoisted(() => ({
  gateway: null,
  hosted: true,
  deploymentModeConfirmed: true,
}));
vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual("@tanstack/react-query");
  return {
    ...actual,
    useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  };
});
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(_props, _ref) {
    return <div data-testid="ag-grid" />;
  }),
}));
vi.mock("./context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gateway: state.gateway,
    gatewayId: "default",
    hosted: state.hosted,
    deploymentModeConfirmed: state.deploymentModeConfirmed,
    isLoading: false,
    refreshGateways: vi.fn(),
  }),
}));
// One pushed provider measured healthy, so the Endpoint card builds its
// first-request example for the snippet checks below.
vi.mock("./providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: null }),
  useProviderHealth: () => ({
    data: {
      providers: [
        {
          id: "openai",
          name: "openai",
          healthy: true,
          pushed: true,
          status: "healthy",
          models: ["gpt-4o"],
          request_count: 391,
          error_rate: 0,
        },
      ],
    },
  }),
}));
vi.mock("./analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: { total_requests: 0 } }),
}));
vi.mock("./keys/hooks/useApiKeys", () => ({
  useApiKeys: () => ({ data: [], isLoading: false }),
}));
vi.mock("./providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null }),
}));
vi.mock("./onboarding/useRequestCount", () => ({
  useRequestCount: () => ({ data: 0, isLoading: false }),
}));
vi.mock("./models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => ({
    data: { available: false, enabled: false },
    isLoading: false,
  }),
  useManagedCatalog: () => ({ data: undefined, isLoading: false }),
}));
vi.mock("./hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("./overview/GatewaySnippetKey", () => ({
  default: ({ children }) => children(""),
}));
vi.mock("./onboarding/GettingStarted", () => ({ default: () => null }));

function renderPage() {
  return render(
    <HelmetProvider>
      <GatewayOverviewPage />
    </HelmetProvider>,
  );
}

beforeEach(() => {
  state.hosted = true;
  state.deploymentModeConfirmed = true;
  state.gateway = {
    id: "default",
    name: "Gateway",
    baseUrl: "",
    status: "unconfigured",
  };
});

describe("gateway public endpoint on the live overview page", () => {
  it("shows an explicit missing state without a copy action, any request on the marked placeholder", () => {
    renderPage();
    const endpoint = screen.getByTestId("overview-endpoint");
    // The Endpoint row shows the marked placeholder; the note says why.
    expect(
      within(endpoint).getByText("https://YOUR_GATEWAY_URL/v1", {
        selector: "code",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Gateway URL not configured")).toBeNull();
    expect(
      screen.queryByRole("button", { name: /copy endpoint/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Send your first request"),
    ).not.toBeInTheDocument();
    // QuickstartCode renders the real pre; a request shown here is built on
    // the marked placeholder, and the note says to replace it.
    const code = endpoint.querySelector("pre");
    if (code) expect(code).toHaveTextContent("https://YOUR_GATEWAY_URL/v1");
    expect(endpoint.textContent).toContain(
      "isn't available from the console right now",
    );
    expect(document.body.textContent).not.toContain("localhost");
    expect(document.body.textContent).not.toContain(
      "https://gateway.futureagi.com/v1",
    );
  });

  // Both original configured-address cases. A hosted public address is the
  // endpoint, with its copy action. A self-hosted deployment's local address
  // is never shown as the address to send requests to: the Endpoint row shows
  // the marked placeholder with no copy action, as its request examples do.
  // Both recipes are built on that address: the request example (the healthy
  // provider's model is chosen) and the model-discovery recipe.
  it.each([
    {
      url: "https://gateway.example.com/v1",
      hosted: true,
      snippetUrl: "https://gateway.example.com/v1",
      endpointUrl: "https://gateway.example.com/v1",
      copy: true,
    },
    {
      url: "http://localhost:8090/v1",
      hosted: false,
      snippetUrl: "https://YOUR_GATEWAY_URL/v1",
      endpointUrl: "https://YOUR_GATEWAY_URL/v1",
      copy: false,
    },
  ])(
    "keeps the configured endpoint and mode-appropriate snippet for $url",
    ({ url, hosted, snippetUrl, endpointUrl, copy }) => {
      state.hosted = hosted;
      state.gateway = { ...state.gateway, baseUrl: url, status: "healthy" };
      renderPage();
      const endpoint = screen.getByTestId("overview-endpoint");
      expect(
        within(endpoint).getByText(endpointUrl, { selector: "code" }),
      ).toBeInTheDocument();
      const request = endpoint.querySelector("pre");
      expect(request).not.toBeNull();
      expect(request).toHaveTextContent(snippetUrl);
      fireEvent.click(
        within(endpoint).getByRole("button", { name: "Discover models" }),
      );
      const discovery = endpoint.querySelector("pre");
      expect(discovery).not.toBeNull();
      expect(discovery).toHaveTextContent(snippetUrl);
      const copyButton = within(endpoint).queryByRole("button", {
        name: /copy endpoint/i,
      });
      if (copy) expect(copyButton).toBeInTheDocument();
      else expect(copyButton).toBeNull();
      expect(
        screen.queryByText("Gateway URL not configured"),
      ).not.toBeInTheDocument();
      if (!hosted) expect(endpoint.textContent).not.toContain(url);
    },
  );

  it("does not treat a hosted localhost address as a configured public endpoint", () => {
    state.gateway = {
      ...state.gateway,
      baseUrl: "http://localhost:8090/v1",
      status: "healthy",
    };
    renderPage();
    const endpoint = screen.getByTestId("overview-endpoint");
    expect(
      within(endpoint).getByText("https://YOUR_GATEWAY_URL/v1", {
        selector: "code",
      }),
    ).toBeInTheDocument();
    expect(
      within(endpoint).queryByRole("button", { name: /copy endpoint/i }),
    ).not.toBeInTheDocument();
    const code = endpoint.querySelector("pre");
    if (code) expect(code).toHaveTextContent("https://YOUR_GATEWAY_URL/v1");
    expect(endpoint.textContent).not.toMatch(/localhost/);
  });

  it("withholds the endpoint until the deployment mode is confirmed; any request uses the marked placeholder", () => {
    state.deploymentModeConfirmed = false;
    state.gateway = {
      ...state.gateway,
      baseUrl: "https://gateway.example.com/v1",
      status: "healthy",
    };
    renderPage();
    const endpoint = screen.getByTestId("overview-endpoint");
    expect(
      within(endpoint).queryByRole("button", { name: /copy endpoint/i }),
    ).not.toBeInTheDocument();
    const code = endpoint.querySelector("pre");
    if (code) expect(code).toHaveTextContent("https://YOUR_GATEWAY_URL/v1");
    expect(endpoint.textContent).not.toContain(state.gateway.baseUrl);
  });
});
