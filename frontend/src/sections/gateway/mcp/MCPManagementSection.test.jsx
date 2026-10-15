import React from "react";
import { SnackbarProvider } from "notistack";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { renderWithRouter, screen } from "src/utils/test-utils";
import MCPManagementSection from "./MCPManagementSection";

const mocks = vi.hoisted(() => ({
  gatewayId: "test-gateway",
  config: {},
  mutate: vi.fn(),
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: mocks.gatewayId, isLoading: false }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: mocks.config, isLoading: false }),
  useReloadConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("./hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({ data: { servers: [] }, isLoading: false }),
  useMCPTools: () => ({ data: [], isLoading: false }),
  useMCPResources: () => ({ data: [], isLoading: false }),
  useMCPPrompts: () => ({ data: [], isLoading: false }),
  useUpdateMCPGuardrails: () => ({ mutate: mocks.mutate, isPending: false }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("./MCPOverviewTab", () => ({
  default: () => <div>Overview content</div>,
}));
vi.mock("./MCPToolsTab", () => ({ default: () => <div>Tools content</div> }));
vi.mock("./MCPServersTab", () => ({
  default: () => <div>Servers content</div>,
}));
vi.mock("./MCPResourcesTab", () => ({
  default: () => <div>Resources content</div>,
}));
vi.mock("./MCPPromptsTab", () => ({
  default: () => <div>Prompts content</div>,
}));
vi.mock("./MCPPlaygroundTab", () => ({
  default: () => <div>Playground content</div>,
}));
vi.mock("./AddMCPServerDialog", () => ({ default: () => null }));

const MCPRoutes = () => (
  <SnackbarProvider>
    <Routes>
      <Route path="/dashboard/gateway/mcp" element={<MCPManagementSection />} />
      <Route
        path="/dashboard/gateway/mcp/:tab"
        element={<MCPManagementSection />}
      />
    </Routes>
  </SnackbarProvider>
);

const renderMCP = () =>
  renderWithRouter(<MCPRoutes />, {
    route: "/dashboard/gateway/mcp/guardrails",
  });

beforeEach(() => {
  mocks.gatewayId = "test-gateway";
  mocks.config = { mcp: { enabled: true, guardrails: { enabled: false } } };
  mocks.mutate.mockReset();
});

describe("MCP guardrails drafts across tabs", () => {
  it("preserves enabled guardrails and an added rate limit after a Playground round trip", async () => {
    const user = userEvent.setup();
    renderMCP();
    await user.click(
      screen.getByRole("checkbox", { name: "Enable MCP Guardrails" }),
    );
    await user.type(
      screen.getByRole("textbox", { name: "Tool Name" }),
      "test_search",
    );
    await user.type(screen.getByRole("spinbutton", { name: "Max/min" }), "10");
    await user.click(screen.getByRole("button", { name: "Add" }));

    await user.click(screen.getByRole("tab", { name: "Playground" }));
    expect(screen.getByText("Playground content")).toBeVisible();
    expect(
      screen.queryByRole("checkbox", { name: "Enable MCP Guardrails" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Guardrails" }));

    expect(
      screen.getByRole("checkbox", { name: "Enable MCP Guardrails" }),
    ).toBeChecked();
    expect(screen.getByText("test_search")).toBeVisible();
    expect(screen.getByText("10/min")).toBeVisible();
    expect(screen.getByText("You have unsaved changes.")).toBeVisible();
  });

  it("preserves all guardrail fields and unfinished inputs across every other MCP tab", async () => {
    const user = userEvent.setup();
    renderMCP();
    await user.click(
      screen.getByRole("checkbox", { name: "Enable MCP Guardrails" }),
    );
    await user.click(
      screen.getByRole("checkbox", { name: /Validate tool inputs/ }),
    );
    await user.click(
      screen.getByRole("checkbox", { name: "Validate tool outputs" }),
    );
    await user.type(
      screen.getByPlaceholderText("Type a tool name and press Enter..."),
      "test_delete{Enter}",
    );
    await user.type(
      screen.getByPlaceholderText(
        "Type a server ID or select from connected...",
      ),
      "test-server{Enter}",
    );
    await user.type(
      screen.getByPlaceholderText("Type a regex pattern and press Enter..."),
      "blocked.*{Enter}",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Tool Name" }),
      "unfinished_tool",
    );
    await user.type(screen.getByRole("spinbutton", { name: "Max/min" }), "25");
    await user.type(
      screen.getByPlaceholderText("Type a tool name and press Enter..."),
      "unfinished_blocked",
    );

    for (const tabName of [
      "Overview",
      "Tools",
      "Servers",
      "Resources",
      "Prompts",
      "Playground",
    ]) {
      await user.click(screen.getByRole("tab", { name: tabName }));
      expect(screen.getByText(`${tabName} content`)).toBeVisible();
      expect(
        screen.queryByRole("button", { name: "Save Changes" }),
      ).not.toBeInTheDocument();
      await user.click(screen.getByRole("tab", { name: "Guardrails" }));
      expect(
        screen.getByRole("checkbox", { name: /Validate tool inputs/ }),
      ).toBeChecked();
      expect(
        screen.getByRole("checkbox", { name: "Validate tool outputs" }),
      ).toBeChecked();
      expect(screen.getByText("test_delete")).toBeVisible();
      expect(screen.getByText("test-server")).toBeVisible();
      expect(screen.getByText("blocked.*")).toBeVisible();
      expect(screen.getByRole("textbox", { name: "Tool Name" })).toHaveValue(
        "unfinished_tool",
      );
      expect(screen.getByRole("spinbutton", { name: "Max/min" })).toHaveValue(
        25,
      );
      expect(
        screen.getByPlaceholderText("Type a tool name and press Enter..."),
      ).toHaveValue("unfinished_blocked");
    }
    expect(mocks.mutate).not.toHaveBeenCalled();
  });

  it("submits the preserved draft only when the user explicitly saves", async () => {
    const user = userEvent.setup();
    mocks.mutate.mockImplementation((_, { onSuccess }) => onSuccess());
    renderMCP();
    await user.click(
      screen.getByRole("checkbox", { name: "Enable MCP Guardrails" }),
    );
    await user.click(screen.getByRole("tab", { name: "Playground" }));
    await user.click(screen.getByRole("tab", { name: "Guardrails" }));
    expect(mocks.mutate).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(mocks.mutate).toHaveBeenCalledExactlyOnceWith(
      {
        gatewayId: "test-gateway",
        config: {
          enabled: true,
          blocked_tools: [],
          allowed_servers: [],
          validate_inputs: false,
          validate_outputs: false,
          custom_patterns: [],
          tool_rate_limits: {},
        },
      },
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
      }),
    );
    expect(
      screen.queryByText("You have unsaved changes."),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Playground" }));
    await user.click(screen.getByRole("tab", { name: "Guardrails" }));
    expect(
      screen.getByRole("checkbox", { name: "Enable MCP Guardrails" }),
    ).toBeChecked();
  });

  it("retains the unsaved draft after a failed save and tab switch", async () => {
    const user = userEvent.setup();
    mocks.mutate.mockImplementation((_, { onError }) => onError());
    renderMCP();
    await user.click(
      screen.getByRole("checkbox", { name: "Enable MCP Guardrails" }),
    );
    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    await user.click(screen.getByRole("tab", { name: "Playground" }));
    await user.click(screen.getByRole("tab", { name: "Guardrails" }));
    expect(
      screen.getByRole("checkbox", { name: "Enable MCP Guardrails" }),
    ).toBeChecked();
    expect(screen.getByText("You have unsaved changes.")).toBeVisible();
  });

  it("does not carry a draft into another gateway with the same saved settings", async () => {
    const user = userEvent.setup();
    const view = renderMCP();
    await user.click(
      screen.getByRole("checkbox", { name: "Enable MCP Guardrails" }),
    );
    await user.type(
      screen.getByRole("textbox", { name: "Tool Name" }),
      "first_gateway_only",
    );
    await user.click(screen.getByRole("tab", { name: "Playground" }));
    mocks.gatewayId = "another-test-gateway";
    view.rerender(<MCPRoutes />);
    await user.click(screen.getByRole("tab", { name: "Guardrails" }));
    expect(
      screen.getByRole("checkbox", { name: "Enable MCP Guardrails" }),
    ).not.toBeChecked();
    expect(screen.getByRole("textbox", { name: "Tool Name" })).toHaveValue("");
    expect(
      screen.queryByText("You have unsaved changes."),
    ).not.toBeInTheDocument();
  });
});
