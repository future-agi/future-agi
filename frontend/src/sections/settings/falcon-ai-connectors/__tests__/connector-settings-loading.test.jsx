import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent } from "src/utils/test-utils";

import ConnectorSettingsPage from "../ConnectorSettingsPage";

// Mocked at the transport layer so the real `useConnector` query runs: the
// hook closes over its module's own `getConnector`, so replacing that export
// would leave the hook calling the original.
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

// The list serializer omits discovered_tools; only the detail route carries it.
const LIST_ROW = {
  id: "conn-1",
  name: "DeepWiki",
  server_url: "https://mcp.deepwiki.com/mcp",
  transport: "streamable_http",
  auth_type: "none",
  is_verified: true,
  tool_count: 2,
};

const DETAIL = {
  ...LIST_ROW,
  discovered_tools: [
    { name: "ask_question", description: "Ask about a repo." },
    { name: "read_wiki_contents", description: "View documentation." },
  ],
  enabled_tool_names: ["ask_question", "read_wiki_contents"],
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

describe("Connector settings — detail loading", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows a loading placeholder, not the empty state, while the detail loads", async () => {
    let release;
    mocks.get.mockImplementation((url) => {
      if (url === "/falcon-ai/mcp-connectors/") {
        return Promise.resolve({ data: { results: [LIST_ROW] } });
      }
      if (url === "/falcon-ai/mcp-connectors/conn-1/") {
        return new Promise((resolve) => {
          release = () => resolve({ data: { result: DETAIL } });
        });
      }
      return Promise.resolve({ data: {} });
    });

    renderPage();
    fireEvent.click(await screen.findByText("DeepWiki"));

    expect(await screen.findByRole("status")).toHaveAttribute(
      "aria-label",
      "Loading tools",
    );
    // The list row has no tools; saying "none discovered" here blames the
    // connector for a request still in flight.
    expect(
      screen.queryByText(/No tools discovered yet/i),
    ).not.toBeInTheDocument();

    release();
    expect(await screen.findByText("ask_question")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );
  });
});
