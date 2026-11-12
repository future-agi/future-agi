import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent, act } from "src/utils/test-utils";

import CustomizePanel from "../CustomizePanel";

// The Customize connector editor reaches the server only through these. It
// imports createConnector/updateConnector/authenticateConnector lazily;
// vi.mock covers that too.
const mocks = vi.hoisted(() => ({
  listSkills: vi.fn(),
  getSkill: vi.fn(),
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

// Every key MCPConnectorCreateSerializer accepts. It is a plain serializer, so
// anything else (api_key, oauth_client_id, oauth_client_secret) is dropped
// without a word, and the PATCH contract answers "Unknown field." instead.
const CONTRACT_KEYS = [
  "name",
  "server_url",
  "transport",
  "auth_type",
  "auth_header_name",
  "auth_header_value",
];

const NAME = "Docs";
const SERVER_URL = "https://mcp.example.com/mcp";

// What POST /mcp-connectors/ answers with: MCPConnectorDetailSerializer.
const SAVED = {
  id: "conn-new",
  name: NAME,
  server_url: SERVER_URL,
  transport: "streamable_http",
  auth_type: "none",
  auth_header_name: "Authorization",
  is_active: true,
  is_verified: false,
  discovered_tools: [],
  enabled_tool_names: [],
  last_error: "",
};

// An API-key connector as the list returns it (no header name, no tools)...
const LIST_ROW = {
  id: "conn-1",
  name: "Linear",
  server_url: "https://mcp.linear.app/mcp",
  transport: "streamable_http",
  auth_type: "api_key",
  is_active: true,
  is_verified: true,
  tool_count: 1,
};

// ...and as its detail route returns it. The secret is write-only.
const DETAIL = {
  ...LIST_ROW,
  auth_header_name: "X-API-Key",
  discovered_tools: [{ name: "list_issues", description: "List issues." }],
  enabled_tool_names: ["list_issues"],
};

// An OAuth connector waiting for its sign-in, as the list and detail routes
// return it, and its detail once the sign-in has finished.
const OAUTH_ROW = {
  id: "conn-2",
  name: "GitHub",
  server_url: "https://api.githubcopilot.com/mcp/",
  transport: "streamable_http",
  auth_type: "oauth",
  is_active: true,
  is_verified: false,
  tool_count: 0,
};

const OAUTH_PENDING = {
  ...OAUTH_ROW,
  auth_header_name: "Authorization",
  discovered_tools: [],
  enabled_tool_names: [],
};

const OAUTH_SIGNED_IN = {
  ...OAUTH_PENDING,
  is_verified: true,
  tool_count: 1,
  discovered_tools: [{ name: "list_prs", description: "List pull requests." }],
  enabled_tool_names: ["list_prs"],
};

const SIGN_IN_BLOCKED =
  "Your browser blocked the sign-in window. Allow pop-ups for this site, then click Re-authenticate.";

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

const openAddConnector = async () => {
  renderPanel();
  fireEvent.click(await screen.findByText("Connectors"));
  await screen.findByText("No connectors added yet");
  fireEvent.click(screen.getByTitle("Add connector"));
  await screen.findByText("Add custom connector");
};

const openEditConnector = async () => {
  mocks.fetchConnectors.mockResolvedValue({ results: [LIST_ROW] });
  mocks.getConnector.mockResolvedValue(DETAIL);
  mocks.authenticateConnector.mockResolvedValue({
    status: true,
    result: DETAIL,
    message: "Connected successfully",
  });
  renderPanel();
  fireEvent.click(await screen.findByText("Connectors"));
  fireEvent.click(await screen.findByText("Linear"));
  // The detail record carries the header name the editor starts from.
  await screen.findByText("list_issues");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  await screen.findByText("Edit connector");
};

const type = (placeholder, value) =>
  fireEvent.change(screen.getByPlaceholderText(placeholder), {
    target: { value },
  });

const chooseAuth = (value) =>
  fireEvent.change(screen.getByLabelText("Authentication"), {
    target: { value },
  });

const fillNameAndUrl = () => {
  type("Name", NAME);
  type("Remote MCP server URL", SERVER_URL);
};

const expectContracted = (payload) =>
  expect(
    Object.keys(payload).filter((key) => !CONTRACT_KEYS.includes(key)),
  ).toEqual([]);

let openSpy;

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listSkills.mockResolvedValue({ results: [] });
  mocks.fetchConnectors.mockResolvedValue({ results: [] });
  mocks.createConnector.mockResolvedValue({ status: true, result: SAVED });
  mocks.updateConnector.mockResolvedValue({ status: true, result: DETAIL });
  mocks.authenticateConnector.mockResolvedValue({
    status: true,
    result: { ...SAVED, is_verified: true },
    message: "Connected successfully",
  });
  openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
});

afterEach(() => {
  openSpy.mockRestore();
});

describe("Customize > Add custom connector stores the credentials it asks for", () => {
  it("saves a pasted bearer token as the connector's credential", async () => {
    await openAddConnector();
    fillNameAndUrl();
    chooseAuth("bearer");
    type("Bearer token", "tok-123");

    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(mocks.createConnector).toHaveBeenCalledTimes(1));
    // The old form sent this as api_key, which the create endpoint dropped,
    // leaving a connector with auth_type "none" and no token.
    expect(mocks.createConnector).toHaveBeenCalledWith({
      name: NAME,
      server_url: SERVER_URL,
      auth_type: "bearer",
      auth_header_value: "tok-123",
    });
  });

  it("saves an API key with the header the server reads it from", async () => {
    await openAddConnector();
    fillNameAndUrl();
    chooseAuth("api_key");
    type("Header name, e.g. X-API-Key", "X-API-Key");
    type("API key", "key-123");

    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(mocks.createConnector).toHaveBeenCalledTimes(1));
    expect(mocks.createConnector).toHaveBeenCalledWith({
      name: NAME,
      server_url: SERVER_URL,
      auth_type: "api_key",
      auth_header_name: "X-API-Key",
      auth_header_value: "key-123",
    });
  });

  it("offers OAuth, with the client ID and secret shown turned off, and sends neither", async () => {
    mocks.createConnector.mockResolvedValue({
      status: true,
      result: { ...SAVED, auth_type: "oauth" },
    });
    mocks.authenticateConnector.mockResolvedValue({
      status: true,
      auth_type: "oauth",
      authorization_url: "https://auth.example.com/authorize",
      message: "Open the authorization URL to authenticate.",
    });
    await openAddConnector();
    fillNameAndUrl();
    chooseAuth("oauth");

    // Nothing would store them: Falcon registers its own client. Nothing is
    // removed: both fields are shown, turned off, with the reason under them.
    expect(
      screen.getByText(
        "After you save, Falcon registers itself with the server and opens its sign-in page in a new window.",
      ),
    ).toBeInTheDocument();
    for (const label of [
      "OAuth Client ID (optional)",
      "OAuth Client Secret (optional)",
    ]) {
      const field = screen.getByLabelText(label);
      expect(field).toHaveAttribute("placeholder", label);
      expect(field).toBeDisabled();
      expect(field).toHaveAccessibleDescription(
        "Not available in this release",
      );
    }
    expect(
      screen.getByLabelText("OAuth Client Secret (optional)"),
    ).toHaveAttribute("type", "password");
    expect(screen.queryByText(/secret yet/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(mocks.createConnector).toHaveBeenCalledTimes(1));
    const [payload] = mocks.createConnector.mock.calls[0];
    expectContracted(payload);
    expect(payload).toEqual({
      name: NAME,
      server_url: SERVER_URL,
      auth_type: "oauth",
    });
    await waitFor(() =>
      expect(openSpy).toHaveBeenCalledWith(
        "https://auth.example.com/authorize",
        "falcon_oauth",
        expect.any(String),
      ),
    );
  });

  it("asks for the token instead of saving a connector without one", async () => {
    await openAddConnector();
    fillNameAndUrl();
    chooseAuth("bearer");

    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(
      await screen.findByText("Enter the bearer token"),
    ).toBeInTheDocument();
    expect(mocks.createConnector).not.toHaveBeenCalled();
  });

  it("asks for the header name an API key is sent in", async () => {
    await openAddConnector();
    fillNameAndUrl();
    chooseAuth("api_key");
    type("API key", "key-123");

    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(
      await screen.findByText("Enter the header name for the API key"),
    ).toBeInTheDocument();
    expect(mocks.createConnector).not.toHaveBeenCalled();
  });
});

