import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";

import MCPManagementSection from "./MCPManagementSection";
import { MCP_TABS } from "./tabs";
import OverviewTab from "./overview/OverviewTab";
import ServersTab from "./servers/ServersTab";
import RegistryTab from "./registry/RegistryTab";
import ConnectTab from "./connect/ConnectTab";
import VirtualServersTab from "./virtual/VirtualServersTab";
import GuardrailsTab from "./guardrails/GuardrailsTab";
import TrustTab from "./trust/TrustTab";
import ApprovalsTab from "./approvals/ApprovalsTab";
import ToolCallsTab from "./calls/ToolCallsTab";
import PlaygroundTab from "./playground/PlaygroundTab";
import ResourcesTab from "./resources/ResourcesTab";
import PromptsTab from "./prompts/PromptsTab";

const { statusQuery } = vi.hoisted(() => ({
  statusQuery: { current: {} },
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1", isLoading: false }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useReloadConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("./hooks/useMCPConfig", () => ({
  useMCPStatus: () => statusQuery.current,
}));

// The tabs that show data are stubbed: this file tests the shell, and each
// tab's own tests cover its content. The hidden placeholder tabs are wrapped
// in a spy, so a test sees that the shell never renders them.
const { stubTab, spyOnDefault } = vi.hoisted(() => ({
  stubTab: (name) => () => ({
    default: vi.fn(() => <div>{`${name} tab`}</div>),
  }),
  spyOnDefault: async (importOriginal) => {
    const actual = await importOriginal();
    return { ...actual, default: vi.fn(actual.default) };
  },
}));
vi.mock("./overview/OverviewTab", stubTab("overview"));
vi.mock("./servers/ServersTab", stubTab("servers"));
vi.mock("./guardrails/GuardrailsTab", stubTab("guardrails"));
vi.mock("./playground/PlaygroundTab", stubTab("playground"));
vi.mock("./resources/ResourcesTab", stubTab("resources"));
vi.mock("./prompts/PromptsTab", stubTab("prompts"));
vi.mock("./registry/RegistryTab", spyOnDefault);
vi.mock("./connect/ConnectTab", spyOnDefault);
vi.mock("./virtual/VirtualServersTab", spyOnDefault);
vi.mock("./trust/TrustTab", spyOnDefault);
vi.mock("./approvals/ApprovalsTab", spyOnDefault);
vi.mock("./calls/ToolCallsTab", spyOnDefault);

const NOT_ENABLED = "MCP gateway: not enabled";
const STATUS_UNKNOWN = "MCP gateway: status unknown";
const PLACEHOLDER = "Not in this release";

// Each tab's slug and the module the shell must take it from, in tab order.
// Each tab's folder is replaced on its own, so the shell may not import a tab
// from anywhere else.
const FIXED_TAB_MODULES = [
  ["overview", OverviewTab],
  ["servers", ServersTab],
  ["guardrails", GuardrailsTab],
  ["playground", PlaygroundTab],
  ["resources", ResourcesTab],
  ["prompts", PromptsTab],
];
// Tabs whose content is the "Not in this release" placeholder. The shell
// does not list them, and a link to one opens the overview. List a tab
// again only with its real content.
const HIDDEN_TABS = [
  ["registry", "Registry", RegistryTab],
  ["connect", "Connect", ConnectTab],
  ["virtual", "Virtual Servers", VirtualServersTab],
  ["trust", "Trust", TrustTab],
  ["approvals", "Approvals", ApprovalsTab],
  ["calls", "Tool Calls", ToolCallsTab],
];

const testTheme = createTheme({ palette: palette("light") });

let currentLocation;
const LocationProbe = () => {
  currentLocation = useLocation();
  return null;
};

