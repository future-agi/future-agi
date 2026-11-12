import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import ApprovalsTab from "./ApprovalsTab";

vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({ data: { features: { approvals: true } } }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true, disabledReason: "" }),
}));
vi.mock("./useApprovals", () => ({
  approvalRoutesReady: () => true,
  useApprovalDecision: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useApprovals: () => ({ isError: false, refetch: vi.fn(), data: [] }),
}));

// Every MCP tab starts at the page frame's 12px gap under the tab bar. A tab
// with its own top padding makes the content jump when the user switches
// tabs (screen pass F09, seen at 1024, 1280 and 1440).
describe("Approvals tab spacing", () => {
  it("starts at the frame gap, with no top padding of its own", () => {
    const { container } = renderMcp(<ApprovalsTab gatewayId="gw" />);
    expect(screen.getByText("Tool approvals")).toBeInTheDocument();
    expect(container.firstElementChild).not.toHaveStyle({
      paddingTop: "16px",
    });
  });
});
