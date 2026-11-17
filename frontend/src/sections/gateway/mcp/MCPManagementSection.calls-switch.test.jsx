import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import MCPManagementSection from "./MCPManagementSection";
import ToolCallsTab from "./calls/ToolCallsTab";

const { statusQuery, stubTab } = vi.hoisted(() => ({
  statusQuery: { current: {} },
  stubTab: (name) => () => ({ default: () => <div>{`${name} tab`}</div> }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: false }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useReloadConfig: () => ({ mutate: vi.fn() }),
  useGatewayConfig: () => ({ data: { mcp: { enabled: true } } }),
  SavedButNotAppliedError: Error,
}));
vi.mock("./hooks/useMCPConfig", () => ({
  useMCPStatus: () => statusQuery.current,
}));
vi.mock("./hooks/useMCPConfigPatch", () => ({
  useMCPConfigPatch: () => ({ mutate: vi.fn() }),
}));
// Shell-only fixture; the real drawer and exit transition have dedicated specs.
vi.mock("./calls/SiemExportDrawer", () => ({ default: () => null }));
vi.mock("./overview/OverviewTab", stubTab("overview"));
vi.mock("./servers/ServersTab", stubTab("servers"));
vi.mock("./virtual/VirtualServersTab", stubTab("virtual"));
vi.mock("./connect/ConnectTab", stubTab("connect"));
vi.mock("./credentials/CredentialsTab", stubTab("credentials"));
vi.mock("./clients/MCPClientsTab", stubTab("clients"));
vi.mock("./guardrails/GuardrailsTab", stubTab("guardrails"));
vi.mock("./playground/PlaygroundTab", stubTab("playground"));
vi.mock("./resources/ResourcesTab", stubTab("resources"));
vi.mock("./prompts/PromptsTab", stubTab("prompts"));
vi.mock("./registry/RegistryTab", stubTab("registry"));
vi.mock("./trust/TrustTab", stubTab("trust"));
vi.mock("./approvals/ApprovalsTab", stubTab("approvals"));
vi.mock("./calls/ToolCallsTab", () => ({
  default: vi.fn(() => <div>calls tab</div>),
}));