function renderShell(url) {
  return render(
    <ThemeProvider theme={testTheme}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route
            path="/dashboard/gateway/mcp"
            element={<MCPManagementSection />}
          />
          <Route
            path="/dashboard/gateway/mcp/:tab"
            element={<MCPManagementSection />}
          />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("MCPManagementSection tab shell", () => {
  beforeEach(() => {
    statusQuery.current = { data: { enabled: true }, isError: false };
    vi.clearAllMocks();
  });

  it("takes every tab from its fixed module path, in order", () => {
    expect(MCP_TABS.map(({ slug, Content }) => [slug, Content])).toEqual(
      FIXED_TAB_MODULES,
    );
  });

  it("lists only the tabs with real content", () => {
    renderShell("/dashboard/gateway/mcp");

    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Overview",
      "Servers",
      "Guardrails",
      "Playground",
      "Resources",
      "Prompts",
    ]);
  });

  it.each(HIDDEN_TABS)(
    "opens the overview, not the placeholder, for ?tab=%s",
    (slug, label, Module) => {
      renderShell(`/dashboard/gateway/mcp?tab=${slug}`);

      expect(screen.getByText("overview tab")).toBeInTheDocument();
      expect(screen.getByRole("tab", { selected: true })).toHaveTextContent(
        "Overview",
      );
      expect(screen.queryByRole("tab", { name: label })).toBeNull();
      expect(screen.queryByText(PLACEHOLDER)).toBeNull();
      expect(Module).not.toHaveBeenCalled();
    },
  );

  it.each(HIDDEN_TABS)(
    "opens the overview, not the placeholder, for the /mcp/%s path",
    (slug, _label, Module) => {
      renderShell(`/dashboard/gateway/mcp/${slug}`);

      expect(screen.getByText("overview tab")).toBeInTheDocument();
      expect(screen.queryByText(PLACEHOLDER)).toBeNull();
      expect(Module).not.toHaveBeenCalled();
    },
  );

  it("passes the gateway id to the tab it renders", () => {
    renderShell("/dashboard/gateway/mcp?tab=guardrails");

    expect(screen.getByText("guardrails tab")).toBeInTheDocument();
    expect(GuardrailsTab).toHaveBeenCalledWith(
      { gatewayId: "gw-1" },
      expect.anything(),
    );
  });

  it("opens the overview without a tab parameter", () => {
    renderShell("/dashboard/gateway/mcp");

    expect(screen.getByText("overview tab")).toBeInTheDocument();
  });

  it("keeps the tab in the query string when the user switches tabs", () => {
    renderShell("/dashboard/gateway/mcp");

    fireEvent.click(screen.getByRole("tab", { name: "Servers" }));

    expect(currentLocation.pathname).toBe("/dashboard/gateway/mcp");
    expect(new URLSearchParams(currentLocation.search).get("tab")).toBe(
      "servers",
    );
    expect(screen.getByText("servers tab")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));

    expect(currentLocation.search).toBe("");
    expect(screen.getByText("overview tab")).toBeInTheDocument();
  });

  it("still opens links that carry the tab in the path", () => {
    renderShell("/dashboard/gateway/mcp/guardrails");

    expect(screen.getByText("guardrails tab")).toBeInTheDocument();
  });

  it("opens the servers tab for the tools link", () => {
    renderShell("/dashboard/gateway/mcp/tools");

    expect(screen.getByText("servers tab")).toBeInTheDocument();
  });

  it("falls back to the overview for an unknown tab", () => {
    renderShell("/dashboard/gateway/mcp?tab=nope");

    expect(screen.getByText("overview tab")).toBeInTheDocument();
  });
});

describe("MCP status banner", () => {
  beforeEach(() => vi.clearAllMocks());

  it("banner shows Enabled only when the API says enabled: true", () => {
    statusQuery.current = { data: { enabled: true }, isError: false };
    renderShell("/dashboard/gateway/mcp?tab=servers");

    expect(screen.getByText("MCP gateway: Enabled")).toBeInTheDocument();
    expect(screen.queryByText(NOT_ENABLED)).toBeNull();
  });

  it.each([
    ["missing", {}],
    ["false", { enabled: false }],
    ["a string", { enabled: "true" }],
    ["null", { enabled: null }],
  ])(
    "banner shows disabled when enabled is missing or false (%s)",
    (_, data) => {
      statusQuery.current = { data, isError: false };
      renderShell("/dashboard/gateway/mcp?tab=servers");

      expect(screen.getByText(NOT_ENABLED)).toBeInTheDocument();
      expect(screen.queryByText("MCP gateway: Enabled")).toBeNull();
      expect(
        screen.getByText(/did not report MCP as enabled/),
      ).toBeInTheDocument();
    },
  );

  it.each([
    // gateway_mcp_disabled shows the off state instead (MCPOffState.test.jsx).
    ["gateway_unreachable", STATUS_UNKNOWN, /gateway could not be reached/],
    ["something_new", NOT_ENABLED, /did not report MCP as enabled/],
  ])("banner shows the API's reason %s", (reason, title, description) => {
    statusQuery.current = {
      data: { enabled: false, reason },
      isError: false,
    };
    renderShell("/dashboard/gateway/mcp?tab=servers");

    expect(screen.getByText(title)).toBeInTheDocument();
    expect(screen.getByText(reason)).toBeInTheDocument();
    expect(screen.getByText(description)).toBeInTheDocument();
  });

  it("banner shows disabled when the status request fails", () => {
    statusQuery.current = { data: undefined, isError: true };
    renderShell("/dashboard/gateway/mcp?tab=servers");

    expect(screen.getByText(STATUS_UNKNOWN)).toBeInTheDocument();
    expect(
      screen.getByText(/MCP status could not be loaded/),
    ).toBeInTheDocument();
    expect(screen.queryByText("MCP gateway: Enabled")).toBeNull();
  });

  it("shows no verdict while the status is loading", () => {
    statusQuery.current = { data: undefined, isError: false };
    renderShell("/dashboard/gateway/mcp?tab=servers");

    expect(screen.queryByText(/^MCP gateway:/)).toBeNull();
  });
});