describe("Customize > Add custom connector says why a new connector did not connect", () => {
  it("shows an authentication failure on the connector it saved", async () => {
    // Shape the axios interceptor rejects with: the error body, spread flat.
    mocks.authenticateConnector.mockRejectedValue({
      status: false,
      detail: "OAuth setup failed",
      message: "OAuth setup failed",
      error: "OAuth setup failed",
      result: "OAuth setup failed",
      statusCode: 502,
    });
    await openAddConnector();
    fillNameAndUrl();

    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() =>
      expect(mocks.authenticateConnector).toHaveBeenCalledWith(SAVED.id),
    );
    // It used to be swallowed after the editor had already closed onto an
    // empty pane, so the connector just sat at Pending.
    expect(await screen.findByText("OAuth setup failed")).toBeInTheDocument();
    expect(screen.queryByText("Add custom connector")).toBeNull();
    expect(screen.getAllByText(NAME).length).toBeGreaterThan(0);
  });

  it("shows a failed connection test on the connector it saved", async () => {
    mocks.authenticateConnector.mockResolvedValue({
      status: false,
      result: { ...SAVED, last_error: "Connection failed" },
      message: "MCP request failed",
    });
    await openAddConnector();
    fillNameAndUrl();

    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(await screen.findByText("Connection failed")).toBeInTheDocument();
    expect(screen.getByText("Pending")).toBeInTheDocument();
  });

  it("says when the browser blocks the sign-in window, until one opens", async () => {
    // The server answered 401, so authenticate switched it to OAuth and sent
    // back the sign-in page.
    mocks.authenticateConnector.mockResolvedValue({
      status: true,
      auth_type: "oauth",
      authorization_url: "https://auth.example.com/authorize",
      message: "Open the authorization URL to authenticate.",
    });
    await openAddConnector();
    fillNameAndUrl();

    // The spy answers null, as window.open does when a pop-up blocker stops
    // it. Nothing used to say why no sign-in window came.
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(await screen.findByText(SIGN_IN_BLOCKED)).toBeInTheDocument();

    openSpy.mockImplementation(() => ({ closed: false }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Re-authenticate" }),
    );

    await waitFor(() => expect(openSpy).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.queryByText(SIGN_IN_BLOCKED)).not.toBeInTheDocument(),
    );
  });
});

