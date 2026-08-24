import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent, act } from "src/utils/test-utils";

import CustomizePanel from "../CustomizePanel";
import { resolveEnabledNames } from "../connectorTools";
import { falconAIQueryKeys } from "../../hooks/useFalconAPI";

const mocks = vi.hoisted(() => ({
  listSkills: vi.fn(),
  fetchConnectors: vi.fn(),
  getConnector: vi.fn(),
  getSkill: vi.fn(),
  deleteConnector: vi.fn(),
  updateConnectorTools: vi.fn(),
  discoverConnectorTools: vi.fn(),
  authenticateConnector: vi.fn(),
  falconAIQueryKeys: {
    connector: (id) => ["falcon-ai", "connector", id],
  },
}));

vi.mock("../../hooks/useFalconAPI", () => mocks);

// What GET /mcp-connectors/ returns: MCPConnectorListSerializer, which carries
// neither discovered_tools nor enabled_tool_names.
const LIST_ROW = {
  id: "conn-1",
  name: "DeepWiki",
  server_url: "https://mcp.deepwiki.com/mcp",
  transport: "streamable_http",
  auth_type: "none",
  is_active: true,
  is_verified: true,
  tool_count: 3,
};

// What GET /mcp-connectors/<id>/ returns: MCPConnectorDetailSerializer.
const DETAIL = {
  ...LIST_ROW,
  discovered_tools: [
    { name: "ask_question", description: "Ask about a repo." },
    { name: "read_wiki_contents", description: "View documentation." },
    { name: "read_wiki_structure", description: "List doc topics." },
  ],
  enabled_tool_names: [
    "ask_question",
    "read_wiki_contents",
    "read_wiki_structure",
  ],
};

// A second connector, used to reproduce an out-of-order detail response.
const LIST_ROW_B = {
  id: "conn-2",
  name: "GitHub",
  server_url: "https://mcp.github.com/mcp",
  transport: "streamable_http",
  auth_type: "oauth",
  is_active: true,
  is_verified: true,
  tool_count: 2,
};

const DETAIL_B = {
  ...LIST_ROW_B,
  discovered_tools: [
    { name: "list_prs", description: "List pull requests." },
    { name: "create_issue", description: "Create an issue." },
  ],
  enabled_tool_names: ["list_prs", "create_issue"],
};

// A promise the test controls the settlement of, to force out-of-order
// resolution between two overlapping detail fetches.
const createDeferred = () => {
  let resolve;
  const promise = new Promise((res) => {
    resolve = res;
  });
  return { promise, resolve };
};

// useConnectorToolPermissions writes through to the react-query cache after a
// successful toggle, so every render of the panel needs a real QueryClient.
const createTestQueryClient = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });

const renderPanel = (queryClient = createTestQueryClient()) => {
  render(
    <QueryClientProvider client={queryClient}>
      <CustomizePanel />
    </QueryClientProvider>,
  );
  return queryClient;
};

const openConnector = async (queryClient) => {
  const client = renderPanel(queryClient);
  fireEvent.click(await screen.findByText("Connectors"));
  fireEvent.click(await screen.findByText("DeepWiki"));
  return client;
};

describe("resolveEnabledNames", () => {
  it("uses the stored permission list when there is one", () => {
    expect(resolveEnabledNames(DETAIL)).toEqual([
      "ask_question",
      "read_wiki_contents",
      "read_wiki_structure",
    ]);
  });

  it("expands the empty sentinel to every discovered tool", () => {
    // An empty list means "all enabled"; collapsing it to [] on write would
    // silently re-enable everything.
    expect(resolveEnabledNames({ ...DETAIL, enabled_tool_names: [] })).toEqual([
      "ask_question",
      "read_wiki_contents",
      "read_wiki_structure",
    ]);
  });

  it("yields nothing for a connector with no tools at all", () => {
    expect(resolveEnabledNames(LIST_ROW)).toEqual([]);
  });
});

