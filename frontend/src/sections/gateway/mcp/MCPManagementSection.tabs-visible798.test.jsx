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
    data: { enabled: true, features: { tool_call_explorer: true } },
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

const NOTICE = "The Resources and Prompts tabs are disabled.";

beforeEach(() => {
  vi.clearAllMocks();
  gateway.id = "gw-one";
  config.current = { data: { mcp: { enabled: true, servers: {} } } };
});

describe("798: visible, disabled MCP tabs", () => {
  it("renders both disabled tabs and their notice without changing existing tabs", () => {
    renderMcp(<MCPManagementSection />);
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Overview",
      "Servers",
      "Virtual servers",
      "Tool calls",
      "Credentials",
      "Guardrails",
      "Try a tool",
      "Resources",
      "Prompts",
    ]);
    for (const name of ["Resources", "Prompts"]) {
      const tab = screen.getByRole("tab", { name });
      expect(tab).toBeDisabled();
      expect(tab).toHaveAccessibleDescription(NOTICE);
      fireEvent.click(tab);
      expect(screen.getByText("Overview body")).toBeInTheDocument();
    }
    expect(screen.getByText(NOTICE)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Servers" }));
    expect(screen.getByText("Servers body")).toBeInTheDocument();
    expect(resources).not.toHaveBeenCalled();
    expect(prompts).not.toHaveBeenCalled();
  });

  it.each(["resources", "prompts"])(
    "keeps a direct link to %s visible without calling either data hook, including after remounting with a different gateway",
    (slug) => {
      const { unmount } = renderMcp(<MCPManagementSection />, {
        route: `/dashboard/gateway/mcp?tab=${slug}`,
      });
      const name = slug === "resources" ? "Resources" : "Prompts";
      expect(screen.getByRole("tab", { name, selected: true })).toBeDisabled();
      expect(screen.getByText(NOTICE)).toBeInTheDocument();
      expect(resources).not.toHaveBeenCalled();
      expect(prompts).not.toHaveBeenCalled();
      unmount();
      gateway.id = "gw-two";
      renderMcp(<MCPManagementSection />, {
        route: `/dashboard/gateway/mcp?tab=${slug}`,
      });
      expect(screen.getByRole("tab", { name, selected: true })).toBeDisabled();
      expect(screen.getByText(NOTICE)).toBeInTheDocument();
      expect(resources).not.toHaveBeenCalled();
      expect(prompts).not.toHaveBeenCalled();
    },
  );

  it.each([
    ["loading", { isLoading: true }],
    ["failed", { isError: true }],
    ["empty", { data: {} }],
  ])("never loads resource or prompt data while config is %s", (_, query) => {
    config.current = query;
    renderMcp(<MCPManagementSection />, {
      route: "/dashboard/gateway/mcp?tab=resources",
    });
    expect(screen.getByText(NOTICE)).toBeInTheDocument();
    expect(resources).not.toHaveBeenCalled();
    expect(prompts).not.toHaveBeenCalled();
  });
});
