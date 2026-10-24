import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import ServersTab from "./ServersTab";

// The write controls are Owner or Admin only (useGatewayAdmin); these cases
// act as an Admin. AdminGating.test.jsx covers the other levels.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

// Fixture credential values for the stored servers below. They must never
// reach the page.
const STORED_TOKEN = "stored-bearer-value-0001";
const STORED_KEY = "stored-api-key-value-0002";

const { configQuery, mockUpdateMutate, snackbars } = vi.hoisted(() => ({
  configQuery: { current: {} },
  mockUpdateMutate: vi.fn(),
  snackbars: [],
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => configQuery.current,
}));
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({
    data: {
      enabled: true,
      servers: [
        { server_id: "github", status: "healthy", tool_count: 2 },
        { server_id: "search", status: "healthy", tool_count: 1 },
      ],
    },
  }),
  useMCPTools: () => ({
    data: [{ name: "github_create_issue", server: "github" }],
    isLoading: false,
  }),
  useRemoveMCPServer: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateMCPServer: () => ({ mutate: mockUpdateMutate, isPending: false }),
}));

const storedConfig = {
  mcp: {
    servers: {
      github: {
        url: "https://mcp.example.com/github",
        transport: "http",
        tools_cache_ttl: "5m",
        auth: { type: "bearer", token: STORED_TOKEN },
      },
      search: {
        url: "https://mcp.example.com/search",
        transport: "http",
        auth: { type: "api_key", header: "X-Search-Key", key: STORED_KEY },
      },
      open: { url: "https://mcp.example.com/open", transport: "http" },
      docs: {
        url: "https://mcp.example.com/docs",
        transport: "http",
        auth: { type: "bearer" },
      },
    },
  },
};

function cardFor(serverId) {
  return screen
    .getAllByRole("heading", { name: serverId })[0]
    .closest(".MuiCard-root");
}

function expectNoStoredSecretOnPage() {
  const html = document.body.innerHTML;
  expect(html).not.toContain(STORED_TOKEN);
  expect(html).not.toContain(STORED_KEY);
  expect(screen.queryByDisplayValue(STORED_TOKEN)).toBeNull();
  expect(screen.queryByDisplayValue(STORED_KEY)).toBeNull();
}

describe("ServersTab", () => {
  beforeEach(() => {
    configQuery.current = { data: storedConfig, isLoading: false };
    mockUpdateMutate.mockClear();
    snackbars.length = 0;
  });

  it("edit dialog never renders a stored token", async () => {
    const user = userEvent.setup();
    render(<ServersTab gatewayId="gw-1" />);

    await user.click(
      within(cardFor("github")).getByRole("button", { name: /edit/i }),
    );

    expect(screen.getByText("Edit MCP Server")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Bearer Token", { selector: "input" }),
    ).toHaveValue("");
    expect(screen.getByText(/Stored credential: set/)).toBeInTheDocument();
    expectNoStoredSecretOnPage();
  });

  it("edit dialog never renders a stored API key", async () => {
    const user = userEvent.setup();
    render(<ServersTab gatewayId="gw-1" />);

    await user.click(
      within(cardFor("search")).getByRole("button", { name: /edit/i }),
    );

    expect(
      screen.getByLabelText("Header Name", { selector: "input" }),
    ).toHaveValue("X-Search-Key");
    expect(screen.getByLabelText("API Key", { selector: "input" })).toHaveValue(
      "",
    );
    expect(screen.getByText(/Stored credential: set/)).toBeInTheDocument();
    expectNoStoredSecretOnPage();
  });

  it("sends a credential only when the user types one", async () => {
    const user = userEvent.setup();
    render(<ServersTab gatewayId="gw-1" />);

    await user.click(
      within(cardFor("github")).getByRole("button", { name: /edit/i }),
    );
    await user.click(screen.getByRole("button", { name: "Update Server" }));

    // Saving replaces the server entry, so an empty field
    // would drop the stored token: the dialog asks for it instead.
    expect(mockUpdateMutate).not.toHaveBeenCalled();
    expect(snackbars).toEqual([
      {
        message: "Enter the bearer token to save this server",
        options: { variant: "warning" },
      },
    ]);

    await user.type(
      screen.getByLabelText("Bearer Token", { selector: "input" }),
      "new-value-0003",
    );
    await user.click(screen.getByRole("button", { name: "Update Server" }));

    expect(mockUpdateMutate.mock.calls[0][0]).toEqual({
      gatewayId: "gw-1",
      serverId: "github",
      config: {
        transport: "http",
        url: "https://mcp.example.com/github",
        auth: { type: "bearer", token: "new-value-0003" },
      },
    });
  });

  it("shows whether each server's credential is set, never its value", () => {
    render(<ServersTab gatewayId="gw-1" />);

    expect(
      within(cardFor("github")).getByText("Credential: set"),
    ).toBeInTheDocument();
    expect(
      within(cardFor("search")).getByText("Credential: set"),
    ).toBeInTheDocument();
    expect(
      within(cardFor("docs")).getByText("Credential: not set"),
    ).toBeInTheDocument();
    expect(within(cardFor("open")).queryByText(/Credential:/)).toBeNull();
    expectNoStoredSecretOnPage();
  });

  it("sends an API key only when the user types one", async () => {
    const user = userEvent.setup();
    render(<ServersTab gatewayId="gw-1" />);

    await user.click(
      within(cardFor("search")).getByRole("button", { name: /edit/i }),
    );
    await user.click(screen.getByRole("button", { name: "Update Server" }));

    expect(mockUpdateMutate).not.toHaveBeenCalled();
    expect(snackbars.map(({ message }) => message)).toEqual([
      "Enter the API key to save this server",
    ]);

    await user.type(
      screen.getByLabelText("API Key", { selector: "input" }),
      "new-key-value-0005",
    );
    await user.click(screen.getByRole("button", { name: "Update Server" }));

    expect(mockUpdateMutate.mock.calls[0][0]).toEqual({
      gatewayId: "gw-1",
      serverId: "search",
      config: {
        transport: "http",
        url: "https://mcp.example.com/search",
        auth: {
          type: "api_key",
          header: "X-Search-Key",
          key: "new-key-value-0005",
        },
      },
    });
  });

  it("does not show the tools cache TTL the gateway ignores", () => {
    render(<ServersTab gatewayId="gw-1" />);

    expect(screen.queryByText(/Cache TTL/)).toBeNull();
  });

  it("lists the servers' tools on the servers tab", () => {
    render(<ServersTab gatewayId="gw-1" />);

    expect(screen.getByRole("heading", { name: "Tools" })).toBeInTheDocument();
    expect(screen.getByText("github_create_issue")).toBeInTheDocument();
  });

  it("opens an empty add dialog", async () => {
    const user = userEvent.setup();
    render(<ServersTab gatewayId="gw-1" />);

    await user.click(screen.getByRole("button", { name: "Add Server" }));

    expect(screen.getByText("Add MCP Server")).toBeInTheDocument();
    expect(screen.getByLabelText(/Server ID/)).toHaveValue("");
    expect(screen.queryByText(/Stored credential/)).toBeNull();
  });
});
