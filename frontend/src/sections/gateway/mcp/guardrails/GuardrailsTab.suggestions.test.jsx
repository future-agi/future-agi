import React from "react";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, within } from "src/utils/test-utils";
import GuardrailsTab from "./GuardrailsTab";

// Allowed servers suggests the servers the MCP status lists for the
// organization (v1.58.0 mcp/MCPGuardrailsTab.jsx).

vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({
    data: {
      enabled: true,
      servers: [
        { server_id: "github", healthy: true, tool_count: 2 },
        { server_id: "search", healthy: false, tool_count: 0 },
        { healthy: true },
      ],
    },
  }),
  useUpdateMCPGuardrails: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
// An Admin, who may edit the stored settings.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: {
      mcp: { guardrails: { enabled: true, allowed_servers: ["search"] } },
    },
    isLoading: false,
  }),
}));

describe("MCP > Guardrails: Allowed servers suggestions", () => {
  it("offers the connected servers from the MCP status", async () => {
    const user = userEvent.setup();
    render(<GuardrailsTab gatewayId="gw-1" />);
    await user.click(
      screen.getByRole("button", { name: "Edit stored settings" }),
    );
    const dialog = screen.getByRole("dialog", {
      name: "Stored MCP guardrail settings",
    });
    expect(
      within(dialog).getByRole("button", {
        name: "Add github to Allowed servers",
      }),
    ).toBeInTheDocument();
    // Already allowed.
    expect(
      within(dialog).queryByRole("button", {
        name: "Add search to Allowed servers",
      }),
    ).toBeNull();
  });
});
