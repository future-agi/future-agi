import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { renderMcp, DEMO_MCP } from "../testing/renderMcp";
import VirtualServersTab from "./VirtualServersTab";

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "gw-1",
    gateway: { id: "gw-1", baseUrl: "http://localhost:28490/v1" },
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: () => {} }));

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

beforeEach(() => {
  grid.props = null;
  api.get.mockReset();
  api.get.mockImplementation(async (url) => {
    if (url === "/agentcc/gateways/gw-1/config/")
      return { data: { result: { mcp: DEMO_MCP } } };
    if (url === "/agentcc/gateways/gw-1/mcp-tools/")
      return { data: { result: [] } };
    if (url === "/agentcc/gateways/gw-1/mcp-status/")
      return { data: { result: { enabled: true, tools: 0, servers: [] } } };
    if (url === "/agentcc/api-keys/") return { data: { result: [] } };
    throw new Error(`unexpected GET ${url}`);
  });
});

// "Who can use it" is cut to "Every key in the organizat..." at 1024, 1280
// and 1440 (screen pass N02); hovering the cell shows all of it.
describe("Virtual servers 'Who can use it' column", () => {
  it("gives a cut-off access summary its full text on hover", async () => {
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    await screen.findByRole("row");
    const access = grid.props.columnDefs.find((col) => col.colId === "access");
    const data = grid.props.rowData[0];
    expect(
      access.tooltipValueGetter?.({
        data,
        value: access.valueGetter({ data }),
      }),
    ).toBe("Every key in the organization");
  });
});
