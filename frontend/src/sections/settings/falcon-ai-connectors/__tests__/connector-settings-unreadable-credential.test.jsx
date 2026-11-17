import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent } from "src/utils/test-utils";

import ConnectorSettingsPage from "../ConnectorSettingsPage";

// Mocked at the transport layer so the real `useConnector` query runs.
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => mocks.enqueueSnackbar(...args),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: mocks.get,
    post: mocks.post,
    patch: mocks.patch,
    delete: mocks.delete,
  },
  endpoints: {
    falconAI: {
      connectors: "/falcon-ai/mcp-connectors/",
      connector: (id) => `/falcon-ai/mcp-connectors/${id}/`,
      connectorTools: (id) => `/falcon-ai/mcp-connectors/${id}/tools/`,
    },
  },
}));

// The connector API keeps this code as a connector's last error when the
// credential saved for it can't be read here, and the connector shows Pending.
const CODE = "CONNECTOR_AUTH_REQUIRED";
const SENTENCE =
  "The saved credential for this connector can't be read here. Connect it again to use it.";

const LIST_ROW = {
  id: "conn-1",
  name: "Linear",
  server_url: "https://mcp.linear.app/mcp",
  transport: "streamable_http",
  auth_type: "api_key",
  is_verified: false,
  tool_count: 1,
};

const CONNECTOR_URL = "/falcon-ai/mcp-connectors/conn-1/";

// Every kind of credential a connector is saved with.
const KINDS = [
  ["an API key", "api_key"],
  ["a bearer token", "bearer"],
  ["an OAuth sign-in", "oauth"],
];

const renderPage = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ConnectorSettingsPage />
    </QueryClientProvider>,
  );

// Opens the connector and waits for the detail route's tools to render; the
// detail record carries the last error.
const openConnector = async (authType, lastError) => {
  const row = { ...LIST_ROW, auth_type: authType };
  const detail = {
    ...row,
    discovered_tools: [{ name: "list_issues", description: "List issues." }],
    enabled_tool_names: ["list_issues"],
    last_error: lastError,
  };
  mocks.get.mockImplementation((url) => {
    if (url === "/falcon-ai/mcp-connectors/") {
      return Promise.resolve({ data: { results: [row] } });
    }
    if (url === CONNECTOR_URL) {
      return Promise.resolve({ data: { result: detail } });
    }
    return Promise.resolve({ data: {} });
  });

  renderPage();
  fireEvent.click(await screen.findByText("Linear"));
  await screen.findByText("list_issues");
};

const action = (name) => screen.getByRole("button", { name });

describe("Connector settings — a connector whose saved credential can't be read here", () => {
  beforeEach(() => vi.clearAllMocks());

  it.each(KINDS)(
    "says so in words for a connector saved with %s, not with its code",
    async (_kind, authType) => {
      await openConnector(authType, CODE);

      // The banner used to show the code itself.
      const banner = await screen.findByText(SENTENCE);
      expect(banner.closest('[role="alert"]')).not.toBeNull();
      expect(screen.queryByText(CODE)).toBeNull();
      expect(screen.getAllByText("Pending").length).toBeGreaterThan(0);
    },
  );

  it("shows any other error exactly as it is written", async () => {
    await openConnector("api_key", "Connection failed");

    expect(await screen.findByText("Connection failed")).toBeInTheDocument();
    expect(screen.queryByText(SENTENCE)).toBeNull();
  });

  it("keeps Edit and Test Connection on the card", async () => {
    await openConnector("api_key", CODE);

    expect(action("Edit")).toBeEnabled();
    expect(action("Test Connection")).toBeEnabled();
  });

  it("keeps Re-authenticate on the card of an OAuth connector", async () => {
    await openConnector("oauth", CODE);

    expect(action("Re-authenticate")).toBeEnabled();
    expect(action("Test Connection")).toBeEnabled();
  });
});
