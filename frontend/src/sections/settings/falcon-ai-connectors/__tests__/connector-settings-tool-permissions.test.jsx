import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent, act } from "src/utils/test-utils";

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

const TOOLS_URL = "/falcon-ai/mcp-connectors/conn-1/tools/";

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

// Opens the connector and waits for the detail route's tools to render.
const openConnector = async (detail) => {
  mocks.get.mockImplementation((url) => {
    if (url === "/falcon-ai/mcp-connectors/") {
      return Promise.resolve({ data: { results: [LIST_ROW] } });
    }
    if (url === "/falcon-ai/mcp-connectors/conn-1/") {
      return Promise.resolve({ data: { result: detail } });
    }
    return Promise.resolve({ data: {} });
  });
  mocks.patch.mockResolvedValue({ data: { result: detail } });

  renderPage();
  fireEvent.click(await screen.findByText("DeepWiki"));
  await screen.findByText("ask_question");
};

describe("Connector settings — tool permissions", () => {
  beforeEach(() => vi.clearAllMocks());

  it("refuses to deny the last enabled tool, which would grant every tool", async () => {
    // [] is the all-enabled sentinel on both sides (mcp_tools.py:207), so
    // emptying the list inverts the permission and persists. TH-7673 — the
    // same guard the Customize pane has, on the surface that lacked it.
    await openConnector({ ...DETAIL, enabled_tool_names: ["ask_question"] });

    const [askQuestion] = screen.getAllByRole("checkbox");
    expect(askQuestion).toBeChecked();

    fireEvent.click(askQuestion);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /At least one tool must stay allowed/i,
    );
    expect(mocks.patch).not.toHaveBeenCalled();
  });

  it("denies a tool from the all-enabled sentinel instead of writing it back", async () => {
    // Stored [] means "all enabled". Filtering the raw list leaves [], which
    // reads as all-enabled again — the toggle appears to do nothing.
    await openConnector({ ...DETAIL, enabled_tool_names: [] });

    const [askQuestion] = screen.getAllByRole("checkbox");
    expect(askQuestion).toBeChecked();

    fireEvent.click(askQuestion);

    await waitFor(() =>
      expect(mocks.patch).toHaveBeenCalledWith(TOOLS_URL, {
        enabled_tool_names: ["read_wiki_contents"],
      }),
    );
  });

  it("still allows denying a tool when others remain enabled", async () => {
    await openConnector(DETAIL);

    fireEvent.click(screen.getAllByRole("checkbox")[0]);

    await waitFor(() =>
      expect(mocks.patch).toHaveBeenCalledWith(TOOLS_URL, {
        enabled_tool_names: ["read_wiki_contents"],
      }),
    );
  });

  it("shows the write in flight rather than a switch that does nothing", async () => {
    // The switch is controlled by the server's answer, and this page also
    // refetches the list afterwards, so the wait is longer than it looks.
    await openConnector(DETAIL);

    // After openConnector: the helper seeds a resolved patch of its own.
    let release;
    mocks.patch.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () => resolve({ data: { result: DETAIL } });
        }),
    );

    fireEvent.click(screen.getAllByRole("checkbox")[0]);

    expect(await screen.findByLabelText("Saving")).toBeInTheDocument();
    // Still clickable: the writer coalesces further clicks rather than
    // dropping them, so blocking them would defeat the point.
    expect(screen.getAllByRole("checkbox")[0]).not.toBeDisabled();

    await act(async () => {
      release();
    });
    await waitFor(() =>
      expect(screen.queryByLabelText("Saving")).not.toBeInTheDocument(),
    );
  });

  it("coalesces clicks made while a write is out instead of losing them", async () => {
    await openConnector({
      ...DETAIL,
      discovered_tools: [
        ...DETAIL.discovered_tools,
        { name: "read_wiki_structure", description: "List doc topics." },
      ],
      enabled_tool_names: [
        "ask_question",
        "read_wiki_contents",
        "read_wiki_structure",
      ],
    });

    const releases = [];
    mocks.patch.mockImplementation(
      () =>
        new Promise((resolve) => releases.push(() => resolve({ data: {} }))),
    );

    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getAllByRole("checkbox")[1]);

    expect(mocks.patch).toHaveBeenCalledTimes(1);

    await act(async () => {
      releases[0]();
    });

    await waitFor(() => expect(mocks.patch).toHaveBeenCalledTimes(2));
    expect(mocks.patch.mock.calls[1][1]).toEqual({
      enabled_tool_names: ["read_wiki_structure"],
    });
  });

  it("sends a click that arrives while the post-write refresh is running", async () => {
    // This page resyncs from the server once writes settle, and that refetch
    // is as slow as any other request. A click during it must still be sent.
    const three = {
      ...DETAIL,
      discovered_tools: [
        ...DETAIL.discovered_tools,
        { name: "read_wiki_structure", description: "List doc topics." },
      ],
      enabled_tool_names: [
        "ask_question",
        "read_wiki_contents",
        "read_wiki_structure",
      ],
    };
    await openConnector(three);

    let releaseRefresh;
    mocks.get.mockImplementation((url) => {
      if (url === "/falcon-ai/mcp-connectors/") {
        return Promise.resolve({ data: { results: [LIST_ROW] } });
      }
      if (url === "/falcon-ai/mcp-connectors/conn-1/") {
        return new Promise((resolve) => {
          releaseRefresh = () => resolve({ data: { result: three } });
        });
      }
      return Promise.resolve({ data: {} });
    });

    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    await waitFor(() => expect(mocks.patch).toHaveBeenCalledTimes(1));

    // The write has landed and the refresh is now in flight.
    await waitFor(() => expect(releaseRefresh).toBeDefined());
    fireEvent.click(screen.getAllByRole("checkbox")[1]);

    await act(async () => {
      releaseRefresh();
    });

    // Without the outer drain loop this second write is never sent, and the
    // refresh reverts the row it was applied to.
    await waitFor(() => expect(mocks.patch).toHaveBeenCalledTimes(2));
    expect(mocks.patch.mock.calls[1][1]).toEqual({
      enabled_tool_names: ["read_wiki_structure"],
    });
  });

  it("warns on the last enabled toggle before it is clicked", async () => {
    await openConnector({ ...DETAIL, enabled_tool_names: ["ask_question"] });

    fireEvent.mouseOver(screen.getAllByRole("checkbox")[0]);

    expect(
      await screen.findByText(/at least one must stay on/i),
    ).toBeInTheDocument();
  });

  it("leaves the other toggles unannotated", async () => {
    await openConnector(DETAIL);

    fireEvent.mouseOver(screen.getAllByRole("checkbox")[0]);

    await waitFor(() =>
      expect(
        screen.queryByText(/at least one must stay on/i),
      ).not.toBeInTheDocument(),
    );
  });
});
