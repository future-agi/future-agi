import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";

import { renderMcp, DEMO_MCP, demoToolCall } from "../testing/renderMcp";
import ToolCallsTab from "./ToolCallsTab";

vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../testing/agGridMock");
  return { AgGridReact: MockAgGrid };
});

const api = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get: api.get } };
});

const LIST_URL = "/agentcc/mcp/tool-calls/";
const EXPORT_URL = `${LIST_URL}export/`;
const SELECTED_AT = Date.parse("2026-11-09T12:00:00Z");
const TEN_MINUTES = 10 * 60 * 1000;
const RANGES = [
  { label: "Last hour", ms: 60 * 60 * 1000 },
  { label: "Last 24 hours", ms: 24 * 60 * 60 * 1000 },
  { label: "Last 7 days", ms: 7 * 24 * 60 * 60 * 1000 },
];
let client;

const listRequests = () =>
  api.get.mock.calls
    .filter(([url]) => url === LIST_URL)
    .map(([, options]) => options.params);

const exportRequests = () =>
  api.get.mock.calls
    .filter(([url]) => url === EXPORT_URL)
    .map(([, options]) => options.params);

function callsInRange(ms) {
  return [
    demoToolCall({
      id: 1,
      upstream_tool: "near_lower_bound",
      started_at: new Date(SELECTED_AT - ms + TEN_MINUTES / 2).toISOString(),
      agent_run_id: "run-window",
    }),
    demoToolCall({
      id: 2,
      upstream_tool: "recent_call",
      started_at: new Date(SELECTED_AT - 60000).toISOString(),
      agent_run_id: "run-window",
    }),
  ];
}

// Keep the real query hook and download helper. The API fixture applies the
// same absolute lower bound as Django; it does not resolve relative ranges.
function routeGets(rows, { paginated = false } = {}) {
  const exportedIds = [];
  api.get.mockImplementation(async (url, { params = {} } = {}) => {
    if (url === "/agentcc/gateways/gw-1/mcp-status/") {
      return {
        data: {
          result: {
            enabled: true,
            features: { tool_call_explorer: true, tool_call_download: true },
          },
        },
      };
    }
    if (url === "/agentcc/gateways/gw-1/config/")
      return { data: { result: { mcp: DEMO_MCP } } };
    if (url === "/agentcc/gateways/gw-1/mcp-tools/" || url === "/agentcc/api-keys/")
      return { data: { result: [] } };
    const matching = rows.filter(
      (row) =>
        (!params.from || Date.parse(row.started_at) >= Date.parse(params.from)) &&
        (!params.run_id || row.agent_run_id === params.run_id),
    );
    if (url === LIST_URL) {
      const offset = params.cursor ? 1 : 0;
      return {
        data: {
          next:
            paginated && !params.cursor && matching.length > 1
              ? "http://localhost/?cursor=next-page"
              : null,
          results: paginated ? matching.slice(offset, offset + 1) : matching,
        },
      };
    }
    if (url === EXPORT_URL) {
      exportedIds.push(matching.map((row) => row.id));
      return { status: 200, data: new Blob([JSON.stringify(matching)]) };
    }
    throw new Error(`unexpected GET ${url}`);
  });
  return exportedIds;
}

async function openRange(label) {
  ({ client } = renderMcp(<ToolCallsTab gatewayId="gw-1" />));
  await screen.findByText("near_lower_bound");
  fireEvent.mouseDown(screen.getByRole("combobox", { name: /^Time/ }));
  fireEvent.click(await screen.findByRole("option", { name: label }));
  await waitFor(() => expect(listRequests().at(-1).from).toBeDefined());
  await screen.findByText("near_lower_bound");
}

async function download(format) {
  const count = exportRequests().length;
  fireEvent.click(screen.getByRole("button", { name: "Export" }));
  fireEvent.click(
    await screen.findByRole("menuitem", { name: `Export as ${format.toUpperCase()}` }),
  );
  await waitFor(() => expect(exportRequests()).toHaveLength(count + 1));
  await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(count + 1));
}

