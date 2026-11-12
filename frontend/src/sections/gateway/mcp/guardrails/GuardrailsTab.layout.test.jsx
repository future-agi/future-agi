import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import GuardrailsTab from "./GuardrailsTab";

vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({ data: { servers: [] } }),
  useUpdateMCPGuardrails: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
const { org } = vi.hoisted(() => ({ org: { level: 15 } }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: { mcp: { guardrails: { enabled: true, blocked_tools: ["shell"] } } },
    isLoading: false,
  }),
}));

// The child of the tab's column box that holds `el`.
function columnItem(el) {
  let node = el;
  while (
    node.parentElement &&
    window.getComputedStyle(node.parentElement).flexDirection !== "column"
  ) {
    node = node.parentElement;
  }
  return node;
}

// The outlined stored-settings button stretched across the
// whole content width (790 px at 1024), unlike every other gateway button.
describe("MCP > Guardrails (layout)", () => {
  it.each([
    ["an admin", 15],
    ["a member", 3],
  ])("sizes the stored-settings button to its label for %s", (_who, level) => {
    org.level = level;
    render(<GuardrailsTab gatewayId="default" />);
    const button = screen.getByRole("button", { name: /stored settings$/ });
    const item = columnItem(button);
    expect(window.getComputedStyle(item).alignSelf).toBe("flex-start");
  });
});
