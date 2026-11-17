import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import {
  renderMcp,
  DEMO_MCP,
} from "src/sections/gateway/mcp/testing/renderMcp";
import CredentialsTab from "src/sections/gateway/mcp/credentials/CredentialsTab";
import VirtualServersTab from "src/sections/gateway/mcp/virtual/VirtualServersTab";
import { MCP_API } from "src/sections/gateway/mcp/api";
const state = vi.hoisted(() => ({ level: 3, get: vi.fn(), post: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: state.level }),
}));
vi.mock("src/sections/gateway/context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "gw-1",
    gateway: { baseUrl: "http://localhost/v1" },
  }),
}));
vi.mock("src/utils/axios", async (original) => {
  const actual = await original();
  return {
    ...actual,
    default: { ...actual.default, get: state.get, post: state.post },
  };
});
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
beforeEach(() => {
  cleanup();
  state.level = 3;
  state.post.mockReset();
  state.get.mockReset().mockImplementation(async (url) => {
    if (url === "/agentcc/gateways/gw-1/config/")
      return {
        data: {
          result: {
            mcp: { ...structuredClone(DEMO_MCP), virtual_servers: {} },
          },
        },
      };
    if (url === MCP_API.credentials) return { data: { results: [], count: 0 } };
    if (url.endsWith("/mcp-tools/")) return { data: { result: [] } };
    if (url.endsWith("/mcp-status/"))
      return { data: { result: { enabled: false } } };
    if (url === "/agentcc/api-keys/") return { data: { result: [] } };
    throw new Error(`Unexpected fixture read ${url}`);
  });
});
describe("independent no-hidden-controls verification", () => {
  it.each(["credentials", "virtual"])(
    "retains the empty %s create control for a restricted member",
    async (kind) => {
      renderMcp(
        kind === "credentials" ? (
          <CredentialsTab gatewayId="gw-1" />
        ) : (
          <VirtualServersTab gatewayId="gw-1" />
        ),
      );
      await screen.findByText(
        kind === "credentials"
          ? "No stored credentials"
          : "No virtual servers yet",
      );
      const button = screen.getByRole("button", {
        name: kind === "credentials" ? "Add credential" : "New virtual server",
      });
      expect(button).toBeDisabled();
      expect(screen.getByText("Owner or Admin only")).toBeInTheDocument();
      fireEvent.click(button);
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(state.post).not.toHaveBeenCalled();
    },
  );
  it.each(["credentials", "virtual"])(
    "retains working empty %s create control for an admin",
    async (kind) => {
      state.level = 8;
      renderMcp(
        kind === "credentials" ? (
          <CredentialsTab gatewayId="gw-1" />
        ) : (
          <VirtualServersTab gatewayId="gw-1" />
        ),
      );
      await screen.findByText(
        kind === "credentials"
          ? "No stored credentials"
          : "No virtual servers yet",
      );
      expect(
        screen.getByRole("button", {
          name:
            kind === "credentials" ? "Add credential" : "New virtual server",
        }),
      ).toBeEnabled();
    },
  );
});
