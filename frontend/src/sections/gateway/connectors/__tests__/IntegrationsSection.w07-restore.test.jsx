import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import IntegrationsSection from "../IntegrationsSection";

const { state, api } = vi.hoisted(() => ({
  state: { current: {} },
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw" }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("../../mcp/hooks/useMCPConfig", () => ({
  useMCPStatus: () => state.current,
}));
vi.mock("../hooks/useMyConnections", () => ({
  useMyConnections: () => ({ data: [{ server_id: "static" }] }),
}));
vi.mock("../hooks/useOwnOAuthApp", () => ({ ownAppRoutes: () => null }));
vi.mock("../components/IntegrationsFilterBar", () => ({
  default: () => <div>catalogue filters</div>,
}));
vi.mock("../components/IntegrationsGrid", () => ({
  default: ({ apps }) => (
    <div data-testid="catalogue-grid">{apps.length} apps</div>
  ),
}));
vi.mock("../components/AppDetailDrawer", () => ({
  default: ({ canConnect }) => (
    <div data-testid="catalogue-drawer">
      {canConnect ? "connect enabled" : "connect unavailable"}
    </div>
  ),
}));
vi.mock("../components/MyConnections", () => ({
  default: () => <div>personal static account</div>,
}));
vi.mock("src/utils/axios", () => ({
  default: api,
  endpoints: { gateway: { connectorCatalog: "/catalog/" } },
}));
let client;
beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.get.mockResolvedValue({ data: { results: [] } });
});
afterEach(() => {
  cleanup();
  client.clear();
});
function tree() {
  return (
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <IntegrationsSection />
      </QueryClientProvider>
    </HelmetProvider>
  );
}
const OFF_SENTENCE = "The MCP catalog is off or its status is unavailable.";
const NOT_AVAILABLE = "Not available in this deployment";
const ON = { catalog: true, credential_vault: true, virtual_servers: true };

// The grid, filter bar and drawer stay while
// mcp-status loads, fails or reports an unreachable gateway, as v1.56 did.
// Only a catalog switch the status carries as false replaces them, with the
// deployment's "Not available" line.
describe("Integrations stays while MCP status loads, fails or a switch is off", () => {
  it("loading: shows the grid's loading state, never the off sentence", () => {
    state.current = { isLoading: true, isPending: true };
    render(tree());
    expect(screen.queryByText(OFF_SENTENCE)).toBeNull();
    expect(screen.getByTestId("catalogue-grid")).toBeTruthy();
    expect(screen.getByTestId("catalogue-drawer")).toBeTruthy();
  });

  it("status read error: still reads the catalog and keeps the drawer", async () => {
    state.current = { isError: true };
    render(tree());
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(OFF_SENTENCE)).toBeNull();
    expect(screen.getByTestId("catalogue-drawer")).toBeTruthy();
  });

  it("gateway unreachable: reads the switches from features", async () => {
    state.current = {
      data: { enabled: false, reason: "gateway_unreachable", features: ON },
    };
    render(tree());
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(OFF_SENTENCE)).toBeNull();
    expect(screen.getByTestId("catalogue-drawer").textContent).toBe(
      "connect enabled",
    );
  });

  it("catalog switch off: says Not available in this deployment", () => {
    state.current = {
      data: { enabled: true, features: { ...ON, catalog: false } },
    };
    render(tree());
    expect(screen.getByText(NOT_AVAILABLE)).toBeTruthy();
    expect(screen.queryByText(OFF_SENTENCE)).toBeNull();
    expect(screen.queryByTestId("catalogue-grid")).toBeNull();
    expect(api.get).not.toHaveBeenCalled();
    expect(
      screen.getByRole("link", { name: "Custom MCP server" }),
    ).toBeTruthy();
  });

  it("healthy: reads the catalog with connect enabled", async () => {
    state.current = { data: { enabled: true, features: ON } };
    render(tree());
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("catalogue-drawer").textContent).toBe(
      "connect enabled",
    );
    expect(screen.queryByText(NOT_AVAILABLE)).toBeNull();
  });
});
