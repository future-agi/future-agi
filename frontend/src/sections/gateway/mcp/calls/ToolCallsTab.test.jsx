import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import { renderMcp, DEMO_MCP, demoToolCall } from "../testing/renderMcp";
import { MCP_API } from "../api";
import { cursorFrom, formatCallTime, toolCallParams } from "./toolCalls";
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

const CONFIG_URL = "/agentcc/gateways/gw-1/config/";

function routeGets({ calls, detail, callsError } = {}) {
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
    if (url === CONFIG_URL) return { data: { result: { mcp: DEMO_MCP } } };
    if (url === "/agentcc/gateways/gw-1/mcp-tools/") {
      return {
        data: {
          result: [
            {
              name: "support-tools__create_ticket",
              server_id: "support-tools",
            },
            { name: "crm-tools__find_account", server_id: "crm-tools" },
          ],
        },
      };
    }
    if (url === "/agentcc/api-keys/") {
      return {
        data: {
          result: [
            {
              gateway_key_id: "key_5bedce7c58ba2ea7428d44be",
              name: "support-bot",
            },
          ],
        },
      };
    }
    if (url === MCP_API.toolCalls) {
      if (callsError) throw new Error("boom");
      return { data: calls(options.params || {}) };
    }
    if (url.startsWith("/agentcc/mcp/tool-calls/")) {
      return { data: { status: true, result: detail } };
    }
    throw new Error(`unexpected GET ${url}`);
  });
}

beforeEach(() => {
  api.get.mockReset();
});

describe("tool call helpers", () => {
  it("filters map run agent key server tool outcome and billing mode to query params", () => {
    expect(
      toolCallParams({
        runId: "run-1",
        agentId: "agent-1",
        apiKeyId: "key-1",
        serverId: "up",
        tool: "up_ok",
        outcome: "success",
        billingMode: "byok",
        range: "all",
      }),
    ).toEqual({
      limit: 50,
      run_id: "run-1",
      agent_id: "agent-1",
      api_key_id: "key-1",
      server_id: "up",
      tool: "up_ok",
      outcome: "success",
      billing_mode: "byok",
    });
  });
  it("reads the cursor out of the next page URL", () => {
    expect(
      cursorFrom(
        "http://localhost:8410/agentcc/mcp/tool-calls/?cursor=cD0yMDI2&limit=50",
      ),
    ).toBe("cD0yMDI2");
    expect(cursorFrom(null)).toBeUndefined();
  });

  it("sends only the filters the API takes", () => {
    const now = new Date("2026-10-20T12:00:00Z");
    expect(
      toolCallParams(
        {
          serverId: "support-tools",
          tool: "",
          outcome: "denied",
          range: "24h",
        },
        now,
      ),
    ).toEqual({
      limit: 50,
      server_id: "support-tools",
      outcome: "denied",
      from: "2026-10-19T12:00:00.000Z",
    });
    expect(toolCallParams({ range: "all" }, now)).toEqual({ limit: 50 });
  });
});

