import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderMcp, DEMO_MCP, demoToolCall } from "../testing/renderMcp";
import OverviewTab from "../overview/OverviewTab";
import ServerDrawer from "../servers/ServerDrawer";
import ToolCallsTab from "./ToolCallsTab";
import ToolCallDrawer from "./ToolCallDrawer";

const state = vi.hoisted(() => ({ status: {}, get: vi.fn() }));
vi.mock("src/utils/axios", async (original) => {
  const actual = await original();
  return { ...actual, default: { ...actual.default, get: state.get } };
});
vi.mock("../hooks/useMCPConfig", async (original) => ({
  ...(await original()),
  useMCPStatus: () => state.status,
  useRemoveMCPServer: () => ({ mutate: vi.fn() }),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1", gateway: {} }),
}));
vi.mock("../servers/ServedToolsPanel", () => ({
  default: () => <div>Existing served tools</div>,
}));
vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../testing/agGridMock");
  return { AgGridReact: MockAgGrid };
});

const LIST = "/agentcc/mcp/tool-calls/";
const EXPORT = `${LIST}export/`;
function capability(explorer, download = false, extra = {}) {
  state.status = {
    data: {
      enabled: true,
      features: { tool_call_explorer: explorer, tool_call_download: download },
    },
    ...extra,
  };
}
const callRequests = () =>
  state.get.mock.calls.filter(([url]) => url.startsWith(LIST));
