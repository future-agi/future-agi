import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import ServersTab from "./ServersTab";

// A server card's health comes from the status read. While that read fails,
// the card says the status is unknown, the banner's word, not Not connected.
const state = vi.hoisted(() => ({ get: vi.fn(), status: {} }));
vi.mock("src/utils/axios", async (original) => {
  const actual = await original();
  return { ...actual, default: { ...actual.default, get: state.get } };
});
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
          paused: { url: "https://paused.example/mcp", enabled: false },
        },
      },
    },
    isLoading: false,
  }),
}));
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => state.status,
  useMCPTools: () => ({ data: [], isLoading: false }),
  useMCPResources: () => ({ data: [] }),
  useMCPPrompts: () => ({ data: [] }),
  useRemoveMCPServer: () => ({ mutate: vi.fn() }),
  useUpdateMCPServer: () => ({ mutate: vi.fn() }),
}));

beforeEach(() => {
  state.get.mockReset().mockResolvedValue({ data: { result: [] } });
});

describe("ServersTab while the MCP status cannot be read", () => {
  it("says a server's health is unknown, not Not connected", () => {
    state.status = { data: undefined, isError: true, isLoading: false };
    renderMcp(<ServersTab gatewayId="gw" />);
    expect(
      screen.getByRole("button", { name: "alpha Status unknown" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "alpha Not connected" }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "paused Disabled" }),
    ).toBeInTheDocument();
  });

  it("keeps Not connected when the status is read and lists no server", () => {
    state.status = {
      data: { enabled: true, servers: [] },
      isError: false,
      isLoading: false,
    };
    renderMcp(<ServersTab gatewayId="gw" />);
    expect(
      screen.getByRole("button", { name: "alpha Not connected" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Status unknown")).toBeNull();
  });
});
