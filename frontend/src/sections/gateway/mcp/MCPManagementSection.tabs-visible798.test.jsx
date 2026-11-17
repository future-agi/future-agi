import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderMcp } from "./testing/renderMcp";
import MCPManagementSection from "./MCPManagementSection";

const { resources, prompts, gateway, config, stub } = vi.hoisted(() => ({
  resources: vi.fn(() => ({ data: [] })),
  prompts: vi.fn(() => ({ data: [] })),
  gateway: { id: "gw-one" },
  config: { current: {} },
  stub: (label) => () => ({ default: () => `${label} body` }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: gateway.id }),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => config.current,
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
  useMCPResources: resources,
  useMCPPrompts: prompts,
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

beforeEach(() => {
  vi.clearAllMocks();
  gateway.id = "gw-one";
  config.current = { data: { mcp: { enabled: true, servers: {} } } };
});

// Resources and Prompts are enabled tabs, as they were in v1.58.0: each opens
// the list of what the organization's MCP servers expose.
describe("the Resources and Prompts tabs", () => {
  it("are enabled among the other tabs, with no disabled notice", () => {
    renderMcp(<MCPManagementSection />);
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Overview",
      "Servers",
      "Virtual servers",
      "Tool calls",
      "Credentials",
      // MCP clients stays in the bar,
      // disabled, while client OAuth is off.
      "MCP clients",
      "Guardrails",
      "Try a tool",
      "Resources",
      "Prompts",
      // Connect stays in the bar,
      // disabled, while client installs is off.
      "Connect",
      "Tool revisions",
      // Approvals stays visible.
      "Approvals",
    ]);
    for (const name of ["Resources", "Prompts"]) {
      const tab = screen.getByRole("tab", { name });
      expect(tab).not.toBeDisabled();
      expect(tab).not.toHaveAttribute("aria-disabled");
    }
    expect(screen.queryByText(/tabs are disabled/)).toBeNull();
    expect(screen.getByText("Overview body")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(resources).not.toHaveBeenCalled();
    expect(prompts).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("tab", { name: "Servers" }));
    expect(screen.getByText("Servers body")).toBeInTheDocument();
  });

  it.each([
    ["Resources", "No resources", resources],
    ["Prompts", "No prompts", prompts],
  ])("opens %s and reads its list", (name, emptyTitle, hook) => {
    renderMcp(<MCPManagementSection />);
    fireEvent.click(screen.getByRole("tab", { name }));
    expect(
      screen.getByRole("tab", { name, selected: true }),
    ).toBeInTheDocument();
    expect(screen.getByText(emptyTitle)).toBeInTheDocument();
    expect(hook).toHaveBeenCalledWith("gw-one", { enabled: true });
  });

  it.each(["resources", "prompts"])(
    "opens a direct link to %s on its list, for each gateway",
    (slug) => {
      const name = slug === "resources" ? "Resources" : "Prompts";
      const hook = slug === "resources" ? resources : prompts;
      const emptyTitle = slug === "resources" ? "No resources" : "No prompts";
      const { unmount } = renderMcp(<MCPManagementSection />, {
        route: `/dashboard/gateway/mcp?tab=${slug}`,
      });
      expect(
        screen.getByRole("tab", { name, selected: true }),
      ).not.toBeDisabled();
      expect(screen.getByText(emptyTitle)).toBeInTheDocument();
      expect(hook).toHaveBeenCalledWith("gw-one", { enabled: true });
      unmount();
      gateway.id = "gw-two";
      renderMcp(<MCPManagementSection />, {
        route: `/dashboard/gateway/mcp?tab=${slug}`,
      });
      expect(screen.getByText(emptyTitle)).toBeInTheDocument();
      expect(hook).toHaveBeenCalledWith("gw-two", { enabled: true });
    },
  );

  it.each([
    ["loading", { isLoading: true }],
    ["failed", { isError: true }],
    ["empty", { data: {} }],
  ])("opens the resources list while config is %s", (_, query) => {
    config.current = query;
    renderMcp(<MCPManagementSection />, {
      route: "/dashboard/gateway/mcp?tab=resources",
    });
    expect(screen.getByText("No resources")).toBeInTheDocument();
    expect(resources).toHaveBeenCalledWith("gw-one", { enabled: true });
    expect(prompts).not.toHaveBeenCalled();
  });
});
