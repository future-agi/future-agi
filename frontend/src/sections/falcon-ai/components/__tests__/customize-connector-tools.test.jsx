import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "src/utils/test-utils";

import CustomizePanel from "../CustomizePanel";
import { resolveEnabledNames } from "../connectorTools";

const mocks = vi.hoisted(() => ({
  listSkills: vi.fn(),
  fetchConnectors: vi.fn(),
  getConnector: vi.fn(),
  getSkill: vi.fn(),
  deleteConnector: vi.fn(),
  updateConnectorTools: vi.fn(),
  discoverConnectorTools: vi.fn(),
  authenticateConnector: vi.fn(),
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

const openConnector = async () => {
  render(<CustomizePanel />);
  fireEvent.click(await screen.findByText("Connectors"));
  fireEvent.click(await screen.findByText("DeepWiki"));
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

    render(<CustomizePanel />);
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

    render(<CustomizePanel />);
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
