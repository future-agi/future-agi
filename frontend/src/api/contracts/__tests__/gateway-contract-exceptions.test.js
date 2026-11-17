import { describe, expect, it } from "vitest";

import { endpoints } from "src/utils/axios";
import { MCP_API } from "src/sections/gateway/mcp/api";
import {
  isGatewayControlHidden,
  isGatewayPageHidden,
} from "src/sections/gateway/shell/page-status";
import { API_CONTRACT_EXCEPTIONS } from "../api-contract-exceptions";
import { isContractedApiPath, getContractedApiMethods } from "../api-surface";
import { OPENAPI_CONTRACT } from "../openapi-contract.generated";

// The gateway console's uncontracted endpoints must say what is true of the
// backend on this line: none of them has a Django view here, and the console
// hides what would call them (sections/gateway/shell/page-status.js).
describe("gateway console contract exceptions", () => {
  it("does not register the playground run API, which has no view", () => {
    // agentcc/urls_playground.py has no routes; the one playground is the
    // Prompts workbench, and the gateway's own test is test-playground.
    expect(API_CONTRACT_EXCEPTIONS).not.toHaveProperty(
      "/agentcc/playground/run/",
    );
    expect(endpoints.gateway).not.toHaveProperty("playgroundRun");
  });

  it("records the served credit API and its role and purchasing gates", () => {
    const managed = Object.entries(API_CONTRACT_EXCEPTIONS).filter(([path]) =>
      path.startsWith("/agentcc/managed-credits/"),
    );
    expect(managed.map(([path]) => path)).toEqual([
      "/agentcc/managed-credits/reconciliation/",
    ]);
    expect(managed[0][1].served).not.toBe(true);
    const served = [
      ["", ["get"]], ["entries/", ["get"]], ["statement/", ["get"]],
      ["checkout/", ["post"]], ["checkout/{session_id}/", ["get"]],
      ["settings/", ["get", "put"]], ["grant/", ["post"]],
      ["adjust/", ["post"]], ["refund/", ["post"]],
    ];
    expect(served).toHaveLength(9);
    for (const [suffix, methods] of served) {
      const path = `/agentcc/managed-credits/${suffix}`;
      expect(isContractedApiPath(path)).toBe(true);
      expect(API_CONTRACT_EXCEPTIONS).not.toHaveProperty([path]);
      expect(getContractedApiMethods(path)).toEqual(methods);
      for (const method of methods) {
        // Published refusals remain required. Runtime Owner/Admin enforcement
        // is covered by test_managed_credits_api.py and billing-member-access.
        expect(OPENAPI_CONTRACT.endpoints[path][method].responses).toHaveProperty("403");
      }
    }
    expect(OPENAPI_CONTRACT.definitions.ManagedCreditSummary.properties.billing_available.type).toBe("boolean");
    expect(isGatewayControlHidden("managedCredits")).toBe(false);
  });

  // The MCP console's tool-call log, credential vault and connection probe,
  // and the Integrations page's catalogue and add, are served on this line
  // (agentcc/urls_mcp_toolcalls.py, urls_mcp_credentials.py and
  // urls_connector_catalog.py), so they are in the generated contract, not
  // in the exceptions, and the MCP page that calls them is shown.
  it("takes the MCP and connector paths from the generated contract, not the exceptions", () => {
    const contracted = [
      "/agentcc/mcp/tool-calls/",
      "/agentcc/mcp/tool-calls/{id}/",
      "/agentcc/mcp/credentials/",
      "/agentcc/mcp/credentials/{credential_id}/",
      "/agentcc/mcp/credentials/{credential_id}/rotate/",
      "/agentcc/mcp/probe/",
      "/agentcc/connectors/catalog/",
      "/agentcc/connectors/apps/{app_id}/add/",
    ];
    for (const path of contracted) {
      expect(isContractedApiPath(path), path).toBe(true);
      expect(API_CONTRACT_EXCEPTIONS, path).not.toHaveProperty([path]);
    }
    expect(API_CONTRACT_EXCEPTIONS).not.toHaveProperty([
      "/agentcc/mcp/tool-calls/{pk}/",
    ]);
    expect(isGatewayPageHidden("mcp")).toBe(false);
    expect(endpoints.gateway.connectorCatalog).toBe(
      "/agentcc/connectors/catalog/",
    );
    expect(endpoints.gateway.connectorAdd("linear")).toBe(
      "/agentcc/connectors/apps/linear/add/",
    );
  });

  it("builds the MCP console's paths through the endpoint registry", () => {
    const gw = endpoints.gateway;
    expect(MCP_API.toolCalls).toBe(gw.mcpToolCalls);
    expect(MCP_API.toolCall(42)).toBe(gw.mcpToolCall(42));
    expect(MCP_API.toolCall(42)).toBe("/agentcc/mcp/tool-calls/42/");
    expect(MCP_API.credentials).toBe("/agentcc/mcp/credentials/");
    expect(MCP_API.credential("c 1")).toBe("/agentcc/mcp/credentials/c%201/");
    expect(MCP_API.rotateCredential("c1")).toBe(
      "/agentcc/mcp/credentials/c1/rotate/",
    );
    expect(MCP_API.probe).toBe(gw.mcpProbe);
  });
});