beforeEach(() => {
  api.get.mockReset();
  // Only Date is fake: React Query, MUI and Testing Library keep real timers.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(SELECTED_AT);
  URL.createObjectURL = vi.fn(() => "blob:tool-calls-window");
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  client?.clear();
  client = undefined;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("tool-call relative windows", () => {
  // J15-W13-W14-W17-004: clicking export later must not drop displayed calls.
  it.each(RANGES.flatMap((range) => ["csv", "json"].map((format) => ({ ...range, format }))))(
    "exports the displayed $label boundary as $format after time passes",
    async ({ label, ms, format }) => {
      const exportedIds = routeGets(callsInRange(ms));
      await openRange(label);
      fireEvent.change(screen.getByRole("textbox", { name: "Run ID" }), {
        target: { value: "run-window" },
      });
      await waitFor(() => expect(listRequests().at(-1).run_id).toBe("run-window"));
      // The request starts before React Query publishes the filtered rows.
      await screen.findByText("near_lower_bound");
      const { limit, cursor, ...displayed } = listRequests().at(-1);
      expect(displayed.from).toBe(new Date(SELECTED_AT - ms).toISOString());
      vi.setSystemTime(SELECTED_AT + TEN_MINUTES);
      expect(screen.getByText("near_lower_bound")).toBeInTheDocument();
      await download(format);
      expect(exportRequests().at(-1)).toEqual({ ...displayed, export_format: format });
      expect(exportedIds.at(-1)).toEqual([1, 2]);
    },
  );

  // J15.W15-F04: Refresh advances each advertised relative window, and the
  // subsequent export must keep that refreshed boundary rather than drift again.
  it.each(RANGES)("refreshes $label and exports the refreshed view", async ({ label, ms }) => {
    const exportedIds = routeGets(callsInRange(ms));
    await openRange(label);
    const count = listRequests().length;
    vi.setSystemTime(SELECTED_AT + TEN_MINUTES);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    const from = new Date(SELECTED_AT + TEN_MINUTES - ms).toISOString();
    await waitFor(() => expect(listRequests().at(-1).from).toBe(from));
    expect(listRequests().slice(count)).toEqual([{ limit: 50, from, cursor: undefined }]);
    await waitFor(() => expect(screen.queryByText("near_lower_bound")).toBeNull());
    expect(screen.getByText("recent_call")).toBeInTheDocument();
    vi.setSystemTime(SELECTED_AT + 2 * TEN_MINUTES);
    await download("json");
    expect(exportRequests().at(-1)).toEqual({ from, export_format: "json" });
    expect(exportedIds.at(-1)).toEqual([2]);
  });

  it("keeps the boundary during pagination but resets the cursor on Refresh", async () => {
    const { label, ms } = RANGES[0];
    routeGets(callsInRange(ms), { paginated: true });
    await openRange(label);
    const from = listRequests().at(-1).from;
    vi.setSystemTime(SELECTED_AT + TEN_MINUTES);
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await screen.findByText("recent_call");
    expect(listRequests().at(-1)).toEqual({ limit: 50, from, cursor: "next-page" });
    expect(screen.getByText("near_lower_bound")).toBeInTheDocument();
    const count = listRequests().length;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(listRequests().at(-1).from).not.toBe(from));
    expect(listRequests().slice(count)).toEqual([
      { limit: 50, from: new Date(SELECTED_AT + TEN_MINUTES - ms).toISOString(), cursor: undefined },
    ]);
    await waitFor(() => expect(screen.queryByText("near_lower_bound")).toBeNull());
    expect(screen.getAllByRole("row")).toHaveLength(1);
  });

  it("advances the anchor on filter changes, not only on range selection", async () => {
    const { label, ms } = RANGES[0];
    routeGets(callsInRange(ms));
    await openRange(label);
    vi.setSystemTime(SELECTED_AT + TEN_MINUTES);
    fireEvent.change(screen.getByRole("textbox", { name: "Run ID" }), {
      target: { value: "run-window" },
    });
    await waitFor(() => expect(listRequests().at(-1)).toEqual({
      limit: 50,
      cursor: undefined,
      run_id: "run-window",
      from: new Date(SELECTED_AT + TEN_MINUTES - ms).toISOString(),
    }));
    await waitFor(() => expect(screen.queryByText("near_lower_bound")).toBeNull());
  });

  it("still refetches if a relative Refresh resolves to the same instant", async () => {
    routeGets(callsInRange(RANGES[0].ms));
    await openRange(RANGES[0].label);
    const params = listRequests().at(-1);
    const count = listRequests().length;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(listRequests()).toHaveLength(count + 1));
    expect(listRequests().at(-1)).toEqual(params);
  });

  it("refetches All time without adding time bounds and exports every matching call", async () => {
    const exportedIds = routeGets(callsInRange(RANGES[2].ms));
    ({ client } = renderMcp(<ToolCallsTab gatewayId="gw-1" />));
    await screen.findByText("near_lower_bound");
    const count = listRequests().length;
    vi.setSystemTime(SELECTED_AT + TEN_MINUTES);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(listRequests()).toHaveLength(count + 1));
    expect(listRequests().at(-1)).toEqual({ limit: 50, cursor: undefined });
    await download("csv");
    expect(exportRequests().at(-1)).toEqual({ export_format: "csv" });
    expect(exportedIds.at(-1)).toEqual([1, 2]);
  });
});
