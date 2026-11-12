import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";

import { renderMcp, demoToolCall } from "../testing/renderMcp";
import ServersTab from "./ServersTab";
import { ADMIN_LEVELS, expectEditable } from "../../testing/adminOnly";

vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../testing/agGridMock");
  return { AgGridReact: MockAgGrid };
});

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(async (url) => {
        if (url.endsWith("/mcp-served-tools/"))
          return {
            data: {
              result: [
                {
                  name: "github__create_issue",
                  upstream_name: "create_issue",
                  server_id: "github",
                  quarantined: false,
                  identity: {
                    platform: "other",
                    toolkit_slug: "",
                    toolkit_name: "",
                  },
                },
              ],
            },
          };
        if (url.endsWith("/api-keys/")) return { data: { result: [] } };
        throw new Error("Unexpected fixture HTTP path");
      }),
    },
  };
});

// Stand-ins for stored upstream credentials, as the config read returns them
// on this build. They must never reach the page.
const STORED_TOKEN = "stored-bearer-value-0001";
const STORED_KEY = "stored-api-key-value-0002";

const { configQuery, mockUpdateMutate, mockRemoveMutate, snackbars, calls } =
  vi.hoisted(() => ({
    configQuery: { current: {} },
    mockUpdateMutate: vi.fn(),
    mockRemoveMutate: vi.fn(),
    snackbars: [],
    calls: { current: [] },
  }));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));
// Server writes are Owner or Admin only (useGatewayAdmin); these cases act
// as an Admin unless they set another level.
const { org } = vi.hoisted(() => ({ org: { level: 8 } }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => configQuery.current,
}));
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({
    data: {
      features: {
        catalog: true,
        credential_vault: true,
        virtual_servers: true,
        tool_call_explorer: true,
      },
      enabled: true,
      servers: [
        { server_id: "github", healthy: true, tool_count: 2 },
        {
          server_id: "search",
          healthy: false,
          tool_count: 0,
          last_error: "initialize: http 401",
        },
      ],
    },
  }),
  useMCPTools: () => ({
    data: [
      {
        name: "github__create_issue",
        server_id: "github",
        upstream_name: "create_issue",
        quarantined: false,
      },
      {
        name: "github__close_issue",
        server_id: "github",
        upstream_name: "close_issue",
        quarantined: true,
      },
    ],
    isLoading: false,
  }),
  useMCPResources: () => ({
    data: [{ uri: "docs://a", name: "Readme", server_id: "github" }],
  }),
  useMCPPrompts: () => ({ data: [] }),
  useRemoveMCPServer: () => ({ mutate: mockRemoveMutate, isPending: false }),
  useUpdateMCPServer: () => ({ mutate: mockUpdateMutate, isPending: false }),
}));
vi.mock("../hooks/useMCPCredentials", () => ({
  useMCPCredentials: () => ({ data: { rows: [], count: 0 } }),
}));
vi.mock("../hooks/useMCPToolCalls", () => ({
  useMCPToolCalls: () => ({
    data: { pages: [{ next: null, results: calls.current }] },
    isLoading: false,
  }),
}));

const storedConfig = {
  mcp: {
    enabled: true,
    servers: {
      github: {
        url: "https://mcp.example.com/github/mcp",
        transport: "http",
        tools_cache_ttl: "5m",
        auth: { type: "bearer", token: STORED_TOKEN },
      },
      search: {
        url: "https://mcp.example.com/search/mcp",
        transport: "http",
        auth: { type: "api_key", header: "X-Search-Key", key: STORED_KEY },
      },
      open: { url: "https://mcp.example.com/open/mcp", transport: "http" },
      docs: {
        url: "https://mcp.example.com/docs/mcp",
        transport: "http",
        auth: { type: "bearer" },
      },
      payments: { url: "https://mcp.stripe.com", transport: "http" },
    },
  },
};

function cardFor(serverId) {
  return screen.getByRole("button", { name: new RegExp(`^${serverId}\\b`) });
}

function expectNoStoredSecretOnPage() {
  const html = document.body.innerHTML;
  expect(html).not.toContain(STORED_TOKEN);
  expect(html).not.toContain(STORED_KEY);
}

async function openDrawer(serverId) {
  fireEvent.click(cardFor(serverId));
  return screen.findByRole("dialog", { name: serverId });
}

beforeEach(() => {
  org.level = 8;
  configQuery.current = { data: storedConfig, isLoading: false };
  mockUpdateMutate.mockClear();
  mockRemoveMutate.mockClear();
  snackbars.length = 0;
  calls.current = [];
});

