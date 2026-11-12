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
const matrix = Array.from({ length: 8 }, (_, bits) => [
  !!(bits & 4),
  !!(bits & 2),
  !!(bits & 1),
]);
describe("W07 catalogue UI", () => {
  it.each(matrix)(
    "catalog=%s vault=%s virtual=%s keeps Custom MCP and independently enabled accounts",
    async (catalog, vault, virtual) => {
      state.current = {
        data: {
          enabled: true,
          features: {
            catalog,
            credential_vault: vault,
            virtual_servers: virtual,
            upstream_oauth: true,
          },
        },
      };
      render(tree());
      expect(
        screen.getByRole("link", { name: "Custom MCP server" }),
      ).toBeTruthy();
      expect(screen.getByText("personal static account")).toBeTruthy();
      if (catalog) {
        await waitFor(() => expect(api.get).toHaveBeenCalledTimes(1));
        expect(screen.getByTestId("catalogue-drawer").textContent).toBe(
          vault && virtual ? "connect enabled" : "connect unavailable",
        );
      } else {
        expect(api.get).not.toHaveBeenCalled();
        expect(screen.queryByTestId("catalogue-drawer")).toBeNull();
        expect(screen.queryByTestId("catalogue-grid")).toBeNull();
        expect(screen.queryByText("catalogue filters")).toBeNull();
        expect(
          screen.getByText("Not available in this deployment"),
        ).toBeTruthy();
        expect(
          screen.queryByText(
            "The MCP catalog is off or its status is unavailable.",
          ),
        ).toBeNull();
      }
      expect(api.post).not.toHaveBeenCalled();
    },
  );
  // A failed status read keeps the drawer,
  // with connecting held until the status answers again.
  it("cached ON keeps the drawer after status failure, connect held", () => {
    state.current = { data: { enabled: true, features: { catalog: true } } };
    const view = render(tree());
    expect(screen.getByTestId("catalogue-drawer")).toBeTruthy();
    state.current.isError = true;
    view.rerender(tree());
    expect(screen.getByTestId("catalogue-drawer").textContent).toBe(
      "connect unavailable",
    );
    expect(
      screen.queryByText(
        "The MCP catalog is off or its status is unavailable.",
      ),
    ).toBeNull();
    expect(
      screen.getByRole("link", { name: "Custom MCP server" }),
    ).toBeTruthy();
  });
});
