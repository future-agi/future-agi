import React from "react";
import { expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderMcp } from "../testing/renderMcp";
import { isMcpTabHidden } from "../../shell/page-status";
import ApprovalsTab from "./ApprovalsTab";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { result: [] } }) },
  endpoints: {
    gateway: {
      mcpApprovals: "/agentcc/mcp/approvals/",
      mcpApproval: (id) => `/agentcc/mcp/approvals/${id}/`,
      mcpApprovalDecision: (id) => `/agentcc/mcp/approvals/${id}/decision/`,
    },
  },
}));
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({ data: { features: { approvals: true } } }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));

it("replaces the unavailable shell with a usable approval list", async () => {
  renderMcp(<ApprovalsTab gatewayId="synthetic-gateway" />);
  expect(
    screen.getByRole("heading", { name: "Tool approvals" }),
  ).toBeInTheDocument();
  expect(await screen.findByText("No approval requests")).toBeInTheDocument();
});

it("shows the approval tab rather than silently cutting the feature", () => {
  expect(isMcpTabHidden("approvals")).toBe(false);
});
