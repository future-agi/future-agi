import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import IntegrationDiscoveryPanel from "./IntegrationDiscoveryPanel";

const result = {
  available: true,
  connectors: [
    {
      id: "github",
      name: "GitHub",
      provider: "GitHub",
      auth: "oauth",
      status: "installed",
    },
    {
      id: "gmail",
      name: "Gmail",
      provider: "Google",
      auth: "oauth",
      status: "available",
    },
  ],
};

function client() {
  return {
    listWorkforce: vi
      .fn()
      .mockResolvedValue({
        available: true,
        capabilities: { role: "owner", permittedActions: ["view", "update"] },
        agents: [],
        connectorBindings: [],
      }),
    getConnector: vi
      .fn()
      .mockResolvedValue({
        available: true,
        connector: {
          id: "github",
          name: "GitHub",
          provider: "GitHub",
          auth: "oauth",
        },
      }),
    listConnectorSources: vi
      .fn()
      .mockResolvedValue({ available: true, sources: [] }),
    listConnectorSyncs: vi
      .fn()
      .mockResolvedValue({ available: true, syncs: [] }),
    listConnectorSyncSchedules: vi
      .fn()
      .mockResolvedValue({ available: true, schedules: [] }),
    getConnectorOAuth: vi
      .fn()
      .mockResolvedValue({
        available: true,
        connectorId: "github",
        auth: "oauth",
        configured: false,
        bindings: [],
      }),
  };
}

describe("IntegrationDiscoveryPanel", () => {
  it("searches and filters the authoritative rows, then delegates selected details", async () => {
    const api = client();
    render(<IntegrationDiscoveryPanel result={result} client={api} />);
    expect(screen.getByText("Provider coverage")).toBeInTheDocument();
    expect(screen.getAllByText("GitHub").length).toBeGreaterThan(0);
    expect(screen.getByText("Connected")).toBeInTheDocument();
    await userEvent.type(
      screen.getByRole("textbox", { name: "Search integrations" }),
      "gmail",
    );
    const availableList = screen.getByRole("list", {
      name: "Available server integrations",
    });
    expect(availableList).toHaveTextContent("Gmail");
    expect(availableList).not.toHaveTextContent("GitHub");
    await userEvent.click(
      screen.getAllByRole("button", { name: "Open details" }).at(-1),
    );
    expect(await screen.findByText("Connector metadata")).toBeInTheDocument();
    expect(api.getConnector).toHaveBeenCalledWith("gmail");
  });

  it("never renders a fake Connect action for providers absent from the server catalog", () => {
    render(<IntegrationDiscoveryPanel result={result} client={client()} />);
    expect(
      screen.getAllByText("Not available on this deployment").length,
    ).toBeGreaterThan(0);
    expect(
      screen.queryByRole("button", { name: /^Connect$/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /custom mcp/i }),
    ).not.toBeInTheDocument();
  });

  it("keeps an unavailable server catalog explicit", () => {
    render(
      <IntegrationDiscoveryPanel
        result={{ available: false, reason: "Connector catalog disabled." }}
        client={client()}
      />,
    );
    expect(screen.getByText("Connector catalog disabled.")).toBeInTheDocument();
    expect(screen.queryByText("Provider coverage")).not.toBeInTheDocument();
  });

  it("surfaces tenant custom MCP review and preserves an unavailable broker state", async () => {
    const api = {
      ...client(),
      listCustomConnectors: vi
        .fn()
        .mockResolvedValue({
          available: true,
          connectors: [
            {
              id: "mcp-1",
              kind: "remote_mcp",
              origin: "https://mcp.example.com",
              path: "/mcp",
              status: "active",
              runtime: { available: false },
            },
          ],
        }),
      getCustomConnectorReview: vi
        .fn()
        .mockResolvedValue({
          available: true,
          review: {
            runtime: { available: false, reason: "broker unavailable" },
          },
        }),
      createCustomConnector: vi
        .fn()
        .mockResolvedValue({
          available: false,
          reason: "approval unavailable",
        }),
    };
    render(<IntegrationDiscoveryPanel result={result} client={api} />);
    await screen.findByText("Custom MCP · mcp-1");
    await userEvent.click(
      screen.getAllByRole("button", { name: "Open details" }).at(-1),
    );
    expect(await screen.findByText("broker unavailable")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Register custom MCP" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "MCP id" }),
      "new-mcp",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "HTTPS origin" }),
      "https://mcp.example.com",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Submit for server review" }),
    );
    expect(await screen.findByText("approval unavailable")).toBeInTheDocument();
  });
});
