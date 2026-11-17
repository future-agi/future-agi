import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { renderMcp, DEMO_MCP } from "../testing/renderMcp";
import VirtualServersTab from "./VirtualServersTab";

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../testing/agGridMock");
  return { AgGridReact: MockAgGrid };
});

vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "gw-1",
    gateway: { id: "gw-1", baseUrl: "http://localhost:28490/v1" },
  }),
}));

vi.mock("notistack", () => ({ enqueueSnackbar: () => {} }));

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: api.get, post: api.post },
  };
});

// The demo org's answers, as VirtualServersTab.test.jsx reads them.
const STATUS_ON = {
  enabled: true,
  tools: 2,
  servers: [
    { server_id: "crm-tools", healthy: true, tool_count: 1 },
    { server_id: "support-tools", healthy: true, tool_count: 1 },
  ],
  features: { virtual_servers: true },
};

const TOOLS = [
  {
    name: "crm-tools__find_account",
    server_id: "crm-tools",
    upstream_name: "find_account",
  },
  {
    name: "support-tools__create_ticket",
    server_id: "support-tools",
    upstream_name: "create_ticket",
  },
];

// What a client of the default endpoint gets, then where connector tools
// are reached: never through the organization's access default.
const DEFAULT_LINE =
  "A client of http://localhost:28490/mcp gets the virtual server bound to its key, else the organization default, else every server the organization's access default allows.";
const CONNECTOR_LINE =
  "Connector tools are not in that last set; they are reached only through a virtual server that includes the connector.";

beforeEach(() => {
  api.get.mockReset();
  api.post.mockReset();
  api.get.mockImplementation(async (url) => {
    if (url === "/agentcc/gateways/gw-1/config/") {
      return { data: { result: { mcp: DEMO_MCP } } };
    }
    if (url === "/agentcc/gateways/gw-1/mcp-tools/") {
      return { data: { result: TOOLS } };
    }
    if (url === "/agentcc/gateways/gw-1/mcp-status/") {
      return { data: { result: STATUS_ON } };
    }
    if (url === "/agentcc/api-keys/") {
      return {
        data: {
          result: [{ gateway_key_id: "key_5bed", name: "support-bot" }],
        },
      };
    }
    throw new Error(`unexpected GET ${url}`);
  });
});

describe("VirtualServersTab default-endpoint line", () => {
  it("says, after what the default endpoint serves, that connector tools are not in it", async () => {
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    const line = await screen.findByText(`${DEFAULT_LINE} ${CONNECTOR_LINE}`);
    expect(line.textContent.startsWith(DEFAULT_LINE)).toBe(true);
    expect(line.textContent.endsWith(CONNECTOR_LINE)).toBe(true);
    expect(api.post).not.toHaveBeenCalled();
  });
});