describe("ToolCallsTab", () => {
  const requests = () =>
    api.get.mock.calls
      .filter(([url]) => url === MCP_API.toolCalls)
      .map(([, options]) => options.params);
  const paginated = (params) => ({
    next: params.cursor ? null : "http://localhost/?cursor=next-page",
    results: [demoToolCall({ id: params.cursor ? 45 : 44 })],
  });

  it("filter changes reset the cursor", async () => {
    routeGets({ calls: paginated });
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Load more" }));
    await waitFor(() => expect(requests().at(-1).cursor).toBe("next-page"));
    fireEvent.change(screen.getByRole("textbox", { name: "Run ID" }), {
      target: { value: "run-new" },
    });
    await waitFor(() =>
      expect(requests().at(-1)).toMatchObject({ run_id: "run-new" }),
    );
    expect(requests().at(-1).cursor).toBeUndefined();
  });

  it("refresh and pagination retain active filters", async () => {
    routeGets({ calls: paginated });
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);
    await screen.findByRole("row");
    fireEvent.change(screen.getByRole("textbox", { name: "Run ID" }), {
      target: { value: "run-keep" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Agent ID" }), {
      target: { value: "agent-keep" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Key ID" }), {
      target: { value: "key-keep" },
    });
    fireEvent.mouseDown(
      screen.getByRole("combobox", { name: /^Billing mode/ }),
    );
    fireEvent.click(await screen.findByRole("option", { name: "Own key" }));
    const active = {
      run_id: "run-keep",
      agent_id: "agent-keep",
      api_key_id: "key-keep",
      billing_mode: "byok",
    };
    await waitFor(() => expect(requests().at(-1)).toMatchObject(active));
    const n = requests().length;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(requests().length).toBeGreaterThan(n));
    expect(requests().at(-1)).toMatchObject(active);
    fireEvent.click(await screen.findByRole("button", { name: "Load more" }));
    await waitFor(() =>
      expect(requests().at(-1)).toMatchObject({
        ...active,
        cursor: "next-page",
      }),
    );
  });

  it("run link preserves the recorded run id", async () => {
    const run = "run/recorded?step=1#done";
    routeGets({
      calls: () => ({
        next: null,
        results: [demoToolCall({ agent_run_id: run })],
      }),
    });
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);
    expect(await screen.findByRole("link", { name: run })).toHaveAttribute(
      "href",
      `/dashboard/gateway/runs/${encodeURIComponent(run)}`,
    );
  });

  it("inspector shows no content when not stored", async () => {
    const call = demoToolCall({ content_mode: "none" });
    routeGets({
      calls: () => ({ next: null, results: [call] }),
      detail: { ...call, arguments: { secret: "must-not-load" } },
    });
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);
    fireEvent.click(await screen.findByRole("row"));
    expect(
      await screen.findByText(/Arguments and result are not stored/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/must-not-load/)).toBeNull();
    expect(
      api.get.mock.calls.filter(
        ([url]) =>
          url.startsWith(MCP_API.toolCalls) && url !== MCP_API.toolCalls,
      ),
    ).toHaveLength(0);
  });
  it("lists the org's tool calls with readable names", async () => {
    routeGets({
      calls: () => ({ next: null, previous: null, results: [demoToolCall()] }),
    });
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);

    const row = await screen.findByRole("row");
    expect(within(row).getByText("delete_customer_data")).toBeInTheDocument();
    expect(within(row).getByText("support-tools")).toBeInTheDocument();
    // The virtual server's title, not its vs_ id.
    expect(within(row).getByText("Support desk")).toBeInTheDocument();
    expect(within(row).getByText("Success")).toBeInTheDocument();
    expect(within(row).getByText("support-bot")).toBeInTheDocument();
    expect(within(row).getByText("103ms")).toBeInTheDocument();
  });

  it("loads the next page with the cursor the API gave", async () => {
    routeGets({
      calls: (params) =>
        params.cursor
          ? {
              next: null,
              previous: null,
              results: [
                demoToolCall({
                  id: 45,
                  tool: "crm-tools__find_account",
                  upstream_tool: "find_account",
                  server_id: "crm-tools",
                }),
              ],
            }
          : {
              next: "http://localhost:8410/agentcc/mcp/tool-calls/?cursor=abc123&limit=50",
              previous: null,
              results: [demoToolCall()],
            },
    });
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Load more" }));
    await screen.findByText("find_account");
    const cursors = api.get.mock.calls
      .filter(([url]) => url === MCP_API.toolCalls)
      .map(([, options]) => options.params.cursor);
    expect(cursors).toEqual([undefined, "abc123"]);
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  });

  it("filters on the server through the API", async () => {
    routeGets({ calls: () => ({ next: null, previous: null, results: [] }) });
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);
    await screen.findByText("No tool calls yet");

    fireEvent.mouseDown(screen.getByRole("combobox", { name: /^Outcome/ }));
    fireEvent.click(await screen.findByRole("option", { name: "Denied" }));
    await waitFor(() => {
      const last = api.get.mock.calls
        .filter(([url]) => url === MCP_API.toolCalls)
        .at(-1);
      expect(last[1].params).toMatchObject({ outcome: "denied" });
    });
    // A filtered list with no hits offers to clear the filters.
    expect(await screen.findByText("No tool calls match")).toBeInTheDocument();
  });

  it("opens a call with its arguments and result", async () => {
    routeGets({
      calls: () => ({ next: null, previous: null, results: [demoToolCall()] }),
      detail: {
        ...demoToolCall(),
        arguments: { customer_id: "cus_015" },
        result: {
          content: [{ type: "text", text: "Erased records for cus_015" }],
        },
      },
    });
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);

    fireEvent.click(await screen.findByRole("row"));
    const drawer = await screen.findByRole("dialog");
    expect(
      within(drawer).getByText("support-tools__delete_customer_data"),
    ).toBeInTheDocument();
    expect(
      await within(drawer).findByText(/"customer_id": "cus_015"/),
    ).toBeInTheDocument();
    expect(
      within(drawer).getByText(/Erased records for cus_015/),
    ).toBeInTheDocument();
    expect(
      within(drawer).getByText("mcp_01M5CATN86KSEYB76RFBYRJYWX"),
    ).toBeInTheDocument();
    // The drawer shows the start time the way the list does, not the
    // browser's default "10/20/2026, 2:38:36 AM".
    const started = demoToolCall().started_at;
    expect(
      within(drawer).getByText(formatCallTime(started)),
    ).toBeInTheDocument();
    expect(within(drawer).queryByText(/\d+\/\d+\/\d{4}/)).toBeNull();
  });

  it("says the content was not stored when the call's content mode keeps none", async () => {
    const call = demoToolCall({ content_mode: "metadata" });
    routeGets({
      calls: () => ({ next: null, previous: null, results: [call] }),
      detail: call,
    });
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);

    fireEvent.click(await screen.findByRole("row"));
    const drawer = await screen.findByRole("dialog");
    expect(
      await within(drawer).findByText(/Arguments and result are not stored/),
    ).toBeInTheDocument();
  });

  it("shows an error with a retry when the list cannot load", async () => {
    routeGets({ callsError: true });
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);
    expect(
      await screen.findByText("Tool calls could not be loaded"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });

  it("opens filtered on the server a link names", async () => {
    routeGets({ calls: () => ({ next: null, previous: null, results: [] }) });
    renderMcp(<ToolCallsTab gatewayId="gw-1" />, {
      route: "/dashboard/gateway/mcp?tab=calls&server=crm-tools",
    });
    await waitFor(() => {
      const first = api.get.mock.calls.find(
        ([url]) => url === MCP_API.toolCalls,
      );
      expect(first[1].params).toMatchObject({ server_id: "crm-tools" });
    });
  });
});
