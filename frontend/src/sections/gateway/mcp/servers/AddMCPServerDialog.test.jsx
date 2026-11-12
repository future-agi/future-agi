import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderMcp } from "../testing/renderMcp";
import { MCP_API } from "../api";
import AddMCPServerDialog from "./AddMCPServerDialog";

const mockMutate = vi.fn();
let mockMutationState = {};
const { snackbars, api } = vi.hoisted(() => ({
  snackbars: [],
  api: { post: vi.fn() },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));
vi.mock("../hooks/useMCPConfig", () => ({
  useUpdateMCPServer: () => ({
    mutate: mockMutate,
    isPending: false,
    ...mockMutationState,
  }),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, post: api.post } };
});

const STORED_TOKEN = "stored-bearer-value-0001";

function open(props = {}) {
  return renderMcp(
    <AddMCPServerDialog
      open
      onClose={vi.fn()}
      gatewayId="default"
      {...props}
    />,
  );
}

async function chooseAuth(user, name) {
  await user.click(screen.getByRole("combobox", { name: /Authentication/ }));
  await user.click(await screen.findByRole("option", { name }));
}

beforeEach(() => {
  mockMutate.mockReset();
  mockMutationState = {};
  snackbars.length = 0;
  api.post.mockReset();
});

describe("MCP setup completion", () => {
  const editServer = {
    serverId: "crm-tools",
    config: {
      url: "https://example.test/mcp", transport: "http", enabled: false,
      timeout_ms: 12000, era: "auto", forward_headers: ["x-tenant"],
      labels: ["private_data"], propagate_depth: true,
      trusted_annotations: ["readOnlyHint"],
      credential: { mode: "shared", credential_id: "c1", state: "active" },
    },
  };
  const passed = { data: { result: { ok: true, tool_count: 2, latency_ms: 9 } } };

  it("restores a disabled server in one update without rotating or resending credentials", () => {
    open({ editServer });
    const enabled = screen.getByRole("checkbox", { name: "Enabled" });
    expect(enabled).not.toBeChecked();
    fireEvent.click(enabled);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const { credential, ...settings } = editServer.config;
    expect(mockMutate).toHaveBeenCalledOnce();
    expect(mockMutate.mock.calls[0][0]).toEqual({
      gatewayId: "default", serverId: "crm-tools", config: { ...settings, enabled: true },
    });
    expect(mockMutate.mock.calls[0][0].config).not.toHaveProperty("auth");
    expect(api.post).not.toHaveBeenCalled();
  });

  it("does not claim an enabled setting turns the deployment runtime on", () => {
    open({ editServer, runtimeOff: true });
    expect(screen.getByText(/Saving does not turn MCP on/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "Enabled" }));
    expect(screen.getByRole("button", { name: "Test connection" })).toBeDisabled();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("keeps saved-but-not-applied distinct from a completed connection", () => {
    mockMutationState = {
      isError: true,
      error: { savedButNotApplied: true, message: "Saved, but the gateway did not apply it: gateway unavailable." },
    };
    open({ editServer });
    expect(screen.getByRole("alert")).toHaveTextContent("Saved, but the gateway did not apply it");
    expect(screen.queryByText("Connected")).toBeNull();
    expect(snackbars.some(({ options }) => options.variant === "success")).toBe(false);
  });

  it("does not emit a success message when an update is saved but not synced", () => {
    const onClose = vi.fn();
    mockMutate.mockImplementation((_, { onSuccess }) => onSuccess({ gateway_synced: false }));
    open({ editServer, onClose });
    fireEvent.click(screen.getByRole("checkbox", { name: "Enabled" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mockMutate).toHaveBeenCalledOnce();
    expect(snackbars).toEqual([{
      message: "Saved. The gateway has not picked it up yet.", options: { variant: "warning" },
    }]);
  });

  it("keeps server changes and probing disabled for a non-admin", () => {
    open({ editServer, canEdit: false });
    expect(screen.getByRole("checkbox", { name: "Enabled" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeDisabled();
    expect(mockMutate).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("cancels a successful unsaved test without updating any server", async () => {
    api.post.mockResolvedValue(passed);
    const onClose = vi.fn();
    open({ onClose });
    fireEvent.change(screen.getByLabelText(/URL/), { target: { value: "https://example.test/mcp" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Test passed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save server" })).toBeEnabled();
    expect(screen.queryByText("Connected")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(mockMutate).not.toHaveBeenCalled();
    expect(api.post.mock.calls.map(([url]) => url)).toEqual([MCP_API.probe]);
  });

  it.each(["URL", "Authentication", "Token", "Header name", "Key"])("invalidates a successful probe when %s changes", async (field) => {
    api.post.mockResolvedValue(passed);
    const user = userEvent.setup();
    open();
    fireEvent.change(screen.getByLabelText(/URL/), { target: { value: "https://example.test/mcp" } });
    if (field === "Token") await chooseAuth(user, "Bearer token");
    if (["Header name", "Key"].includes(field)) await chooseAuth(user, "API key");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Test passed")).toBeInTheDocument();
    if (field === "Authentication") await chooseAuth(user, "Bearer token");
    else fireEvent.change(screen.getByLabelText(new RegExp(`^${field}`)), { target: { value: "changed" } });
    expect(screen.queryByText("Test passed")).toBeNull();
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it("ignores a late probe response after its URL changes", async () => {
    let complete;
    api.post.mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
    open();
    fireEvent.change(screen.getByLabelText(/URL/), { target: { value: "https://example.test/mcp" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledOnce());
    fireEvent.change(screen.getByLabelText(/URL/), { target: { value: "https://other.test/mcp" } });
    await act(async () => complete(passed));
    expect(screen.queryByText("Test passed")).toBeNull();
    expect(screen.queryByText("Connected")).toBeNull();
  });
});

describe("AddMCPServerDialog", () => {
  it("shows the reason when the save is rejected", () => {
    mockMutationState = {
      isError: true,
      error: {
        message:
          "The gateway cannot accept this config. mcp.servers.github.command: Extra inputs are not permitted",
      },
    };
    open();
    expect(
      screen.getByText(/mcp\.servers\.github\.command: Extra inputs/),
    ).toBeInTheDocument();
  });

  it("asks for the server's MCP endpoint, not a base URL", () => {
    open();
    expect(screen.getByLabelText(/URL/)).toHaveAttribute(
      "placeholder",
      expect.stringMatching(/\/mcp$/),
    );
  });

  it("saves an HTTP server with only the fields the gateway config has", async () => {
    const user = userEvent.setup();
    open();
    await user.type(screen.getByLabelText(/Server ID/), "github");
    await user.type(screen.getByLabelText(/URL/), "http://mcp:8080/mcp");
    await user.click(screen.getByRole("button", { name: "Add server" }));

    expect(mockMutate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      serverId: "github",
      config: { transport: "http", url: "http://mcp:8080/mcp" },
    });
  });

  it("does not offer a stdio transport", () => {
    open();
    expect(screen.queryByLabelText(/Transport/)).toBeNull();
    expect(screen.queryByLabelText(/Command/)).toBeNull();
  });

  it("keeps a stored server's other settings on edit, never a command or the ignored cache TTL", async () => {
    const user = userEvent.setup();
    open({
      editServer: {
        serverId: "remote",
        config: {
          transport: "http",
          url: "http://old:1/mcp",
          enabled: true,
          timeout_ms: 10000,
          tools_cache_ttl: "5m",
          command: "npx thing",
        },
      },
    });
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(mockMutate.mock.calls[0][0].config).toEqual({
      transport: "http",
      url: "http://old:1/mcp",
      enabled: true,
      timeout_ms: 10000,
    });
  });

  it("never renders a stored token, and keeps it when left alone", async () => {
    const user = userEvent.setup();
    open({
      editServer: {
        serverId: "remote",
        config: {
          transport: "http",
          url: "http://old:1/mcp",
          auth: { type: "bearer", token: STORED_TOKEN },
        },
      },
    });
    expect(screen.getByText(/Stored credential: set/)).toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(STORED_TOKEN);

    await user.click(screen.getByRole("button", { name: "Save" }));
    // No auth in the request: the backend keeps the stored credential.
    expect(mockMutate.mock.calls[0][0].config).toEqual({
      transport: "http",
      url: "http://old:1/mcp",
    });
  });

  it("removes a stored credential only when asked", async () => {
    const user = userEvent.setup();
    open({
      editServer: {
        serverId: "remote",
        config: {
          transport: "http",
          url: "http://old:1/mcp",
          credential: { mode: "shared", credential_id: "c1", state: "active" },
        },
        hasCredential: true,
      },
    });
    await chooseAuth(user, /Remove the stored credential/);
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(mockMutate.mock.calls[0][0].config).toEqual({
      transport: "http",
      url: "http://old:1/mcp",
      auth: { type: "none" },
    });
  });

  it("says not set when the stored server has no credential", () => {
    open({
      editServer: {
        serverId: "remote",
        config: { transport: "http", url: "http://old:1/mcp" },
      },
    });
    expect(screen.getByText(/Stored credential: not set/)).toBeInTheDocument();
  });

  it("asks for the token before saving a bearer server", async () => {
    const user = userEvent.setup();
    open();
    await user.type(screen.getByLabelText(/Server ID/), "github");
    await user.type(screen.getByLabelText(/URL/), "http://mcp:8080/mcp");
    await chooseAuth(user, "Bearer token");
    await user.click(screen.getByRole("button", { name: "Add server" }));

    expect(mockMutate).not.toHaveBeenCalled();
    expect(snackbars.map(({ message }) => message)).toEqual([
      "Enter the bearer token to save this server",
    ]);

    await user.type(screen.getByLabelText(/^Token/), "typed-value-0004");
    await user.click(screen.getByRole("button", { name: "Add server" }));
    expect(mockMutate.mock.calls[0][0].config).toEqual({
      transport: "http",
      url: "http://mcp:8080/mcp",
      auth: { type: "bearer", token: "typed-value-0004" },
    });
  });

  it("sends an API key with its header", async () => {
    const user = userEvent.setup();
    open();
    await user.type(screen.getByLabelText(/Server ID/), "search");
    await user.type(screen.getByLabelText(/URL/), "https://s.example.com/mcp");
    await chooseAuth(user, "API key");
    await user.type(screen.getByLabelText(/Header name/), "X-Search-Key");
    await user.type(screen.getByLabelText(/^Key/), "k-1");
    await user.click(screen.getByRole("button", { name: "Add server" }));
    expect(mockMutate.mock.calls[0][0].config).toEqual({
      transport: "http",
      url: "https://s.example.com/mcp",
      auth: { type: "api_key", header: "X-Search-Key", key: "k-1" },
    });
  });

  it("has no tools cache TTL field", () => {
    open();
    expect(screen.queryByLabelText(/Cache TTL/)).toBeNull();
  });

  it("tests the connection through the gateway before saving", async () => {
    api.post.mockResolvedValue({
      data: {
        status: true,
        result: {
          ok: true,
          era: "2025-11-25",
          server_info: { name: "support-tools", version: "1.4.0" },
          tool_count: 4,
          latency_ms: 65,
          error: null,
        },
      },
    });
    const user = userEvent.setup();
    open();
    await user.type(
      screen.getByLabelText(/URL/),
      "http://demo-upstream:8080/mcp",
    );
    await chooseAuth(user, "Bearer token");
    await user.type(screen.getByLabelText(/^Token/), "tok");
    await user.click(screen.getByRole("button", { name: "Test connection" }));

    expect(
      await screen.findByText(
        "Test passed for support-tools 1.4.0: 4 tools, protocol 2025-11-25, 65ms. Save this server to keep the configuration.",
      ),
    ).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledWith(MCP_API.probe, {
      url: "http://demo-upstream:8080/mcp",
      transport: "http",
      auth: { type: "bearer", value: "tok" },
    });
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it("explains a URL that is not an MCP endpoint", async () => {
    api.post.mockResolvedValue({
      data: {
        status: true,
        result: { ok: false, error: "not_mcp", latency_ms: 14 },
      },
    });
    const user = userEvent.setup();
    open();
    await user.type(screen.getByLabelText(/URL/), "http://demo-upstream:8080");
    await user.click(screen.getByRole("button", { name: "Test connection" }));
    expect(
      await screen.findByText(/did not answer as an MCP server/),
    ).toBeInTheDocument();
  });

  it("tests an edited server with its stored vault credential", async () => {
    api.post.mockResolvedValue({
      data: {
        status: true,
        result: { ok: true, tool_count: 2, latency_ms: 9 },
      },
    });
    const user = userEvent.setup();
    open({
      editServer: {
        serverId: "crm-tools",
        config: {
          transport: "http",
          url: "http://demo-upstream:8080/mcp-crm",
          credential: { mode: "shared", credential_id: "c1", state: "active" },
        },
        hasCredential: true,
      },
    });
    await user.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(MCP_API.probe, {
        url: "http://demo-upstream:8080/mcp-crm",
        transport: "http",
        credential_id: "c1",
      }),
    );
  });

  it("shows a refused probe's reason", async () => {
    api.post.mockRejectedValue({
      response: {
        status: 403,
        data: { detail: "You do not have permission to perform this action." },
      },
    });
    const user = userEvent.setup();
    open();
    await user.type(screen.getByLabelText(/URL/), "http://x/mcp");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    const dialog = screen.getByRole("dialog");
    expect(
      await within(dialog).findByText(
        "You do not have permission to perform this action.",
      ),
    ).toBeInTheDocument();
  });
});
