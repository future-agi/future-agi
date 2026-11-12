import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import MCPManagementSection from "./MCPManagementSection";
import ConnectTab from "./connect/ConnectTab";

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
vi.mock("./calls/ToolCallsTab", stubTab("calls"));
vi.mock("./credentials/CredentialsTab", stubTab("credentials"));
vi.mock("./clients/MCPClientsTab", stubTab("clients"));
vi.mock("./guardrails/GuardrailsTab", stubTab("guardrails"));
vi.mock("./playground/PlaygroundTab", stubTab("playground"));
vi.mock("./resources/ResourcesTab", stubTab("resources"));
vi.mock("./prompts/PromptsTab", stubTab("prompts"));
vi.mock("./registry/RegistryTab", stubTab("registry"));
vi.mock("./trust/TrustTab", stubTab("trust"));
vi.mock("./approvals/ApprovalsTab", stubTab("approvals"));
vi.mock("./connect/ConnectTab", () => ({
  default: vi.fn(() => <div>connect tab</div>),
}));

const theme = createTheme({ palette: palette("light"), typography });
const NOT_AVAILABLE = "Not available in this deployment";
let location;
function LocationProbe() {
  location = useLocation();
  return null;
}
function mount(url = "/dashboard/gateway/mcp?tab=connect&x=1") {
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
function capability(clientInstalls, otherFeatures = false, virtual = true) {
  statusQuery.current = {
    data: {
      enabled: true,
      features: {
        client_installs: clientInstalls,
        virtual_servers: virtual,
        client_oauth: otherFeatures,
        upstream_oauth: otherFeatures,
        k2_apps: otherFeatures,
      },
    },
  };
}
// Connect stays in the bar, disabled
// like an off Credentials or Virtual servers tab, with the deployment's line
// as its description, and ConnectTab never mounts (was: no Connect tab).
function expectConnectOff() {
  const tab = screen.getByRole("tab", { name: "Connect" });
  expect(tab).toHaveAttribute("aria-disabled", "true");
  expect(tab).toHaveAccessibleDescription(NOT_AVAILABLE);
  expect(ConnectTab).not.toHaveBeenCalled();
  expect(screen.getByRole("tab", { name: "Servers" })).toBeInTheDocument();
}
// A link to Connect lands on the
// disabled tab and shows its line, not Overview (was: Overview).
function expectConnectLinkOff() {
  expectConnectOff();
  expect(
    screen.getByRole("tab", { name: "Connect", selected: true }),
  ).toBeInTheDocument();
  expect(screen.getByTestId("mcp-tab-off-notice")).toHaveTextContent(
    NOT_AVAILABLE,
  );
  expect(screen.queryByText("overview tab")).toBeNull();
}
beforeEach(() => {
  vi.clearAllMocks();
  capability(false, true);
});
describe("client installs independent deployment switch", () => {
  // Both links stay on the disabled
  // Connect tab (was: Overview, with the tab dropped from the URL).
  it.each([
    ["?tab=connect&x=1", "/dashboard/gateway/mcp", "?tab=connect&x=1"],
    ["/connect?x=1", "/dashboard/gateway/mcp/connect", "?x=1"],
  ])("OFF prevents mounting via %s", (link, pathname, search) => {
    mount(`/dashboard/gateway/mcp${link}`);
    expectConnectLinkOff();
    expect(location.pathname).toBe(pathname);
    expect(location.search).toBe(search);
  });
  it("OFF keeps Connect in the bar but not selectable", () => {
    mount("/dashboard/gateway/mcp?tab=servers&x=1");
    expectConnectOff();
    fireEvent.click(screen.getByRole("tab", { name: "Connect" }));
    expect(
      screen.getByRole("tab", { name: "Servers", selected: true }),
    ).toBeInTheDocument();
    expect(screen.getByText("servers tab")).toBeInTheDocument();
    expect(ConnectTab).not.toHaveBeenCalled();
    expect(location.search).toBe("?tab=servers&x=1");
  });
  it.each([undefined, null, "true", 1])(
    "fails closed for capability %s",
    (value) => {
      capability(value, true);
      mount();
      expectConnectLinkOff();
      expect(location.search).toBe("?tab=connect&x=1");
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
    expectConnectLinkOff();
    expect(location.search).toBe("?tab=connect&x=1");
  });
  // Connect also needs the virtual
  // server switch: with it off, Connect stays, disabled, whether client
  // installs is on or off.
  it.each([
    [true, "?tab=connect&x=1", "/dashboard/gateway/mcp", "?tab=connect&x=1"],
    [true, "/connect?x=1", "/dashboard/gateway/mcp/connect", "?x=1"],
    [false, "?tab=connect&x=1", "/dashboard/gateway/mcp", "?tab=connect&x=1"],
    [false, "/connect?x=1", "/dashboard/gateway/mcp/connect", "?x=1"],
  ])(
    "virtual servers OFF with client installs %s keeps %s on disabled Connect",
    (installs, link, pathname, search) => {
      capability(installs, true, false);
      mount(`/dashboard/gateway/mcp${link}`);
      expectConnectLinkOff();
      expect(
        screen.getByRole("tab", { name: "Virtual servers" }),
      ).toHaveAttribute("aria-disabled", "true");
      expect(location.pathname).toBe(pathname);
      expect(location.search).toBe(search);
    },
  );
  it("ON mounts Connect without enabling either OAuth feature", () => {
    capability(true);
    mount();
    expect(screen.getByRole("tab", { name: "Connect" })).toBeInTheDocument();
    // Enabled exactly as before.
    expect(screen.getByRole("tab", { name: "Connect" })).not.toHaveAttribute(
      "aria-disabled",
    );
    // Tool calls and MCP clients are off
    // in this fixture and stay in the bar with the line, so check that Connect
    // itself carries no line (was: no "Not available" text on the page).
    expect(
      screen.getByRole("tab", { name: "Connect" }),
    ).not.toHaveAccessibleDescription(NOT_AVAILABLE);
    expect(screen.queryByTestId("mcp-tab-off-notice")).toBeNull();
    expect(screen.getByText("connect tab")).toBeInTheDocument();
    expect(ConnectTab).toHaveBeenCalledWith(
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
      // Connect is in the bar, disabled,
      // while the status loads, and nothing mounts behind it.
      expectConnectOff();
      expect(screen.queryByText("overview tab")).toBeNull();
      expect(
        screen.getByRole("status", { name: "Loading MCP status" }),
      ).toBeInTheDocument();
      expect(location.search).toBe("?tab=connect&x=1");
      capability(enabled);
      view.rerender(view.tree());
      if (enabled) {
        expect(screen.getByText("connect tab")).toBeInTheDocument();
      } else {
        expectConnectLinkOff();
        expect(location.search).toBe("?tab=connect&x=1");
      }
    },
  );
});
