import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { renderMcp } from "./testing/renderMcp";
import MCPManagementSection from "./MCPManagementSection";

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
  useMCPStatus: () => ({ data: { enabled: true, features: {} } }),
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

// Every style rule in the page that applies to `el`.
function rulesFor(el) {
  return Array.from(document.styleSheets).flatMap((sheet) =>
    Array.from(sheet.cssRules).filter((rule) => {
      try {
        return Boolean(rule.selectorText) && el.matches(rule.selectorText);
      } catch {
        return false;
      }
    }),
  );
}

// The strip zeroed each tab's padding through a
// ".MuiTab-root" rule, so the labels ran together ("Overview Tools Servers
// ..."), 8 px apart, while the Providers tabs keep the theme's spacing.
describe("MCP tools tab strip (layout)", () => {
  it("leaves the tabs' padding and gaps to the theme, as on Providers", () => {
    renderMcp(<MCPManagementSection />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.length).toBeGreaterThan(1);
    for (const tab of tabs) {
      const overrides = rulesFor(tab).filter(
        (rule) =>
          rule.selectorText.includes(".MuiTab-root") &&
          ["padding-left", "padding-right", "margin-right"].some((name) =>
            rule.style.getPropertyValue(name),
          ),
      );
      expect(overrides.map((rule) => rule.cssText)).toEqual([]);
    }
  });
});
