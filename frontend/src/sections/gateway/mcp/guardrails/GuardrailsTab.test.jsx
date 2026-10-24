import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import GuardrailsTab from "./GuardrailsTab";

// The write controls are Owner or Admin only (useGatewayAdmin); these cases
// act as an Admin. AdminGating.test.jsx covers the other levels.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const mockMutate = vi.fn();
let mockMutationState = {};

vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({ data: { servers: [] } }),
  useUpdateMCPGuardrails: () => ({
    mutate: mockMutate,
    isPending: false,
    ...mockMutationState,
  }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: storedConfig, isLoading: false }),
  isSavedButNotApplied: (error) => Boolean(error?.savedButNotApplied),
}));

// A stored config that holds a key the per-org gateway config does not have.
const { storedConfig } = vi.hoisted(() => ({
  storedConfig: {
    mcp: {
      guardrails: {
        enabled: true,
        blocked_tools: ["shell"],
        custom_patterns: ["(?i)password"],
      },
    },
  },
}));

describe("GuardrailsTab", () => {
  beforeEach(() => {
    mockMutate.mockClear();
    mockMutationState = {};
  });

  it("shows the reason when the save is rejected", () => {
    mockMutationState = {
      isError: true,
      error: {
        message:
          "The gateway cannot accept this config. mcp.guardrails.custom_patterns: Extra inputs are not permitted",
      },
    };
    render(<GuardrailsTab gatewayId="default" />);

    expect(
      screen.getByText(/mcp\.guardrails\.custom_patterns: Extra inputs/),
    ).toBeInTheDocument();
  });

  it("saves only the settings the per-org gateway config has", async () => {
    const user = userEvent.setup();
    render(<GuardrailsTab gatewayId="default" />);

    // The save button only shows once the form is dirty.
    await user.click(screen.getByLabelText("Validate tool outputs"));
    await user.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(mockMutate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      config: {
        enabled: true,
        blocked_tools: ["shell"],
        allowed_servers: [],
        validate_inputs: false,
        validate_outputs: true,
        tool_rate_limits: {},
      },
    });
  });

  it("does not offer custom injection patterns", () => {
    render(<GuardrailsTab gatewayId="default" />);

    expect(screen.queryByText("Custom Injection Patterns")).toBeNull();
  });
});