const row = {
  id: "support-tools",
  url: "https://example.com/mcp",
  transport: "http",
  health: { tone: "healthy", label: "Healthy" },
  auth: { kind: "none" },
  config: {},
};
const surfaces = [
  ["Overview", () => <OverviewTab gatewayId="gw-1" />],
  [
    "ServerDrawer",
    () => (
      <ServerDrawer
        row={row}
        gatewayId="gw-1"
        onClose={() => {}}
        onEdit={() => {}}
      />
    ),
  ],
  ["Calls", () => <ToolCallsTab gatewayId="gw-1" />],
  [
    "Detail",
    () => (
      <ToolCallDrawer
        call={demoToolCall()}
        gatewayId="gw-1"
        onClose={() => {}}
      />
    ),
  ],
];

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
beforeEach(() => {
  capability(false, false);
  state.get.mockReset().mockImplementation(async (url) => {
    if (url === "/agentcc/gateways/gw-1/config/")
      return { data: { result: { mcp: DEMO_MCP } } };
    if (url === LIST)
      return { data: { results: [demoToolCall()], next: null } };
    if (url === `${LIST}46/`)
      return {
        data: {
          result: {
            ...demoToolCall(),
            arguments: { example: "stored-detail" },
          },
        },
      };
    if (url === EXPORT)
      return {
        status: 200,
        data: new Blob(["metadata"], { type: "text/csv" }),
      };
    if (
      [
        "/agentcc/api-keys/",
        "/agentcc/gateways/gw-1/mcp-tools/",
        "/agentcc/gateways/gw-1/mcp-resources/",
        "/agentcc/gateways/gw-1/mcp-prompts/",
      ].includes(url)
    )
      return { data: { result: [] } };
    throw new Error(`Unexpected GET ${url}`);
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  URL.createObjectURL = vi.fn(() => "blob:test");
  URL.revokeObjectURL = vi.fn();
});

describe.each(surfaces)("%s explorer switch", (name, element) => {
  it.each([false, undefined, null, "true", 1])(
    "OFF/invalid %s never renders or requests calls, even with download ON",
    async (value) => {
      capability(value, true);
      renderMcp(element());
      await act(async () => {});
      expect(callRequests()).toEqual([]);
      expect(screen.queryByText("Recent tool calls")).toBeNull();
      expect(screen.queryByText("Tool calls, last 24 h")).toBeNull();
      expect(screen.queryByRole("button", { name: "Export" })).toBeNull();
      expect(screen.queryByText(/stored-detail/)).toBeNull();
    },
  );
  it.each([{ isLoading: true }, { isError: true }])(
    "fails closed despite stale ON status %j",
    async (extra) => {
      capability(true, true, extra);
      renderMcp(element());
      await act(async () => {});
      expect(callRequests()).toEqual([]);
    },
  );
  it("ON requests calls and OFF removes cached content without another request", async () => {
    capability(true, true);
    const view = renderMcp(element());
    await waitFor(() => expect(callRequests().length).toBeGreaterThan(0));
    if (name === "Overview" || name === "ServerDrawer") {
      expect(await screen.findByText("Recent tool calls")).toBeInTheDocument();
    } else if (name === "Calls") {
      expect(await screen.findByRole("row")).toBeInTheDocument();
    } else {
      expect(await screen.findByText(/stored-detail/)).toBeInTheDocument();
    }
    const count = callRequests().length;
    capability(false, true);
    view.rerender(element());
    await act(async () => {});
    expect(callRequests()).toHaveLength(count);
    expect(screen.queryByText("Recent tool calls")).toBeNull();
    expect(screen.queryByText("Tool calls, last 24 h")).toBeNull();
    expect(screen.queryByRole("button", { name: "Export" })).toBeNull();
    expect(screen.queryByText(/stored-detail/)).toBeNull();
  });
});

it("OFF keeps Overview's server health and tools", async () => {
  renderMcp(<OverviewTab gatewayId="gw-1" />);
  expect(await screen.findByText("Server health")).toBeInTheDocument();
  expect(screen.getByText("Tools")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "All servers" })).toBeEnabled();
});
it("OFF keeps static server editing, removal and served tools", async () => {
  renderMcp(
    <ServerDrawer
      row={row}
      gatewayId="gw-1"
      onClose={() => {}}
      onEdit={() => {}}
    />,
  );
  expect(screen.getByRole("button", { name: "Edit" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Remove" })).toBeEnabled();
  expect(screen.getByText("Existing served tools")).toBeInTheDocument();
});

it.each([false, undefined, null, "true", 1])(
  "downloads %s leaves explorer available but makes no download request",
  async (download) => {
    capability(true, download);
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);
    await screen.findByRole("row");
    expect(screen.queryByRole("button", { name: "Export" })).toBeNull();
    // SIEM entry and independent controls now belong to the MCP page header.
    expect(screen.queryByRole("button", { name: "SIEM export" })).toBeNull();
    expect(state.get.mock.calls.some(([url]) => url === EXPORT)).toBe(false);
  },
);
it.each([
  ["Export as CSV", "csv"],
  ["Export as JSON", "json"],
])("both ON permits %s", async (label, format) => {
  capability(true, true);
  renderMcp(<ToolCallsTab gatewayId="gw-1" />);
  await screen.findByRole("row");
  fireEvent.click(screen.getByRole("button", { name: "Export" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: label }));
  await waitFor(() =>
    expect(state.get).toHaveBeenCalledWith(
      EXPORT,
      expect.objectContaining({
        params: expect.objectContaining({ export_format: format }),
      }),
    ),
  );
});
it("download ON->OFF removes an already open menu without hiding explorer", async () => {
  capability(true, true);
  const element = <ToolCallsTab gatewayId="gw-1" />;
  const view = renderMcp(element);
  await screen.findByRole("row");
  fireEvent.click(screen.getByRole("button", { name: "Export" }));
  expect(
    await screen.findByRole("menuitem", { name: "Export as CSV" }),
  ).toBeInTheDocument();
  capability(true, false);
  view.rerender(<ToolCallsTab gatewayId="gw-1" />);
  expect(screen.queryByRole("menuitem", { name: "Export as CSV" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Export" })).toBeNull();
  expect(screen.getByRole("row")).toBeInTheDocument();
  expect(state.get.mock.calls.some(([url]) => url === EXPORT)).toBe(false);
});
