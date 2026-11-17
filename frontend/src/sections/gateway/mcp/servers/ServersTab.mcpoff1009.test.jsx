import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { renderMcp } from "../testing/renderMcp";
import ServersTab from "./ServersTab";

const state = vi.hoisted(() => ({
  get: vi.fn(),
  save: vi.fn(),
  remove: vi.fn(),
  canEdit: true,
  servers: {},
}));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: state.get, post: vi.fn() },
}));
vi.mock("ag-grid-react", async () => ({
  AgGridReact: (await import("../testing/agGridMock")).MockAgGrid,
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: state.canEdit }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", async (original) => ({
  ...(await original()),
  useGatewayConfig: () => ({
    data: { mcp: { enabled: false, servers: state.servers } },
  }),
}));
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({
    data: { enabled: false, reason: "gateway_mcp_disabled", servers: [] },
  }),
  useMCPTools: () => ({ data: [] }),
  useMCPResources: () => ({ data: [] }),
  useMCPPrompts: () => ({ data: [] }),
  useRemoveMCPServer: () => ({ mutate: state.remove }),
  useUpdateMCPServer: () => ({ mutate: state.save }),
}));
beforeEach(() => {
  state.canEdit = true;
  state.servers = {
    alpha: { url: "https://alpha.example/mcp", transport: "http" },
  };
  state.get.mockReset().mockRejectedValue(new Error("Unexpected request"));
  state.save.mockReset();
  state.remove.mockReset();
});

describe("RL-1009 saved server controls in the off shell", () => {
  it.each(["Owner", "Admin", "Member", "Reader"])(
    "keeps saved Edit and Remove permissions for %s without new drawer surfaces",
    async (role) => {
      state.canEdit = ["Owner", "Admin"].includes(role);
      renderMcp(<ServersTab gatewayId="gw" runtimeOff />);
      fireEvent.click(screen.getByRole("button", { name: /^alpha\b/ }));
      const drawer = await screen.findByRole("dialog", { name: "alpha" });
      const edit = within(drawer).getByRole("button", { name: "Edit" });
      const remove = within(drawer).getByRole("button", { name: "Remove" });
      expect(within(drawer).queryByText("Recent tool calls")).toBeNull();
      expect(
        within(drawer).queryByRole("button", { name: "Manage credentials" }),
      ).toBeNull();
      expect(
        within(drawer).getByText(/Live MCP operations are unavailable/),
      ).toBeInTheDocument();
      expect(state.get).not.toHaveBeenCalled();
      if (!state.canEdit) {
        expect(edit).toBeDisabled();
        expect(remove).toBeDisabled();
        fireEvent.click(edit);
        fireEvent.click(remove);
        expect(state.save).not.toHaveBeenCalled();
        expect(state.remove).not.toHaveBeenCalled();
        return;
      }
      expect(edit).toBeEnabled();
      expect(remove).toBeEnabled();
      fireEvent.click(edit);
      const dialog = await screen.findByRole("dialog", { name: "Edit alpha" });
      fireEvent.change(within(dialog).getByLabelText(/URL/), {
        target: { value: "https://alpha.example/new" },
      });
      fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
      expect(state.save).toHaveBeenCalledWith(
        {
          gatewayId: "gw",
          serverId: "alpha",
          config: {
            transport: "http",
            url: "https://alpha.example/new",
          },
        },
        expect.any(Object),
      );
      fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
      fireEvent.click(remove);
      const confirm = await screen.findByRole("dialog", {
        name: "Remove MCP server",
      });
      fireEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
      expect(state.remove).not.toHaveBeenCalled();
      fireEvent.click(remove);
      fireEvent.click(
        await screen.findByRole("button", { name: "Remove server" }),
      );
      expect(state.remove).toHaveBeenCalledWith(
        { gatewayId: "gw", serverId: "alpha" },
        expect.any(Object),
      );
    },
  );
  it("retains saved Disabled state and the Disabled filter while runtime is off", () => {
    state.servers.paused = {
      url: "https://paused.example/mcp",
      enabled: false,
    };
    renderMcp(<ServersTab gatewayId="gw" runtimeOff />);
    expect(
      screen.getByRole("button", { name: "paused Disabled" }),
    ).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Status" }));
    fireEvent.click(screen.getByRole("option", { name: "Disabled" }));
    expect(
      screen.getByRole("button", { name: "paused Disabled" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^alpha\b/ })).toBeNull();
    expect(state.get).not.toHaveBeenCalled();
  });
  it("shows the unavailable runtime once per saved server card", () => {
    renderMcp(<ServersTab gatewayId="gw" runtimeOff />);
    const card = screen.getByRole("button", { name: /^alpha\b/ });
    expect(within(card).getAllByText("Runtime unavailable")).toHaveLength(1);
    expect(within(card).queryByText("0 tools")).toBeNull();
  });
  it.each([true, false])("opens ?add=1 only for an editor (%s)", (canEdit) => {
    state.canEdit = canEdit;
    renderMcp(<ServersTab gatewayId="gw" runtimeOff />, { route: "/?add=1" });
    if (canEdit)
      expect(
        screen.getByRole("dialog", { name: "Add MCP server" }),
      ).toBeInTheDocument();
    else expect(screen.queryByRole("dialog")).toBeNull();
    expect(state.get).not.toHaveBeenCalled();
  });
  it("keeps the tools filters and Add available even with no saved server", () => {
    state.servers = {};
    renderMcp(<ServersTab gatewayId="gw" runtimeOff />);
    expect(screen.getByRole("button", { name: "Add server" })).toBeEnabled();
    const search = screen.getByPlaceholderText("Search tools");
    fireEvent.change(search, { target: { value: "local" } });
    expect(search).toHaveValue("local");
    expect(screen.getByRole("combobox", { name: "Server" })).toBeEnabled();
    expect(state.get).not.toHaveBeenCalled();
  });
});
