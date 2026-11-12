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

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), canEdit: true }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: api.get, post: api.post },
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: api.canEdit }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-refresh", gateway: {} }),
}));
vi.mock("ag-grid-react", async () => ({
  AgGridReact: (await import("./testing/agGridMock")).MockAgGrid,
}));

const gw = endpoints.gateway;
const OFF = {
  enabled: false,
  reason: "gateway_mcp_disabled",
  features: {
    client_oauth: true,
    client_installs: true,
    tool_call_explorer: true,
    tool_call_downloads: true,
    credential_vault: true,
    virtual_servers: true,
  },
};
const tabs = [
  "Overview",
  "Tools",
  "Servers",
  "Resources",
  "Prompts",
  "Guardrails",
  "Playground",
];
let failed;
let servers;
const clients = [];
async function open(tab) {
  const view = renderMcp(<MCPManagementSection />, {
    route: `/dashboard/gateway/mcp?tab=${tab}`,
  });
  clients.push(view.client);
  await waitFor(() =>
    expect(screen.getByRole("tab", { name: "Resources" })).toBeEnabled(),
  );
  return view;
}
function onlySavedReads() {
  expect(
    api.get.mock.calls
      .map(([url]) => url)
      .filter(
        (url) =>
          ![gw.config("gw-refresh"), gw.mcpStatus("gw-refresh")].includes(url),
      ),
  ).toEqual([]);
  expect(api.post).not.toHaveBeenCalled();
}
// The search field has its own inline "Clear search" control. The empty
// state's action is the one matching button outside the search landmark.
function emptyStateClear() {
  const searchBar = screen.getByRole("search");
  const actions = screen
    .getAllByRole("button", { name: /Clear filters|Clear search/i })
    .filter((button) => !searchBar.contains(button));
  expect(actions).toHaveLength(1);
  return actions[0];
}
beforeEach(() => {
  api.canEdit = true;
  failed = false;
  servers = { alpha: { url: "https://alpha.example/mcp" } };
  api.get.mockReset().mockImplementation(async (url) => {
    if (url === gw.config("gw-refresh"))
      return { data: { result: { mcp: { enabled: false, servers } } } };
    if (url === gw.mcpStatus("gw-refresh")) {
      if (failed) throw new Error("Status refresh failed");
      return { data: { result: OFF } };
    }
    throw new Error(`Unexpected request: ${String(url)}`);
  });
  api.post.mockReset();
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("RL-1009 cached OFF and truthful saved inventory", () => {
  it.each(["overview", "servers"])(
    "keeps the off allowlist after failed %s background and manual status refetches",
    async (tab) => {
      const { client } = await open(tab);
      expect(
        await screen.findByText(tab === "overview" ? "Saved servers" : "alpha"),
      ).toBeInTheDocument();
      failed = true;
      for (const refresh of [
        () =>
          client.invalidateQueries({
            queryKey: ["agentcc-mcp-status", "gw-refresh"],
          }),
        () =>
          client.refetchQueries({
            queryKey: ["agentcc-mcp-status", "gw-refresh"],
          }),
      ]) {
        await act(async () => {
          await refresh();
        });
        expect(
          await screen.findByText("MCP status could not be loaded"),
        ).toBeInTheDocument();
        expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(
          tabs,
        );
        expect(
          screen.getByRole("button", { name: "Reload config" }),
        ).toBeEnabled();
        expect(
          screen.queryByRole("button", {
            name: /Download|Export|Manage credentials/,
          }),
        ).toBeNull();
        if (tab === "overview")
          expect(screen.getByText("Saved servers")).toBeInTheDocument();
        expect(
          client.getQueryData(["agentcc-mcp-status", "gw-refresh"]),
        ).toEqual(OFF);
        onlySavedReads();
      }
    },
  );

  it.each(["tools", "resources", "prompts"])(
    "does not offer filter advice or a no-op clear action for empty off %s",
    async (kind) => {
      await open(kind);
      expect(
        await screen.findByText(`No previously loaded ${kind} are available.`),
      ).toBeInTheDocument();
      expect(
        screen.queryByText("Try adjusting your filters or search query."),
      ).toBeNull();
      expect(
        screen.queryByRole("button", { name: /Clear filters|Clear search/i }),
      ).toBeNull();
      const search = screen.getByPlaceholderText(`Search ${kind}`);
      fireEvent.change(search, { target: { value: "missing" } });
      fireEvent.click(emptyStateClear());
      expect(search).toHaveValue("");
      expect(
        screen.queryByRole("button", { name: /Clear filters|Clear search/i }),
      ).toBeNull();
      onlySavedReads();
    },
  );

  it.each(["tools", "resources", "prompts"])(
    "retains no-match filtering for cached off %s",
    async (kind) => {
      const { client } = await open(kind);
      await act(async () => {
        client.setQueryData(
          [`agentcc-mcp-${kind}`, "gw-refresh"],
          [{ name: "alpha__read", server_id: "alpha", uri: "file:///saved" }],
        );
      });
      fireEvent.change(screen.getByPlaceholderText(`Search ${kind}`), {
        target: { value: "missing" },
      });
      expect(
        await screen.findByText("Try adjusting your filters or search query."),
      ).toBeInTheDocument();
      expect(
        screen.queryByText(`No previously loaded ${kind} are available.`),
      ).toBeNull();
      fireEvent.click(emptyStateClear());
      expect(await screen.findByText("alpha__read")).toBeInTheDocument();
      onlySavedReads();
    },
  );

  it.each([true, false])(
    "offers empty Overview Add only for canEdit=%s without a runtime request",
    async (canEdit) => {
      servers = {};
      api.canEdit = canEdit;
      await open("overview");
      expect(await screen.findByText("No MCP servers yet")).toBeInTheDocument();
      const add = screen.queryByRole("button", { name: "Add server" });
      if (canEdit) {
        expect(add).toBeEnabled();
        fireEvent.click(add);
        expect(await screen.findByRole("dialog")).toBeInTheDocument();
        expect(
          screen.getByRole("tab", { name: "Servers", hidden: true }),
        ).toHaveAttribute("aria-selected", "true");
      } else expect(add).toBeNull();
      onlySavedReads();
    },
  );
});
