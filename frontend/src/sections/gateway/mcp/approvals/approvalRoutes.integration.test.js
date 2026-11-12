import { describe, expect, it } from "vitest";
import { endpoints } from "src/utils/axios";
import { approvalRoutesReady } from "./useApprovals";

// Deliberately unmocked. This gate stays RED until main regenerates the
// canonical contract and integrates the exact registry proposal. Unit tests
// are not evidence that this entry point exists in the shipped console.
describe("W11 canonical endpoint integration", () => {
  it("installs the real list, poll and decision registry entries", () => {
    expect(approvalRoutesReady()).toBe(true);
    expect(endpoints.gateway.mcpApprovals).toBe("/agentcc/mcp/approvals/");
    expect(endpoints.gateway.mcpApproval("sealed")).toBe(
      "/agentcc/mcp/approvals/sealed/",
    );
    expect(endpoints.gateway.mcpApprovalDecision("sealed")).toBe(
      "/agentcc/mcp/approvals/sealed/decision/",
    );
  });
});
