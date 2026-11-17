import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import { renderMcp, DEMO_MCP, demoToolCall } from "../testing/renderMcp";
import { toolCallParams } from "./toolCalls";
import ToolCallsTab from "./ToolCallsTab";

// The advanced explorer (G16-SIEM): a latency filter next to the others, an
// export of exactly the filtered view, and a drawer that says how the call
// went through the gateway (attempts, approval, limits) without loading
// content the gateway did not store.

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
const EXPORT_URL = "/agentcc/mcp/tool-calls/export/";

function routeGets(rows) {
  api.get.mockImplementation(async (url, options = {}) => {
    if (url === "/agentcc/gateways/gw-1/mcp-status/")
      return {
        data: {
          result: {
            enabled: true,
            features: { tool_call_explorer: true, tool_call_download: true },
          },
        },
      };
    if (url === "/agentcc/gateways/gw-1/config/") {
      return { data: { result: { mcp: DEMO_MCP } } };
    }
    if (url === "/agentcc/gateways/gw-1/mcp-tools/")
      return { data: { result: [] } };
    if (url === "/agentcc/api-keys/") return { data: { result: [] } };
    if (url === LIST_URL) return { data: { next: null, results: rows } };
    if (url === "/agentcc/siem/destinations/") {
      return {
        data: { result: { export_available: false, destinations: [] } },
      };
    }
    if (url === "/agentcc/siem/deliveries/") {
      return { data: { result: { deliveries: [] } } };
    }
    if (url === EXPORT_URL) {
      return {
        status: 200,
        data: new Blob(["event_id\n"], { type: "text/csv" }),
      };
    }
    throw new Error(`unexpected GET ${url} ${JSON.stringify(options)}`);
  });
}

const listRequests = () =>
  api.get.mock.calls
    .filter(([url]) => url === LIST_URL)
    .map(([, options]) => options.params);

beforeEach(() => {
  api.get.mockReset();
  URL.createObjectURL = vi.fn(() => "blob:tool-calls");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("tool call explorer", () => {
  it("latency filter maps to min_latency_ms", () => {
    expect(toolCallParams({ minLatency: "1000", range: "all" })).toEqual({
      limit: 50,
      min_latency_ms: 1000,
    });
  });

  it("filters on latency through the API", async () => {
    routeGets([demoToolCall()]);
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);
    await screen.findByRole("row");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: /^Latency/ }));
    fireEvent.click(await screen.findByRole("option", { name: "Over 1 s" }));
    await waitFor(() =>
      expect(listRequests().at(-1)).toMatchObject({ min_latency_ms: 1000 }),
    );
  });

  it("exports exactly the filtered view without paging", async () => {
    routeGets([demoToolCall()]);
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);
    await screen.findByRole("row");
    fireEvent.change(screen.getByRole("textbox", { name: "Run ID" }), {
      target: { value: "run-export" },
    });
    await waitFor(() =>
      expect(listRequests().at(-1)).toMatchObject({ run_id: "run-export" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "Export as CSV" }),
    );
    await waitFor(() =>
      expect(api.get.mock.calls.some(([url]) => url === EXPORT_URL)).toBe(true),
    );
    const [, options] = api.get.mock.calls.find(([url]) => url === EXPORT_URL);
    expect(options.params).toEqual({
      run_id: "run-export",
      export_format: "csv",
    });
    expect(options.responseType).toBe("blob");
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
  });

  it("drawer shows attempts approvals and limits outcomes", async () => {
    const call = demoToolCall({
      content_mode: "metadata",
      approval_id: "appr_1",
      outcomes: {
        attempts: [
          { step: "gateway", result: "passed" },
          {
            step: "upstream",
            result: "ok",
            latency_ms: 389,
            credential_source: "managed",
            fallback_from: "",
          },
        ],
        approval: {
          approval_id: "appr_1",
          status: "approved",
          requested_at: "2026-10-20T09:38:30Z",
          decided_at: "2026-10-20T09:38:34Z",
          expires_at: null,
        },
        limits: { stopped_by: null, code: "", budget_scope: "run_R1" },
      },
    });
    routeGets([call]);
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);
    fireEvent.click(await screen.findByRole("row"));
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("Attempts")).toBeInTheDocument();
    expect(within(drawer).getByText("Passed")).toBeInTheDocument();
    expect(within(drawer).getByText("OK in 389ms")).toBeInTheDocument();
    expect(within(drawer).getByText("Approved")).toBeInTheDocument();
    expect(
      within(drawer).getByText("No limit stopped this call"),
    ).toBeInTheDocument();
    expect(within(drawer).getByText("run_R1")).toBeInTheDocument();
    // Metadata-only calls still load no detail.
    expect(
      api.get.mock.calls.filter(
        ([url]) => url.startsWith(LIST_URL) && url !== LIST_URL,
      ),
    ).toHaveLength(0);
  });

  it("leaves SIEM management to the MCP page header outside the explorer gate", async () => {
    routeGets([demoToolCall()]);
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);
    await screen.findByRole("row");
    expect(screen.queryByRole("button", { name: "SIEM export" })).toBeNull();
    expect(
      api.get.mock.calls.some(([url]) => url === "/agentcc/siem/destinations/"),
    ).toBe(false);
    // Real drawer reads and destination writes through the header are covered
    // by MCPManagementSection.siem-switch.test.jsx for all switch combinations.
  });

  it("drawer names the limit that stopped a refused call", async () => {
    const call = demoToolCall({
      outcome: "denied",
      content_mode: "none",
      outcomes: {
        attempts: [
          { step: "gateway", result: "denied", code: "tool_budget_exhausted" },
        ],
        approval: { approval_id: "appr_gone", status: null },
        limits: {
          stopped_by: "budget",
          code: "tool_budget_exhausted",
          budget_scope: "",
        },
      },
    });
    routeGets([call]);
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);
    fireEvent.click(await screen.findByRole("row"));
    const drawer = await screen.findByRole("dialog");
    expect(
      within(drawer).getByText("Denied (tool_budget_exhausted)"),
    ).toBeInTheDocument();
    expect(within(drawer).getByText("Budget")).toBeInTheDocument();
    expect(
      within(drawer).getByText("Not found in this organization"),
    ).toBeInTheDocument();
  });
});