describe("ServersTab catalog", () => {
  it("shows each server as a card with health, auth and tools", () => {
    renderMcp(<ServersTab gatewayId="gw-1" />);
    const github = cardFor("github");
    expect(within(github).getByText("Healthy")).toBeInTheDocument();
    expect(within(github).getByText("Bearer token")).toBeInTheDocument();
    expect(within(github).getByText("2 tools")).toBeInTheDocument();
    expect(
      within(github).getByText("mcp.example.com/github/mcp"),
    ).toBeInTheDocument();
    // An unhealthy server says why.
    expect(
      within(cardFor("search")).getByText("initialize: http 401"),
    ).toBeInTheDocument();
    expect(within(cardFor("open")).getByText("No auth")).toBeInTheDocument();
    expect(
      within(cardFor("docs")).getByText("Credential not set"),
    ).toBeInTheDocument();
    expectNoStoredSecretOnPage();
  });

  // Known apps without bundled artwork use initials immediately, without
  // requesting a vendor favicon. An org-chosen id never selects a logo.
  it("uses local initials when an app has no bundled logo", () => {
    renderMcp(<ServersTab gatewayId="gw-1" />);
    expect(cardFor("payments").querySelector("img")).toBeNull();
    expect(within(cardFor("payments")).getByText("PA")).toBeInTheDocument();
    expect(cardFor("github").querySelector("img")).toBeNull();
    expect(within(cardFor("github")).getByText("GI")).toBeInTheDocument();
  });

  it("selects bundled artwork by the server domain, not its id", () => {
    configQuery.current = {
      data: {
        mcp: {
          ...storedConfig.mcp,
          servers: {
            source: {
              url: "https://api.githubcopilot.com/mcp",
              transport: "http",
            },
          },
        },
      },
      isLoading: false,
    };
    renderMcp(<ServersTab gatewayId="gw-1" />);
    const logo = cardFor("source").querySelector("img");
    expect(logo).toHaveAttribute("src", "/assets/icons/app/ic_github_grey.svg");
    expect(logo).toHaveAttribute("referrerpolicy", "no-referrer");
  });

  it("filters by connection status, auth and text", async () => {
    const user = userEvent.setup();
    renderMcp(<ServersTab gatewayId="gw-1" />);

    await user.click(screen.getByRole("combobox", { name: /^Status/ }));
    await user.click(await screen.findByRole("option", { name: "Connected" }));
    expect(
      screen.getByRole("button", { name: /^github\b/ }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^search\b/ })).toBeNull();

    await user.click(screen.getByRole("combobox", { name: /^Status/ }));
    await user.click(await screen.findByRole("option", { name: "Unhealthy" }));
    await user.click(screen.getByRole("combobox", { name: /^Auth/ }));
    await user.click(
      await screen.findByRole("option", { name: "Bearer token" }),
    );
    expect(screen.getByText("No servers match")).toBeInTheDocument();
  });

  it("opens a server with its tools, resources and recent calls, no fake stats", async () => {
    calls.current = [
      demoToolCall({
        id: 9,
        server_id: "github",
        tool: "github__create_issue",
        upstream_tool: "create_issue",
      }),
    ];
    renderMcp(<ServersTab gatewayId="gw-1" />);
    const drawer = await openDrawer("github");
    expect(
      await within(drawer).findByText("github__create_issue"),
    ).toBeInTheDocument();
    expect(within(drawer).getByText("Served tools (1)")).toBeInTheDocument();
    // The served list never falls back to the inventory: the quarantined
    // tool is not served. The server's own tools are listed apart.
    expect(
      within(
        within(drawer).getByRole("region", { name: "Live served tools" }),
      ).queryByText("Quarantined"),
    ).not.toBeInTheDocument();
    expect(
      within(drawer).queryByText("github__close_issue"),
    ).not.toBeInTheDocument();
    expect(within(drawer).getByText("Readme")).toBeInTheDocument();
    expect(
      within(drawer).getByText("1 resource, 0 prompts"),
    ).toBeInTheDocument();
    expect(within(drawer).getAllByText("create_issue").length).toBeGreaterThan(
      0,
    );
    expect(within(drawer).queryByText(/Error rate|Avg latency/)).toBeNull();
    expectNoStoredSecretOnPage();
  });

  it("lists the server's own tools apart from Served tools, with the card's count", async () => {
    renderMcp(<ServersTab gatewayId="gw-1" />);
    const drawer = await openDrawer("github");
    const list = within(drawer).getByRole("region", {
      name: "Tools on this server",
    });
    expect(
      within(list).getByRole("heading", { name: "Tools on this server (2)" }),
    ).toBeInTheDocument();
    expect(within(list).getByText("create_issue")).toBeInTheDocument();
    expect(within(list).getByText("close_issue")).toBeInTheDocument();
    expect(within(list).getByText("Quarantined")).toBeInTheDocument();
    expect(
      await within(drawer).findByText("Served tools (1)"),
    ).toBeInTheDocument();
  });

  it("edits from the drawer without ever rendering the stored token", async () => {
    const user = userEvent.setup();
    renderMcp(<ServersTab gatewayId="gw-1" />);
    const drawer = await openDrawer("github");
    await user.click(within(drawer).getByRole("button", { name: "Edit" }));

    expect(await screen.findByText("Edit github")).toBeInTheDocument();
    expect(screen.getByText(/Stored credential: set/)).toBeInTheDocument();
    expectNoStoredSecretOnPage();

    // Left alone, the stored credential is kept: no auth is sent.
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(mockUpdateMutate.mock.calls[0][0]).toEqual({
      gatewayId: "gw-1",
      serverId: "github",
      config: { transport: "http", url: "https://mcp.example.com/github/mcp" },
    });
  });

  it("sends a new API key only when the user types one", async () => {
    const user = userEvent.setup();
    renderMcp(<ServersTab gatewayId="gw-1" />);
    const drawer = await openDrawer("search");
    await user.click(within(drawer).getByRole("button", { name: "Edit" }));
    await user.click(
      await screen.findByRole("combobox", { name: /Authentication/ }),
    );
    await user.click(await screen.findByRole("option", { name: /^API key/ }));
    expect(screen.getByLabelText(/Header name/)).toHaveValue("X-Search-Key");

    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(mockUpdateMutate).not.toHaveBeenCalled();
    expect(snackbars.map(({ message }) => message)).toEqual([
      "Enter the API key to save this server",
    ]);

    await user.type(screen.getByLabelText(/^Key/), "new-key-value-0005");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(mockUpdateMutate.mock.calls[0][0].config).toEqual({
      transport: "http",
      url: "https://mcp.example.com/search/mcp",
      auth: {
        type: "api_key",
        header: "X-Search-Key",
        key: "new-key-value-0005",
      },
    });
  });

  it("removes a server after confirming", async () => {
    const user = userEvent.setup();
    renderMcp(<ServersTab gatewayId="gw-1" />);
    const drawer = await openDrawer("open");
    await user.click(within(drawer).getByRole("button", { name: "Remove" }));
    await user.click(
      await screen.findByRole("button", { name: "Remove server" }),
    );
    expect(mockRemoveMutate.mock.calls[0][0]).toEqual({
      gatewayId: "gw-1",
      serverId: "open",
    });
  });

  it("does not show the tools cache TTL the gateway ignores", () => {
    renderMcp(<ServersTab gatewayId="gw-1" />);
    expect(screen.queryByText(/Cache TTL/)).toBeNull();
  });

  it("opens an empty add dialog, also from ?add=1", async () => {
    const seen = {};
    const Probe = () => {
      const location = useLocation();
      seen.search = location.search;
      return null;
    };
    renderMcp(
      <>
        <ServersTab gatewayId="gw-1" />
        <Probe />
      </>,
      { route: "/dashboard/gateway/mcp?tab=servers&add=1" },
    );
    expect(await screen.findByText("Add MCP server")).toBeInTheDocument();
    expect(screen.getByLabelText(/Server ID/)).toHaveValue("");
    expect(screen.queryByText(/Stored credential/)).toBeNull();
    await waitFor(() => expect(seen.search).toBe("?tab=servers"));
  });

  it("starts an org with no servers at the add flow", () => {
    configQuery.current = { data: { mcp: {} }, isLoading: false };
    renderMcp(<ServersTab gatewayId="gw-1" />);
    expect(screen.getByText("No MCP servers yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    expect(screen.getByText("Add MCP server")).toBeInTheDocument();
  });

  it.each([
    ["a Member", 3],
    ["a Viewer", 1],
    ["a user whose level is not loaded", null],
  ])(
    "shows Add server, Edit and Remove disabled, saying why, for %s",
    async (_, level) => {
      org.level = level;
      renderMcp(<ServersTab gatewayId="gw-1" />);
      const add = screen.getByRole("button", { name: "Add server" });
      expect(add).toBeDisabled();
      expect(add.closest('[aria-label="Owner or Admin only"]')).not.toBeNull();
      const drawer = await openDrawer("github");
      for (const name of ["Edit", "Remove"]) {
        const control = within(drawer).getByRole("button", { name });
        expect(control).toBeDisabled();
        expect(
          control.closest('[aria-label="Owner or Admin only"]'),
        ).not.toBeNull();
      }
      expect(mockUpdateMutate).not.toHaveBeenCalled();
      expect(mockRemoveMutate).not.toHaveBeenCalled();
    },
  );

  it.each(ADMIN_LEVELS)(
    "lets %s add, edit and remove a server",
    async (_, level) => {
      org.level = level;
      renderMcp(<ServersTab gatewayId="gw-1" />);
      expectEditable(screen.getByRole("button", { name: "Add server" }));
      const drawer = await openDrawer("github");
      for (const name of ["Edit", "Remove"]) {
        expectEditable(within(drawer).getByRole("button", { name }));
      }
    },
  );
});
