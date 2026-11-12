import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { renderMcp } from "./testing/renderMcp";
import MCPManagementSection from "./MCPManagementSection";
import { DISABLED_MCP_NOTICE } from "./tabs";

const { stub } = vi.hoisted(() => ({
  stub: (label) => () => ({ default: () => `${label} body` }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-one" }),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: { mcp: { enabled: true, servers: {} } } }),
  useReloadConfig: () => ({ mutate: vi.fn() }),
}));
vi.mock("./hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({
    data: {
      enabled: true,
      features: {
        catalog: true,
        credential_vault: true,
        virtual_servers: true,
        tool_call_explorer: true,
      },
    },
  }),
  useMCPResources: () => ({ data: [] }),
  useMCPPrompts: () => ({ data: [] }),
}));
vi.mock("./hooks/useMCPConfigPatch", () => ({
  useMCPConfigPatch: () => ({ mutate: vi.fn() }),
}));
vi.mock("./overview/OverviewTab", stub("Overview"));
vi.mock("./servers/ServersTab", stub("Servers"));
vi.mock("./virtual/VirtualServersTab", stub("Virtual servers"));
vi.mock("./calls/ToolCallsTab", stub("Tool calls"));
vi.mock("./credentials/CredentialsTab", stub("Credentials"));
vi.mock("./clients/MCPClientsTab", stub("MCP clients"));
vi.mock("./guardrails/GuardrailsTab", stub("Guardrails"));
vi.mock("./playground/PlaygroundTab", stub("Try a tool"));
vi.mock("./connect/ConnectTab", stub("Connect"));

// The notice under the tab bar takes the page frame's 12px gaps above and
// below it, like every other block, and adds no margin of its own (screen
// pass F09: it sat 28px from the tab bar and from the tab's content).
describe("MCP tabs notice spacing", () => {
  it("keeps the disabled-tabs notice at the frame gap", () => {
    renderMcp(<MCPManagementSection />);
    expect(screen.getByText("Overview body")).toBeInTheDocument();
    expect(screen.getByText(DISABLED_MCP_NOTICE)).not.toHaveStyle({
      marginTop: "16px",
    });
  });
});
