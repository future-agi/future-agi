import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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
function capability(clientInstalls, otherFeatures = false) {
  statusQuery.current = {
    data: {
      enabled: true,
      features: {
        client_installs: clientInstalls,
        client_oauth: otherFeatures,
        upstream_oauth: otherFeatures,
        k2_apps: otherFeatures,
      },
    },
  };
}
function expectNoConnect() {
  expect(screen.queryByRole("tab", { name: "Connect" })).toBeNull();
  expect(ConnectTab).not.toHaveBeenCalled();
  if (statusQuery.current.isLoading)
    expect(screen.queryByText("overview tab")).toBeNull();
  else expect(screen.getByText("overview tab")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Servers" })).toBeInTheDocument();
}
beforeEach(() => {
  vi.clearAllMocks();
  capability(false, true);
});
describe("client installs independent deployment switch", () => {
  it.each(["?tab=connect&x=1", "/connect?x=1"])(
    "OFF prevents mounting via %s",
    (link) => {
      mount(`/dashboard/gateway/mcp${link}`);
      expectNoConnect();
      expect(location.pathname).toBe("/dashboard/gateway/mcp");
      expect(location.search).toBe("?x=1");
    },
  );
  it.each([undefined, null, "true", 1])(
    "fails closed for capability %s",
    (value) => {
      capability(value, true);
      mount();
      expectNoConnect();
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
    expectNoConnect();
  });
  it("ON mounts Connect without enabling either OAuth feature", () => {
    capability(true);
    mount();
    expect(screen.getByRole("tab", { name: "Connect" })).toBeInTheDocument();
    expect(screen.getByText("connect tab")).toBeInTheDocument();
    expect(ConnectTab).toHaveBeenCalledWith(
      expect.objectContaining({ gatewayId: "gw-1" }),
      expect.anything(),
    );
    expect(screen.queryByRole("tab", { name: "MCP clients" })).toBeNull();
  });
  it.each([false, true])(
    "keeps a pending deep link then resolves ON=%s",
    (enabled) => {
      statusQuery.current = { isLoading: true, data: undefined };
      const view = mount();
      expectNoConnect();
      expect(location.search).toBe("?tab=connect&x=1");
      capability(enabled);
      view.rerender(view.tree());
      if (enabled) {
        expect(screen.getByText("connect tab")).toBeInTheDocument();
      } else {
        expectNoConnect();
        expect(location.search).toBe("?x=1");
      }
    },
  );
});
