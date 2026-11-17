import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import GuardrailsTab from "./GuardrailsTab";

vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({ data: { servers: [] } }),
  useUpdateMCPGuardrails: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
// An Owner or Admin, who gets the button itself, not the member's wrapper.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: { mcp: {} }, isLoading: false }),
}));

// The stored-settings button keeps its own width instead of stretching across
// the page (screen pass N03: 792px wide at 1024, 1208px at 1440).
describe("MCP guardrails stored-settings button", () => {
  it("keeps its own width in the tab's column", () => {
    render(<GuardrailsTab gatewayId="default" />);
    expect(
      screen.getByRole("button", { name: "Add stored settings" }),
    ).toHaveStyle({ alignSelf: "flex-start" });
  });
});
