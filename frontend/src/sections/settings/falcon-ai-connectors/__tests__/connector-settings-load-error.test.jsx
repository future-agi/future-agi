import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent } from "src/utils/test-utils";

import ConnectorSettingsPage from "../ConnectorSettingsPage";

// W10-SETTINGS-TASKS-U03: when the connector list fails to load, the page must
// not say "No connectors yet" or invite the user to add one (a user with
// connectors would recreate them and hit "already exists"). It says the list
// could not be loaded and offers a Retry that reads the list again.
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post, patch: mocks.patch },
  endpoints: {
    falconAI: {
      connectors: "/falcon-ai/mcp-connectors/",
      connector: (id) => `/falcon-ai/mcp-connectors/${id}/`,
      connectorTools: (id) => `/falcon-ai/mcp-connectors/${id}/tools/`,
    },
  },
}));

const LIST_URL = "/falcon-ai/mcp-connectors/";

const LIST_ROW = {
  id: "conn-1",
  name: "DeepWiki",
  server_url: "https://mcp.deepwiki.com/mcp",
  transport: "streamable_http",
  auth_type: "none",
  is_verified: true,
  tool_count: 2,
};

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

const listCalls = () =>
  mocks.get.mock.calls.filter(([url]) => url === LIST_URL).length;

describe("Connector settings — list load failure", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    let listReads = 0;
    mocks.get.mockImplementation((url) => {
      if (url === LIST_URL) {
        listReads += 1;
        return listReads === 1
          ? Promise.reject(new Error("synthetic list failure"))
          : Promise.resolve({ data: { results: [LIST_ROW] } });
      }
      return Promise.resolve({ data: {} });
    });
  });

  it("says the list could not be loaded instead of 'No connectors yet'", async () => {
    renderPage();

    const failure = await screen.findByRole("alert");
    expect(failure).toHaveTextContent("Connectors could not be loaded");
    expect(failure).toHaveTextContent("Check your connection and retry.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();

    expect(screen.queryByText("No connectors yet")).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        "Add an external MCP server to give Falcon AI access to additional tools.",
      ),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Add connector" }),
    ).not.toBeInTheDocument();
  });

  it("reads the list again on Retry and shows the connectors", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));

    expect(await screen.findByText("DeepWiki")).toBeInTheDocument();
    expect(listCalls()).toBe(2);
    expect(
      screen.queryByText("Connectors could not be loaded"),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
