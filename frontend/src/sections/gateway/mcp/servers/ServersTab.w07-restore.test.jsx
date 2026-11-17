import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { renderMcp } from "../testing/renderMcp";
import ServersTab from "./ServersTab";

const state = vi.hoisted(() => ({
  get: vi.fn(),
  status: { current: {} },
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
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", async (original) => ({
  ...(await original()),
  useGatewayConfig: () => ({
    data: {
      mcp: {
        enabled: true,
        servers: {
          alpha: { url: "https://alpha.example/mcp", transport: "http" },
        },
      },
    },
  }),
}));
vi.mock("../hooks/useMCPConfig", async (original) => ({
  ...(await original()),
  useMCPStatus: () => state.status.current,
  useMCPTools: () => ({ data: [] }),
  useMCPResources: () => ({ data: [] }),
  useMCPPrompts: () => ({ data: [] }),
  useRemoveMCPServer: () => ({ mutate: vi.fn() }),
  useUpdateMCPServer: () => ({ mutate: vi.fn() }),
}));

const NOT_AVAILABLE = "Not available in this deployment";
async function openDrawer() {
  renderMcp(<ServersTab gatewayId="gw" />);
  fireEvent.click(screen.getByRole("button", { name: /^alpha\b/ }));
  return screen.findByRole("dialog", { name: "alpha" });
}

beforeEach(() => {
  state.get.mockReset().mockRejectedValue(new Error("Unexpected request"));
});

// The server drawer's Manage credentials
// stays while mcp-status loads, fails or reports an unreachable gateway; a
// vault switch that is off disables it with the deployment's line.
describe("the server drawer's Manage credentials", () => {
  it.each([
    ["loading", { isLoading: true, isPending: true }],
    ["a status read error", { isError: true }],
    [
      "an unreachable gateway",
      {
        data: {
          enabled: false,
          reason: "gateway_unreachable",
          features: { credential_vault: true },
        },
      },
    ],
    [
      "a healthy status",
      { data: { enabled: true, features: { credential_vault: true } } },
    ],
  ])("is shown and enabled during %s", async (_state, current) => {
    state.status.current = current;
    const drawer = await openDrawer();
    expect(
      within(drawer).getByRole("button", { name: "Manage credentials" }),
    ).toBeEnabled();
  });

  it("is disabled with the line when the vault switch is off", async () => {
    state.status.current = {
      data: { enabled: true, features: { credential_vault: false } },
    };
    const drawer = await openDrawer();
    expect(
      within(drawer).getByRole("button", { name: "Manage credentials" }),
    ).toBeDisabled();
    expect(within(drawer).getByText(NOT_AVAILABLE)).toBeInTheDocument();
  });
});