const theme = createTheme({ palette: palette("light"), typography });
const NOT_AVAILABLE = "Not available in this deployment";
let location;
function LocationProbe() {
  location = useLocation();
  return null;
}
function mount(url = "/dashboard/gateway/mcp?tab=calls&x=1") {
  const tree = () => (
    <HelmetProvider>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route
              path="/dashboard/gateway/mcp/:tab?"
              element={<MCPManagementSection />}
            />
          </Routes>
          <LocationProbe />
        </MemoryRouter>
      </ThemeProvider>
    </HelmetProvider>
  );
  return { ...render(tree()), tree };
}
function capability(explorer, otherFeatures = false) {
  statusQuery.current = {
    data: {
      enabled: true,
      features: {
        tool_call_explorer: explorer,
        client_oauth: otherFeatures,
        upstream_oauth: otherFeatures,
        k2_apps: otherFeatures,
      },
    },
  };
}
// Tool calls stays in the bar, disabled
// like an off Connect, Credentials or Virtual servers tab, with the
// deployment's line as its description, and ToolCallsTab never mounts (was:
// no Tool calls tab).
function expectCallsOff() {
  const tab = screen.getByRole("tab", { name: "Tool calls" });
  expect(tab).toHaveAttribute("aria-disabled", "true");
  expect(tab).toHaveAccessibleDescription(NOT_AVAILABLE);
  expect(ToolCallsTab).not.toHaveBeenCalled();
  expect(screen.queryByText("calls tab")).toBeNull();
  expect(screen.getByRole("tab", { name: "Servers" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Try a tool" })).toBeInTheDocument();
}
// A link to Tool calls lands on the
// disabled tab and shows its line, not Overview (was: Overview).
function expectCallsLinkOff() {
  expectCallsOff();
  expect(
    screen.getByRole("tab", { name: "Tool calls", selected: true }),
  ).toBeInTheDocument();
  expect(screen.getByTestId("mcp-tab-off-notice")).toHaveTextContent(
    NOT_AVAILABLE,
  );
  expect(screen.queryByText("overview tab")).toBeNull();
}
afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  capability(false, true);
});
describe("tool-call explorer independent deployment switch", () => {
  // Both links stay on the disabled
  // Tool calls tab (was: Overview, with the tab dropped from the URL).
  it.each([
    ["?tab=calls&x=1", "/dashboard/gateway/mcp", "?tab=calls&x=1"],
    ["/calls?x=1", "/dashboard/gateway/mcp/calls", "?x=1"],
  ])("OFF prevents mounting via %s", (link, pathname, search) => {
    mount(`/dashboard/gateway/mcp${link}`);
    expectCallsLinkOff();
    expect(location.pathname).toBe(pathname);
    expect(location.search).toBe(search);
  });
  it("OFF keeps Tool calls in the bar but not selectable", () => {
    mount("/dashboard/gateway/mcp?tab=servers&x=1");
    expectCallsOff();
    fireEvent.click(screen.getByRole("tab", { name: "Tool calls" }));
    expect(
      screen.getByRole("tab", { name: "Servers", selected: true }),
    ).toBeInTheDocument();
    expect(screen.getByText("servers tab")).toBeInTheDocument();
    expect(ToolCallsTab).not.toHaveBeenCalled();
    expect(location.search).toBe("?tab=servers&x=1");
  });
  it.each([undefined, null, "true", 1])(
    "fails closed for capability %s",
    (value) => {
      capability(value, true);
      mount();
      expectCallsLinkOff();
      expect(location.search).toBe("?tab=calls&x=1");
    },
  );
  it.each([
    ["missing status", { data: undefined }],
    ["missing features", { data: { enabled: true } }],
    ["failed status with stale ON", { isError: true }],
  ])("fails closed for %s", (_, query) => {
    capability(true);
    Object.assign(statusQuery.current, query);
    mount();
    expectCallsLinkOff();
    expect(location.search).toBe("?tab=calls&x=1");
  });
  // A failed status read cannot keep
  // cached ON capabilities alive: both switch-gated tabs stay in the bar,
  // disabled, and neither mounts.
  it("failed status read keeps Tool calls and MCP clients disabled, unmounted", () => {
    capability(true, true);
    statusQuery.current.isError = true;
    mount();
    expectCallsLinkOff();
    const clients = screen.getByRole("tab", { name: "MCP clients" });
    expect(clients).toHaveAttribute("aria-disabled", "true");
    expect(clients).toHaveAccessibleDescription(NOT_AVAILABLE);
    expect(screen.queryByText("clients tab")).toBeNull();
    expect(location.search).toBe("?tab=calls&x=1");
  });
  it("ON mounts Calls without enabling either OAuth feature", () => {
    capability(true);
    mount();
    expect(screen.getByRole("tab", { name: "Tool calls" })).toBeInTheDocument();
    // Enabled exactly as before.
    expect(
      screen.getByRole("tab", { name: "Tool calls" }),
    ).not.toHaveAttribute("aria-disabled");
    expect(screen.queryByTestId("mcp-tab-off-notice")).toBeNull();
    expect(screen.getByText("calls tab")).toBeInTheDocument();
    expect(ToolCallsTab).toHaveBeenCalledWith(
      expect.objectContaining({ gatewayId: "gw-1" }),
      expect.anything(),
    );
    // MCP clients stays in the bar,
    // disabled, and does not mount (was: no MCP clients tab).
    expect(screen.getByRole("tab", { name: "MCP clients" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.queryByText("clients tab")).toBeNull();
  });
  it.each([false, true])(
    "keeps a pending deep link then resolves ON=%s",
    (enabled) => {
      statusQuery.current = { isLoading: true, data: undefined };
      const view = mount();
      // 1009 waits for the initial status before mounting any data tab.
      // Tool calls is in the bar,
      // disabled, while the status loads (was: no Tool calls tab).
      expectCallsOff();
      expect(screen.queryByText("overview tab")).not.toBeInTheDocument();
      expect(screen.getByRole("status", { name: "Loading MCP status" })).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: "Servers" })).toBeEnabled();
      expect(screen.getByRole("tab", { name: "Try a tool" })).toBeEnabled();
      expect(location.search).toBe("?tab=calls&x=1");
      capability(enabled);
      view.rerender(view.tree());
      if (enabled) {
        expect(screen.getByText("calls tab")).toBeInTheDocument();
      } else {
        expectCallsLinkOff();
        expect(location.search).toBe("?tab=calls&x=1");
      }
    },
  );
});
