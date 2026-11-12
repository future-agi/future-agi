import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { renderMcp, DEMO_MCP, demoToolCall } from "../testing/renderMcp";
import { MCP_API } from "../api";
import ToolCallsTab from "./ToolCallsTab";

// The props the grid got last, so a test can read its column defs.
const grid = vi.hoisted(() => ({ props: null }));
vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../testing/agGridMock");
  return {
    AgGridReact: (props) => {
      grid.props = props;
      return MockAgGrid(props);
    },
  };
});

const api = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get: api.get } };
});

// A key name wider than the Key column: the walk measured 212px of text in a
// 128px cell at 1440 (screen pass W01).
const LONG_KEY = "walk-example-client-mwbi0zfh";

beforeEach(() => {
  grid.props = null;
  api.get.mockReset();
  api.get.mockImplementation(async (url) => {
    if (url === "/agentcc/gateways/gw-1/mcp-status/")
      return {
        data: {
          result: {
            enabled: true,
            features: {
              catalog: true,
              credential_vault: true,
              virtual_servers: true,
              tool_call_explorer: true,
              tool_call_download: true,
            },
          },
        },
      };
    if (url === "/agentcc/gateways/gw-1/config/")
      return { data: { result: { mcp: DEMO_MCP } } };
    if (url === "/agentcc/gateways/gw-1/mcp-tools/")
      return { data: { result: [] } };
    if (url === "/agentcc/api-keys/")
      return {
        data: {
          result: [
            { gateway_key_id: "key_5bedce7c58ba2ea7428d44be", name: LONG_KEY },
          ],
        },
      };
    if (url === MCP_API.toolCalls)
      return {
        data: { next: null, previous: null, results: [demoToolCall()] },
      };
    throw new Error(`unexpected GET ${url}`);
  });
});

describe("Tool calls Key column", () => {
  it("gives a cut-off key name its full text on hover", async () => {
    renderMcp(<ToolCallsTab gatewayId="gw-1" />);
    expect(await screen.findByText(LONG_KEY)).toBeInTheDocument();
    const key = grid.props.columnDefs.find((col) => col.colId === "key");
    const data = grid.props.rowData[0];
    expect(
      key.tooltipValueGetter?.({ data, value: key.valueGetter({ data }) }),
    ).toBe(LONG_KEY);
  });
});