describe("Customize > a connector that is still connecting", () => {
  it("says it is connecting, and holds Edit until the answer arrives", async () => {
    let answer;
    mocks.authenticateConnector.mockImplementation(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    await openAddConnector();
    fillNameAndUrl();

    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    // The connection test can take ten seconds or more. The editor used to
    // close onto an empty pane meanwhile. Now the pane says it is connecting
    // and holds Edit, so the answer can't land on a form being typed in.
    const connecting = await screen.findByRole("button", {
      name: "Connecting...",
    });
    expect(connecting).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(
      screen.getByText(
        "Connecting to the server. Tools will appear here once it answers.",
      ),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(mocks.authenticateConnector).toHaveBeenCalledWith(SAVED.id),
    );

    await act(async () => {
      answer({
        status: false,
        result: { ...SAVED, last_error: "Connection failed" },
        message: "MCP request failed",
      });
    });

    expect(await screen.findByText("Connection failed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Re-authenticate" }),
    ).toBeEnabled();
  });

  it("keeps the editor, and what was typed, when an OAuth sign-in finishes", async () => {
    mocks.fetchConnectors.mockResolvedValue({ results: [OAUTH_ROW] });
    mocks.getConnector.mockResolvedValueOnce(OAUTH_PENDING);
    mocks.discoverConnectorTools.mockResolvedValue({});
    renderPanel();
    fireEvent.click(await screen.findByText("Connectors"));
    fireEvent.click(await screen.findByText(OAUTH_ROW.name));
    await screen.findByText(/No tools discovered yet/);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    await screen.findByText("Edit connector");
    type("Name", "GitHub (work)");

    // Each read answers with a new record, as the server does once the
    // sign-in has stored a token and found the tools.
    mocks.getConnector.mockImplementation(async () => ({ ...OAUTH_SIGNED_IN }));
    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "falcon_oauth_callback", status: "success" },
        }),
      );
    });
    await waitFor(() => expect(mocks.getConnector).toHaveBeenCalledTimes(2));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    // The callback used to re-select the connector, which closed the editor,
    // and a refreshed record refilled the form from the saved one.
    expect(screen.getByText("Edit connector")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Name")).toHaveValue("GitHub (work)");

    // The record under the editor is the signed-in one.
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("list_prs")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
  });
});

describe("Customize > Edit connector sends only fields the API accepts", () => {
  it("replaces a saved API key with a new one", async () => {
    await openEditConnector();

    expect(screen.getByLabelText("Authentication")).toHaveValue("api_key");
    expect(
      screen.getByPlaceholderText("Header name, e.g. X-API-Key"),
    ).toHaveValue("X-API-Key");
    type("API key (leave blank to keep the saved one)", "key-456");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mocks.updateConnector).toHaveBeenCalledTimes(1));
    // The old editor sent api_key here, which PATCH refuses as an unknown
    // field.
    expect(mocks.updateConnector).toHaveBeenCalledWith("conn-1", {
      name: "Linear",
      server_url: "https://mcp.linear.app/mcp",
      auth_type: "api_key",
      auth_header_name: "X-API-Key",
      auth_header_value: "key-456",
    });
  });

  it("keeps the saved API key when the field is left blank", async () => {
    await openEditConnector();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mocks.updateConnector).toHaveBeenCalledTimes(1));
    const [, payload] = mocks.updateConnector.mock.calls[0];
    expectContracted(payload);
    // A blank value would overwrite the stored key with nothing.
    expect(payload).not.toHaveProperty("auth_header_value");
    expect(payload).toEqual({
      name: "Linear",
      server_url: "https://mcp.linear.app/mcp",
      auth_type: "api_key",
      auth_header_name: "X-API-Key",
    });
  });

  it("asks for a token when switching to bearer, as there is none saved", async () => {
    await openEditConnector();
    chooseAuth("bearer");

    // No "leave blank" offer: no bearer token was ever saved.
    expect(screen.getByPlaceholderText("Bearer token")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText("Enter the bearer token"),
    ).toBeInTheDocument();
    expect(mocks.updateConnector).not.toHaveBeenCalled();
  });
});
