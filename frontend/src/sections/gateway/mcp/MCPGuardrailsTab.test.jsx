import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import MCPGuardrailsTab from "./MCPGuardrailsTab";

const mockMutate = vi.fn();
let mockMutationState = {};

vi.mock("./hooks/useMCPConfig", () => ({
  useUpdateMCPGuardrails: () => ({
    mutate: mockMutate,
    isPending: false,
    ...mockMutationState,
  }),
}));

// A stored config that still holds the key an older build of this tab wrote.
const storedConfig = {
  mcp: {
    guardrails: {
      enabled: true,
      blocked_tools: ["shell"],
      custom_patterns: ["(?i)password"],
    },
  },
};

describe("MCPGuardrailsTab", () => {
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
    render(
      <MCPGuardrailsTab
        config={storedConfig}
        mcpStatus={{ servers: [] }}
        gatewayId="default"
      />,
    );

    expect(
      screen.getByText(/mcp\.guardrails\.custom_patterns: Extra inputs/),
    ).toBeInTheDocument();
  });

  it("saves only the settings the per-org gateway config has", async () => {
    const user = userEvent.setup();
    render(
      <MCPGuardrailsTab
        config={storedConfig}
        mcpStatus={{ servers: [] }}
        gatewayId="default"
      />,
    );

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
    render(
      <MCPGuardrailsTab
        config={storedConfig}
        mcpStatus={{ servers: [] }}
        gatewayId="default"
      />,
    );

    expect(screen.queryByText("Custom Injection Patterns")).toBeNull();
  });
});
