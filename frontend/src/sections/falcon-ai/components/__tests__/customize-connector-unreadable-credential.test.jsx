import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent } from "src/utils/test-utils";

import CustomizePanel from "../CustomizePanel";

// The Customize pane reaches the server only through these. It imports
// authenticateConnector lazily; vi.mock covers that too.
const mocks = vi.hoisted(() => ({
  listSkills: vi.fn(),
  getSkill: vi.fn(),
  createSkill: vi.fn(),
  updateSkill: vi.fn(),
  deleteSkill: vi.fn(),
  fetchConnectors: vi.fn(),
  getConnector: vi.fn(),
  createConnector: vi.fn(),
  updateConnector: vi.fn(),
  deleteConnector: vi.fn(),
  authenticateConnector: vi.fn(),
  updateConnectorTools: vi.fn(),
  discoverConnectorTools: vi.fn(),
  falconAIQueryKeys: {
    connector: (id) => ["falcon-ai", "connector", id],
  },
}));

vi.mock("../../hooks/useFalconAPI", () => mocks);

// The connector API keeps this code as a connector's last error when the
// credential saved for it can't be read here, and the connector shows Pending.
const CODE = "CONNECTOR_AUTH_REQUIRED";
const SENTENCE =
  "The saved credential for this connector can't be read here. Connect it again to use it.";

// A connector as the list returns it (a summary without tools)...
const ROW = {
  id: "conn-1",
  name: "Linear",
  server_url: "https://mcp.linear.app/mcp",
  transport: "streamable_http",
  auth_type: "api_key",
  is_active: true,
  is_verified: false,
  tool_count: 1,
};

// ...and as its detail route returns it.
const detail = (fields) => ({
  ...ROW,
  discovered_tools: [{ name: "list_issues", description: "List issues." }],
  enabled_tool_names: ["list_issues"],
  ...fields,
});

// Every kind of credential a connector is saved with.
const KINDS = [
  ["an API key", "api_key"],
  ["a bearer token", "bearer"],
  ["an OAuth sign-in", "oauth"],
];

const renderPanel = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <CustomizePanel />
    </QueryClientProvider>,
  );

const openConnector = async (connector) => {
  mocks.fetchConnectors.mockResolvedValue({
    results: [{ ...ROW, auth_type: connector.auth_type }],
  });
  mocks.getConnector.mockResolvedValue(connector);
  renderPanel();
  fireEvent.click(await screen.findByText("Connectors"));
  fireEvent.click(await screen.findByText(connector.name));
  // The detail record is on screen once its tools are listed.
  await screen.findByText("list_issues");
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listSkills.mockResolvedValue({ results: [] });
});

describe("Customize > a connector whose saved credential can't be read here", () => {
  it.each(KINDS)(
    "says so in words for a connector saved with %s, not with its code",
    async (_kind, authType) => {
      await openConnector(detail({ auth_type: authType, last_error: CODE }));

      // The banner used to show the code itself.
      const banner = await screen.findByText(SENTENCE);
      expect(banner.closest('[role="alert"]')).not.toBeNull();
      expect(screen.queryByText(CODE)).toBeNull();
      expect(screen.getAllByText("Pending").length).toBeGreaterThan(0);
    },
  );

  it.each([
    ["a failed connection test", "Connection failed"],
    ["a failed sign-in", "Authentication failed."],
  ])("shows %s exactly as it is written", async (_label, message) => {
    await openConnector(detail({ last_error: message }));

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByText(SENTENCE)).toBeNull();
  });

  it("keeps Re-authenticate on the card, and it connects again", async () => {
    const connector = detail({ last_error: CODE });
    mocks.authenticateConnector.mockResolvedValue({
      status: true,
      result: { ...connector, is_verified: true, last_error: "" },
      message: "Connected successfully",
    });
    await openConnector(connector);

    expect(screen.getByRole("button", { name: "Edit" })).toBeEnabled();
    const reauth = screen.getByRole("button", { name: "Re-authenticate" });
    expect(reauth).toBeEnabled();
    fireEvent.click(reauth);

    await waitFor(() =>
      expect(mocks.authenticateConnector).toHaveBeenCalledWith(connector.id),
    );
  });
});
