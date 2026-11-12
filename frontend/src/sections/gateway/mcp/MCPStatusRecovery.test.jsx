import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from "@testing-library/react";
import { endpoints } from "src/utils/axios";
import MCPManagementSection from "./MCPManagementSection";
import { renderMcp } from "./testing/renderMcp";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: api.get, post: api.post },
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-deadline", gateway: {} }),
}));
vi.mock("ag-grid-react", async () => ({
  AgGridReact: (await import("./testing/agGridMock")).MockAgGrid,
}));

const gw = endpoints.gateway;
const clients = [];
let resolveStatus;
function open(tab) {
  const view = renderMcp(<MCPManagementSection />, {
    route: `/dashboard/gateway/mcp?tab=${tab}`,
  });
  clients.push(view.client);
  return view;
}
function statusCalls() {
  return api.get.mock.calls.filter(
    ([url]) => url === gw.mcpStatus("gw-deadline"),
  );
}
beforeEach(() => {
  api.get.mockReset().mockImplementation(async (url) => {
    if (url === gw.config("gw-deadline"))
      return {
        data: {
          result: {
            mcp: {
              enabled: false,
              servers: { alpha: { url: "https://alpha.example/mcp" } },
              guardrails: {},
            },
          },
        },
      };
    if (url === gw.mcpStatus("gw-deadline"))
      return new Promise((resolve) => {
        resolveStatus = resolve;
      });
    if (url === gw.mcpTools("gw-deadline")) return { data: { result: [] } };
    throw new Error(`Unexpected request: ${String(url)}`);
  });
  api.post.mockReset();
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.useRealTimers();
});

describe("RL-1009 first status settlement", () => {
  it("shows a loading skeleton without mounting NEW surfaces before the first response", async () => {
    open("virtual");
    expect(
      screen.getByRole("status", { name: "Loading MCP status" }),
    ).toBeInTheDocument();
    expect(api.get.mock.calls.map(([url]) => url)).toEqual([
      gw.config("gw-deadline"),
      gw.mcpStatus("gw-deadline"),
    ]);
    await act(async () => {
      resolveStatus({
        data: {
          result: {
            enabled: false,
            reason: "gateway_mcp_disabled",
            features: {
              virtual_servers: true,
              credential_vault: true,
              tool_call_explorer: true,
            },
          },
        },
      });
    });
    expect(await screen.findByText("Saved servers")).toBeInTheDocument();
    expect(
      screen.queryByRole("status", { name: "Loading MCP status" }),
    ).toBeNull();
    expect(api.get.mock.calls.map(([url]) => url)).toEqual([
      gw.config("gw-deadline"),
      gw.mcpStatus("gw-deadline"),
    ]);
  });

  it.each(["servers", "guardrails"])(
    "recovers saved %s controls after a stalled status deadline without a remount retry",
    async (tab) => {
      vi.useFakeTimers();
      open(tab);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(14999);
      });
      expect(api.get.mock.calls.map(([url]) => url)).toEqual([
        gw.config("gw-deadline"),
        gw.mcpStatus("gw-deadline"),
      ]);
      expect(
        screen.queryByRole("button", {
          name: /Add server|Add stored settings/,
        }),
      ).toBeNull();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2);
      });
      vi.useRealTimers();
      expect(
        await screen.findByText(/MCP status could not be loaded/),
      ).toBeInTheDocument();
      expect(
        screen.queryByText("MCP is turned off on this gateway"),
      ).toBeNull();
      const label = tab === "servers" ? "Add server" : "Add stored settings";
      expect(await screen.findByRole("button", { name: label })).toBeEnabled();
      fireEvent.click(screen.getByRole("button", { name: label }));
      expect(await screen.findByRole("dialog")).toBeInTheDocument();
      expect(statusCalls()).toHaveLength(1);
      expect(statusCalls()[0][1].signal.aborted).toBe(true);
      expect(api.post).not.toHaveBeenCalled();
      // A late response must not replace the deadline error with an OFF answer.
      await act(async () => {
        resolveStatus({
          data: { result: { enabled: false, reason: "gateway_mcp_disabled" } },
        });
      });
      await waitFor(() =>
        expect(
          screen.getByText(/MCP status could not be loaded/),
        ).toBeInTheDocument(),
      );
      expect(
        screen.queryByText("MCP is turned off on this gateway"),
      ).toBeNull();
    },
  );
});
