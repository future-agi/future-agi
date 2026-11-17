import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent } from "src/utils/test-utils";

import CustomizePanel from "../CustomizePanel";

// Same seam as customize-connector-editor.test.jsx: the editor reaches the
// server only through these.
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

const NAME = "Docs";
const SERVER_URL = "https://mcp.example.com/mcp";
const CLIENT_ID = "client-id-example";
const CLIENT_SECRET = "client-secret-example";
const ID_LABEL = "OAuth Client ID (optional)";
const SECRET_LABEL = "OAuth Client Secret (optional)";

// What POST /mcp-connectors/ answers with.
const SAVED = {
  id: "conn-new",
  name: NAME,
  server_url: SERVER_URL,
  transport: "streamable_http",
  auth_type: "oauth",
  auth_header_name: "Authorization",
  oauth_client_id: CLIENT_ID,
  has_oauth_client_secret: true,
  is_active: true,
  is_verified: false,
  discovered_tools: [],
  enabled_tool_names: [],
  last_error: "",
};

// An OAuth connector that has its own client saved, as the list and the
// detail route return it. The secret itself is never returned.
const LIST_ROW = {
  id: "conn-3",
  name: "Reports",
  server_url: "https://reports.example.com/mcp",
  transport: "streamable_http",
  auth_type: "oauth",
  is_active: true,
  is_verified: true,
  tool_count: 1,
};

const DETAIL = {
  ...LIST_ROW,
  auth_header_name: "Authorization",
  oauth_client_id: CLIENT_ID,
  has_oauth_client_secret: true,
  discovered_tools: [{ name: "list_reports", description: "List reports." }],
  enabled_tool_names: ["list_reports"],
};

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

const openEditConnector = async (detail = DETAIL) => {
  mocks.fetchConnectors.mockResolvedValue({ results: [LIST_ROW] });
  mocks.getConnector.mockResolvedValue(detail);
  renderPanel();
  fireEvent.click(await screen.findByText("Connectors"));
  fireEvent.click(await screen.findByText(LIST_ROW.name));
  await screen.findByText("list_reports");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  await screen.findByText("Edit connector");
};

const type = (placeholder, value) =>
  fireEvent.change(screen.getByPlaceholderText(placeholder), {
    target: { value },
  });

const typeInto = (label, value) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

const chooseOAuth = () =>
  fireEvent.change(screen.getByLabelText("Authentication"), {
    target: { value: "oauth" },
  });

let openSpy;

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listSkills.mockResolvedValue({ results: [] });
  mocks.fetchConnectors.mockResolvedValue({ results: [] });
  mocks.createConnector.mockResolvedValue({ status: true, result: SAVED });
  mocks.updateConnector.mockResolvedValue({ status: true, result: DETAIL });
  mocks.authenticateConnector.mockResolvedValue({
    status: true,
    auth_type: "oauth",
    authorization_url: "https://auth.example.com/authorize",
    message: "Open the authorization URL to authenticate.",
  });
  openSpy = vi.spyOn(window, "open").mockImplementation(() => ({}));
});

afterEach(() => {
  openSpy.mockRestore();
});

describe("Customize > the connector's own OAuth client", () => {
  it("turns the client ID and secret on for an OAuth connector", async () => {
    await openAddConnector();
    chooseOAuth();

    for (const label of [ID_LABEL, SECRET_LABEL]) {
      expect(screen.getByLabelText(label)).toBeEnabled();
    }
    expect(screen.getByLabelText(SECRET_LABEL)).toHaveAttribute(
      "type",
      "password",
    );
    expect(screen.queryByText("Not available in this release")).toBeNull();
  });

  it("sends the client ID and secret typed when adding a connector", async () => {
    await openAddConnector();
    type("Name", NAME);
    type("Remote MCP server URL", SERVER_URL);
    chooseOAuth();
    typeInto(ID_LABEL, CLIENT_ID);
    typeInto(SECRET_LABEL, CLIENT_SECRET);

    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(mocks.createConnector).toHaveBeenCalledTimes(1));
    expect(mocks.createConnector).toHaveBeenCalledWith({
      name: NAME,
      server_url: SERVER_URL,
      auth_type: "oauth",
      oauth_client_id: CLIENT_ID,
      oauth_client_secret: CLIENT_SECRET,
    });
  });

  it("asks for the client ID when only a secret is typed", async () => {
    await openAddConnector();
    type("Name", NAME);
    type("Remote MCP server URL", SERVER_URL);
    chooseOAuth();
    typeInto(SECRET_LABEL, CLIENT_SECRET);

    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(
      await screen.findByText("Enter the OAuth client ID for this secret"),
    ).toBeInTheDocument();
    expect(mocks.createConnector).not.toHaveBeenCalled();
  });

  it("shows the saved client ID and says a secret is saved, never its value", async () => {
    await openEditConnector();

    expect(screen.getByLabelText(ID_LABEL)).toHaveValue(CLIENT_ID);
    const secret = screen.getByLabelText(SECRET_LABEL);
    expect(secret).toHaveValue("");
    expect(secret).toHaveAttribute(
      "placeholder",
      "Saved (leave blank to keep it)",
    );
    expect(secret).toHaveAccessibleDescription(
      "A client secret is saved. Leave the field blank to keep it.",
    );
    expect(screen.queryByDisplayValue(CLIENT_SECRET)).toBeNull();
  });

  it("does not say a secret is saved when none is", async () => {
    await openEditConnector({ ...DETAIL, has_oauth_client_secret: false });

    expect(screen.getByLabelText(SECRET_LABEL)).toHaveAttribute(
      "placeholder",
      SECRET_LABEL,
    );
    expect(screen.queryByText(/secret is saved/)).toBeNull();
  });

  it("keeps the saved client when the fields are left as they were", async () => {
    await openEditConnector();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mocks.updateConnector).toHaveBeenCalledTimes(1));
    const [, payload] = mocks.updateConnector.mock.calls[0];
    expect(payload).not.toHaveProperty("oauth_client_id");
    expect(payload).not.toHaveProperty("oauth_client_secret");
  });

  it("sends a new secret for the saved client", async () => {
    await openEditConnector();
    typeInto(SECRET_LABEL, "other-client-secret-example");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mocks.updateConnector).toHaveBeenCalledTimes(1));
    const [id, payload] = mocks.updateConnector.mock.calls[0];
    expect(id).toBe("conn-3");
    expect(payload.oauth_client_secret).toBe("other-client-secret-example");
    expect(payload).not.toHaveProperty("oauth_client_id");
  });

  it("sends a changed client ID, and stops saying a secret is saved", async () => {
    await openEditConnector();
    typeInto(ID_LABEL, "other-client-id-example");

    // The server drops the old client's secret with a new client ID.
    expect(screen.getByLabelText(SECRET_LABEL)).toHaveAttribute(
      "placeholder",
      SECRET_LABEL,
    );
    typeInto(SECRET_LABEL, "other-client-secret-example");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mocks.updateConnector).toHaveBeenCalledTimes(1));
    const [, payload] = mocks.updateConnector.mock.calls[0];
    expect(payload.oauth_client_id).toBe("other-client-id-example");
    expect(payload.oauth_client_secret).toBe("other-client-secret-example");
  });

  it("sends a blank client ID to remove the saved client", async () => {
    await openEditConnector();
    typeInto(ID_LABEL, "");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mocks.updateConnector).toHaveBeenCalledTimes(1));
    const [, payload] = mocks.updateConnector.mock.calls[0];
    expect(payload.oauth_client_id).toBe("");
    expect(payload).not.toHaveProperty("oauth_client_secret");
  });
});