describe("Customize panel — connector tools", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listSkills.mockResolvedValue({ results: [] });
    mocks.fetchConnectors.mockResolvedValue({ results: [LIST_ROW] });
    mocks.getConnector.mockResolvedValue(DETAIL);
    mocks.updateConnectorTools.mockResolvedValue({});
  });

  it("fetches the connector detail on select and renders Tool permissions", async () => {
    await openConnector();

    // The list payload alone can never satisfy this pane.
    await waitFor(() =>
      expect(mocks.getConnector).toHaveBeenCalledWith("conn-1"),
    );

    expect(await screen.findByText("Tool permissions")).toBeInTheDocument();
    expect(screen.getByText("ask_question")).toBeInTheDocument();
    expect(
      screen.queryByText(/No tools discovered yet/i),
    ).not.toBeInTheDocument();
  });

  it("shows a loading placeholder, not the empty state, while the detail loads", async () => {
    let release;
    mocks.getConnector.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );

    renderPanel();
    fireEvent.click(await screen.findByText("Connectors"));
    fireEvent.click(await screen.findByText("DeepWiki"));

    // The list row carries no tools; answering "nothing discovered" here is the
    // very bug this pane was reported for.
    expect(await screen.findByRole("status")).toHaveAttribute(
      "aria-label",
      "Loading tools",
    );
    expect(
      screen.queryByText(/No tools discovered yet/i),
    ).not.toBeInTheDocument();

    release(DETAIL);
    expect(await screen.findByText("ask_question")).toBeInTheDocument();
    // Held briefly past the response so a ~250ms fetch doesn't flicker.
    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );
  });

  it("reports a failed detail fetch instead of claiming there are no tools", async () => {
    mocks.getConnector.mockRejectedValue({
      response: { data: { detail: "Connector detail is unavailable." } },
    });

    renderPanel();
    fireEvent.click(await screen.findByText("Connectors"));
    fireEvent.click(await screen.findByText("DeepWiki"));

    expect(
      await screen.findByText("Connector detail is unavailable."),
    ).toBeInTheDocument();
    // "No tools discovered yet" would blame the connector for a request that
    // never landed.
    expect(
      screen.queryByText(/No tools discovered yet/i),
    ).not.toBeInTheDocument();

    // Retry re-runs the fetch, so a transient failure is recoverable in place.
    mocks.getConnector.mockResolvedValue(DETAIL);
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByText("ask_question")).toBeInTheDocument();
  });

  it("writes tool names, not tool objects, and keeps the untouched ones", async () => {
    await openConnector();
    await screen.findByText("ask_question");

    fireEvent.click(screen.getAllByTitle("Allowed")[0]);

    await waitFor(() => expect(mocks.updateConnectorTools).toHaveBeenCalled());
    const [connectorId, names] = mocks.updateConnectorTools.mock.calls[0];

    expect(connectorId).toBe("conn-1");
    // The old code sent `(conn.tools || [])` — always [] — wiping every
    // permission, and sent objects where the serializer wants name strings.
    expect(names).not.toEqual([]);
    expect(names.every((n) => typeof n === "string")).toBe(true);
    expect(names).not.toContain("ask_question");
    expect(names).toEqual(
      expect.arrayContaining(["read_wiki_contents", "read_wiki_structure"]),
    );
  });

  it("updates the shared react-query cache so the settings page sees the new permissions", async () => {
    // ConnectorSettingsPage reads this same connector through useConnector(id),
    // keyed identically in the same QueryClient. Seed it as if that page had
    // already fetched the record, pre-toggle.
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(falconAIQueryKeys.connector("conn-1"), DETAIL);

    await openConnector(queryClient);
    await screen.findByText("ask_question");

    fireEvent.click(screen.getAllByTitle("Allowed")[0]);
    await waitFor(() => expect(mocks.updateConnectorTools).toHaveBeenCalled());

    const cached = queryClient.getQueryData(
      falconAIQueryKeys.connector("conn-1"),
    );
    expect(cached.enabled_tool_names).not.toContain("ask_question");
    expect(cached.enabled_tool_names).toEqual(
      expect.arrayContaining(["read_wiki_contents", "read_wiki_structure"]),
    );
  });

  it("refuses to deny the last enabled tool, which would grant every tool", async () => {
    // [] is the all-enabled sentinel on both sides (mcp_tools.py:207), so
    // emptying the list inverts the permission and persists. TH-7673.
    mocks.getConnector.mockResolvedValue({
      ...DETAIL,
      enabled_tool_names: ["ask_question"],
    });

    await openConnector();
    await screen.findByText("ask_question");

    fireEvent.click(screen.getAllByTitle("Allowed")[0]);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Keep at least one tool enabled/i,
    );
    expect(mocks.updateConnectorTools).not.toHaveBeenCalled();
  });

  it("still allows denying a tool when others remain enabled", async () => {
    mocks.getConnector.mockResolvedValue({
      ...DETAIL,
      enabled_tool_names: ["ask_question", "read_wiki_contents"],
    });

    await openConnector();
    await screen.findByText("ask_question");

    fireEvent.click(screen.getAllByTitle("Allowed")[0]);

    await waitFor(() =>
      expect(mocks.updateConnectorTools).toHaveBeenCalledWith("conn-1", [
        "read_wiki_contents",
      ]),
    );
  });

  it("surfaces a failed write instead of leaving the toggle silently stuck", async () => {
    mocks.updateConnectorTools.mockRejectedValue({
      response: { data: { detail: "Connector is not verified." } },
    });

    await openConnector();
    await screen.findByText("ask_question");

    fireEvent.click(screen.getAllByTitle("Allowed")[0]);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Connector is not verified.",
    );
  });

  it("ignores a stale detail response when a newer selection is already in flight", async () => {
    mocks.fetchConnectors.mockResolvedValue({
      results: [LIST_ROW, LIST_ROW_B],
    });

    const deferredA = createDeferred();
    const deferredB = createDeferred();
    mocks.getConnector.mockImplementation((id) => {
      if (id === "conn-1") return deferredA.promise;
      if (id === "conn-2") return deferredB.promise;
      throw new Error(`unexpected connector id ${id}`);
    });

    renderPanel();
    fireEvent.click(await screen.findByText("Connectors"));

    // Select A, then select B before A's detail request has resolved.
    fireEvent.click(await screen.findByText("DeepWiki"));
    fireEvent.click(await screen.findByText("GitHub"));

    await waitFor(() => expect(mocks.getConnector).toHaveBeenCalledTimes(2));

    // Resolve out of order: the newer request (B) settles first, then the
    // stale one (A) settles after.
    await act(async () => {
      deferredB.resolve(DETAIL_B);
      await Promise.resolve();
    });
    expect(await screen.findByText("list_prs")).toBeInTheDocument();

    await act(async () => {
      deferredA.resolve(DETAIL);
      // A macrotask tick flushes every microtask queued by A's now-settled
      // await chain, giving a buggy implementation a full chance to apply.
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    // B's tools must still be showing; A's late response must not have
    // clobbered them.
    expect(screen.getByText("list_prs")).toBeInTheDocument();
    expect(screen.queryByText("ask_question")).not.toBeInTheDocument();
  });

  it("keeps the loading indicator up while a newer request is still in flight, even if a stale one settles first", async () => {
    mocks.fetchConnectors.mockResolvedValue({
      results: [LIST_ROW, LIST_ROW_B],
    });

    const deferredA = createDeferred();
    const deferredB = createDeferred();
    mocks.getConnector.mockImplementation((id) => {
      if (id === "conn-1") return deferredA.promise;
      if (id === "conn-2") return deferredB.promise;
      throw new Error(`unexpected connector id ${id}`);
    });

    renderPanel();
    fireEvent.click(await screen.findByText("Connectors"));

    fireEvent.click(await screen.findByText("DeepWiki"));
    fireEvent.click(await screen.findByText("GitHub"));

    await waitFor(() => expect(mocks.getConnector).toHaveBeenCalledTimes(2));

    // The stale (first) request settles while the newer one is still pending.
    await act(async () => {
      deferredA.resolve(DETAIL);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    // B's request hasn't resolved yet — the loading indicator must still be
    // up. A stale settlement must not have cleared it out from under B.
    expect(screen.getByRole("status")).toHaveAttribute(
      "aria-label",
      "Loading tools",
    );

    await act(async () => {
      deferredB.resolve(DETAIL_B);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(await screen.findByText("list_prs")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("clears the loading state when an in-flight detail fetch is abandoned", async () => {
    // Editing mid-fetch bumps the ticket, so the abandoned response is ignored
    // and its `finally` no longer clears the flag. Nothing else would.
    const deferred = createDeferred();
    mocks.getConnector.mockImplementation(() => deferred.promise);

    renderPanel();
    fireEvent.click(await screen.findByText("Connectors"));
    fireEvent.click(await screen.findByText("DeepWiki"));
    await waitFor(() => expect(mocks.getConnector).toHaveBeenCalled());

    fireEvent.click(await screen.findByText("Edit"));
    await act(async () => {
      deferred.resolve(DETAIL);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    fireEvent.click(await screen.findByText("Cancel"));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("does not invent a cache entry when nothing has fetched the connector", async () => {
    // A partial entry would be stamped fresh, so the settings page would render
    // it — with no discovered_tools — instead of fetching the real record.
    const queryClient = createTestQueryClient();
    await openConnector(queryClient);
    await screen.findByText("ask_question");

    fireEvent.click(screen.getAllByTitle("Allowed")[0]);
    await waitFor(() => expect(mocks.updateConnectorTools).toHaveBeenCalled());

    expect(
      queryClient.getQueryData(falconAIQueryKeys.connector("conn-1")),
    ).toBeUndefined();
  });

  it("keeps the tools on screen after an OAuth callback", async () => {
    // The callback used to assign a row from the LIST endpoint, whose
    // serializer carries no discovered_tools — blanking the very tools the
    // user had just authorised.
    mocks.getConnector.mockResolvedValue(DETAIL);
    mocks.discoverConnectorTools.mockResolvedValue({});

    await openConnector();
    await screen.findByText("ask_question");

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "falcon_oauth_callback", status: "success" },
        }),
      );
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(await screen.findByText("ask_question")).toBeInTheDocument();
    expect(
      screen.queryByText(/No tools discovered yet/i),
    ).not.toBeInTheDocument();
  });

  it("allows a whole group in one request instead of racing per tool", async () => {
    mocks.getConnector.mockResolvedValue({
      ...DETAIL,
      enabled_tool_names: ["ask_question"],
    });

    await openConnector();
    await screen.findByText("read_wiki_contents");

    fireEvent.click(screen.getAllByText("Always allow")[0]);

    await waitFor(() => expect(mocks.updateConnectorTools).toHaveBeenCalled());
    // Per-tool toggles would each compute from the same stale snapshot, so the
    // last write would land alone.
    expect(mocks.updateConnectorTools).toHaveBeenCalledTimes(1);
    const [, names] = mocks.updateConnectorTools.mock.calls[0];
    expect(names).toEqual(
      expect.arrayContaining([
        "ask_question",
        "read_wiki_contents",
        "read_wiki_structure",
      ]),
    );
  });
});
