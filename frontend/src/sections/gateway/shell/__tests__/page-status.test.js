import { describe, expect, it } from "vitest";

import { paths } from "src/routes/paths";

import {
  HIDDEN_GATEWAY_PAGES,
  HIDDEN_MCP_TABS,
  HIDDEN_ORG_CONFIG_TABS,
  isGatewayPageHidden,
  isMcpTabHidden,
  isOrgConfigTabHidden,
} from "../page-status";

const gw = paths.dashboard.gateway;

// What this build hides, now that the gateway connects the MCP servers an
// organization saves (per-org pools, virtual servers at /mcp/v/{id}, the
// credential vault and one tool-call record per call) and the API serves the
// tool-call log and the vault (agentcc/urls_mcp_toolcalls.py,
// urls_mcp_credentials.py).
describe("page status on the MCP and apps build", () => {
  it("shows the MCP page and hides only capabilities and policies", () => {
    expect(isGatewayPageHidden("mcp")).toBe(false);
    expect(Object.keys(HIDDEN_GATEWAY_PAGES).sort()).toEqual([
      "capabilities",
      "policies",
    ]);
    expect(HIDDEN_GATEWAY_PAGES.capabilities.redirectTo).toBe(gw.models);
    expect(HIDDEN_GATEWAY_PAGES.policies.redirectTo).toBe(gw.guardrails.root);
  });

  it("shows the servers, virtual servers, tool calls, credentials and client-setup tabs", () => {
    for (const slug of [
      "servers",
      "virtual",
      "calls",
      "credentials",
      "connect",
    ]) {
      expect(isMcpTabHidden(slug), slug).toBe(false);
    }
  });

  it("retains Resources and Prompts visibly while leaving other tab gates unchanged", () => {
    // Approvals stays visible; only registry and trust are hidden.
    expect(Object.keys(HIDDEN_MCP_TABS).sort()).toEqual(["registry", "trust"]);
    expect(isMcpTabHidden("resources")).toBe(false);
    expect(isMcpTabHidden("prompts")).toBe(false);
  });

  it("keeps MCP visible and restores the read-only A2A and Audit tabs", () => {
    for (const id of ["mcp", "a2a", "audit"]) {
      expect(isOrgConfigTabHidden(id)).toBe(false);
    }
    expect(Object.keys(HIDDEN_ORG_CONFIG_TABS)).toEqual([]);
  });
});
