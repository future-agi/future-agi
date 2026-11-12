import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import axios from "src/utils/axios";
import MCPManagementSection from "./MCPManagementSection";
import MCPClientsTab from "./clients/MCPClientsTab";

const { statusQuery, patchMutate, stubTab } = vi.hoisted(() => ({
  statusQuery: { current: {} },
  patchMutate: vi.fn(),
  stubTab: (name) => () => ({ default: () => <div>{`${name} tab`}</div> }),
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn(), delete: vi.fn() },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "member-a" } }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-a", orgLevel: 8 }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1", isLoading: false }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useReloadConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useGatewayConfig: () => ({ data: { mcp: { enabled: true } } }),
  SavedButNotAppliedError: Error,
}));
vi.mock("./hooks/useMCPConfig", () => ({
  useMCPStatus: () => statusQuery.current,
}));
vi.mock("./hooks/useMCPConfigPatch", () => ({
  useMCPConfigPatch: () => ({ mutate: patchMutate, isPending: false }),
}));
vi.mock("./overview/OverviewTab", stubTab("overview"));
vi.mock("./servers/ServersTab", stubTab("servers"));
vi.mock("./virtual/VirtualServersTab", stubTab("virtual"));
vi.mock("./calls/ToolCallsTab", stubTab("calls"));
vi.mock("./credentials/CredentialsTab", stubTab("credentials"));
vi.mock("./guardrails/GuardrailsTab", stubTab("guardrails"));
vi.mock("./playground/PlaygroundTab", stubTab("playground"));
vi.mock("./resources/ResourcesTab", stubTab("resources"));
vi.mock("./prompts/PromptsTab", stubTab("prompts"));
vi.mock("./registry/RegistryTab", stubTab("registry"));
vi.mock("./connect/ConnectTab", stubTab("connect"));
vi.mock("./trust/TrustTab", stubTab("trust"));
vi.mock("./approvals/ApprovalsTab", stubTab("approvals"));
// Run the real client component and query; spy only to prove it never mounts
// when the capability is unavailable, not just that its DOM is later hidden.
vi.mock("./clients/MCPClientsTab", async (importOriginal) => ({
  default: vi.fn((await importOriginal()).default),
}));

const theme = createTheme({ palette: palette("light"), typography });
const NOT_AVAILABLE = "Not available in this deployment";
const CONSENTS = "/agentcc/mcp-oauth/consents/";
const grant = {
  id: 1,
  client_name: "Synthetic desktop",
  client_id: "synthetic-client",
  user: { id: "member-a", name: "Member A" },
  organization: { id: "org-a", name: "Organization A" },
  resource: "http://127.0.0.1/mcp",
  scopes: ["mcp:tools"],
  granted_at: "2026-10-28T00:00:00Z",
  can_revoke: true,
};
let location;
function LocationProbe() {
  location = useLocation();
  return null;
}
function mount(url = "/dashboard/gateway/mcp") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const tree = () => (
    <QueryClientProvider client={client}>
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
    </QueryClientProvider>
  );
  return { ...render(tree()), tree };
}
function capability(clientOAuth, coreMcp = true) {
  statusQuery.current = {
    data: {
      enabled: coreMcp,
      ...(coreMcp ? {} : { reason: "gateway_mcp_disabled" }),
      // Independent of the two other switches: neither can enable clients.
      features: {
        upstream_oauth: true,
        k2_apps: true,
        client_oauth: clientOAuth,
      },
    },
    isError: false,
  };
}
// The MCP-off bar (MCP_OFF_TABS) has no clients entry unless client OAuth is
// on. The MCP-on rule leaves that bar unchanged, so the
// core-MCP-off cases keep this check.
function expectNoClients() {
  expect(
    screen.queryByRole("tab", { name: /^(Manage MCP clients|MCP clients)$/ }),
  ).toBeNull();
  expect(screen.queryByRole("heading", { name: "MCP clients" })).toBeNull();
  expect(MCPClientsTab).not.toHaveBeenCalled();
  expect(axios.get).not.toHaveBeenCalled();
  expect(axios.delete).not.toHaveBeenCalled();
}
// With MCP on, MCP clients stays in the
// bar, disabled, with the deployment's line as its description; the clients
// component never mounts and no consent is requested (was: expectNoClients,
// no clients tab).
function expectClientsOff() {
  const tab = screen.getByRole("tab", { name: "MCP clients" });
  expect(tab).toHaveAttribute("aria-disabled", "true");
  expect(tab).toHaveAccessibleDescription(NOT_AVAILABLE);
  expect(screen.queryByRole("tab", { name: "Manage MCP clients" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "MCP clients" })).toBeNull();
  expect(MCPClientsTab).not.toHaveBeenCalled();
  expect(axios.get).not.toHaveBeenCalled();
  expect(axios.delete).not.toHaveBeenCalled();
}
// A link to MCP clients lands on the
// disabled tab and shows its line, not Overview (was: Overview).
function expectClientsLinkOff() {
  expectClientsOff();
  expect(
    screen.getByRole("tab", { name: "MCP clients", selected: true }),
  ).toBeInTheDocument();
  expect(screen.getByTestId("mcp-tab-off-notice")).toHaveTextContent(
    NOT_AVAILABLE,
  );
  expect(screen.queryByText("overview tab")).toBeNull();
}
async function expectClientsLoaded() {
  await screen.findByText("Synthetic desktop");
  expect(MCPClientsTab).toHaveBeenCalled();
  expect(axios.get).toHaveBeenCalledWith(
    CONSENTS,
    expect.objectContaining({
      params: { scope: "self" },
      headers: { "X-Organization-Id": "org-a" },
    }),
  );
  expect(location.search).toBe("?tab=clients");
  expect(patchMutate).not.toHaveBeenCalled();
}
beforeEach(() => {
  vi.clearAllMocks();
  capability(false);
  axios.get.mockResolvedValue({ data: { status: true, result: [grant] } });
  axios.delete.mockResolvedValue({
    data: { status: true, result: { revoked: true } },
  });
});

describe("client OAuth deployment switch", () => {
  it("disabled: keeps the clients tab in the bar, disabled with the reason", () => {
    mount();
    expect(screen.getByRole("tab", { name: "Overview" })).toBeInTheDocument();
    expectClientsOff();
    fireEvent.click(screen.getByRole("tab", { name: "MCP clients" }));
    expect(
      screen.getByRole("tab", { name: "Overview", selected: true }),
    ).toBeInTheDocument();
    expect(screen.getByText("overview tab")).toBeInTheDocument();
    expect(MCPClientsTab).not.toHaveBeenCalled();
    expect(axios.get).not.toHaveBeenCalled();
    expect(location.search).toBe("");
  });
  // Both link forms stay on the disabled tab.
  it.each([
    ["?tab=clients&x=1", "/dashboard/gateway/mcp", "?tab=clients&x=1"],
    ["/clients?x=1", "/dashboard/gateway/mcp/clients", "?x=1"],
  ])(
    "disabled: deep link %s opens the disabled tab without mounting or requesting consents",
    (link, pathname, search) => {
      mount(`/dashboard/gateway/mcp${link}`);
      expectClientsLinkOff();
      expect(location.pathname).toBe(pathname);
      expect(location.search).toBe(search);
    },
  );
  it("disabled: core-MCP-off screen offers no manage clients action", () => {
    capability(false, false);
    mount();
    expect(
      screen.queryByRole("button", { name: "Manage MCP clients" }),
    ).toBeNull();
    expectNoClients();
  });
  it("enabled: tab opens real grants and revocation works", async () => {
    capability(true);
    mount();
    fireEvent.click(screen.getByRole("tab", { name: "MCP clients" }));
    await expectClientsLoaded();
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(axios.delete).not.toHaveBeenCalled();
    axios.get.mockResolvedValue({ data: { status: true, result: [] } });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Revoke access",
      }),
    );
    await waitFor(() =>
      expect(axios.delete).toHaveBeenCalledWith(`${CONSENTS}1/`, {
        headers: { "X-Organization-Id": "org-a" },
      }),
    );
    await screen.findByText(/Access revoked. Refresh is blocked/);
  });
  it("enabled: deep link mounts and requests real grants", async () => {
    capability(true);
    mount("/dashboard/gateway/mcp?tab=clients");
    await expectClientsLoaded();
  });
  it("enabled: core-MCP-off manage action opens real grants", async () => {
    capability(true, false);
    mount();
    fireEvent.click(screen.getByRole("tab", { name: "Manage MCP clients" }));
    await expectClientsLoaded();
  });
});

describe("client OAuth unavailable status", () => {
  it.each([
    ["missing features", { data: { enabled: true } }],
    ["no status data", { data: undefined, isLoading: false }],
    ["error with stale enabled data", { isError: true }],
    ["non-boolean", { data: { features: { client_oauth: "true" } } }],
  ])("fails closed for %s, including legacy links", (_, query) => {
    capability(true);
    statusQuery.current = { ...statusQuery.current, ...query };
    mount("/dashboard/gateway/mcp/clients?x=1");
    // The path link stays on the
    // disabled MCP clients tab (was: Overview at /dashboard/gateway/mcp?x=1).
    expectClientsLinkOff();
    expect(location.pathname).toBe("/dashboard/gateway/mcp/clients");
    expect(location.search).toBe("?x=1");
  });
  // A failed status read cannot keep
  // cached ON capabilities alive: both switch-gated tabs stay in the bar,
  // disabled, and neither mounts.
  it("failed status read keeps MCP clients and Tool calls disabled, unmounted", () => {
    capability(true);
    statusQuery.current.data.features.tool_call_explorer = true;
    statusQuery.current.isError = true;
    mount("/dashboard/gateway/mcp?tab=clients&x=1");
    expectClientsLinkOff();
    const calls = screen.getByRole("tab", { name: "Tool calls" });
    expect(calls).toHaveAttribute("aria-disabled", "true");
    expect(calls).toHaveAccessibleDescription(NOT_AVAILABLE);
    expect(screen.queryByText("calls tab")).toBeNull();
    expect(location.search).toBe("?tab=clients&x=1");
  });
  it.each([false, true])(
    "waits for status without mounting clients, then resolves client_oauth=%s",
    async (enabled) => {
      statusQuery.current = { data: undefined, isLoading: true };
      const view = mount("/dashboard/gateway/mcp?tab=clients");
      // MCP clients is in the bar,
      // disabled, while the status loads, and nothing mounts behind it.
      expectClientsOff();
      expect(screen.queryByText("overview tab")).toBeNull();
      // Do not lose a valid deep link before the capability read finishes.
      expect(location.search).toBe("?tab=clients");
      capability(enabled);
      view.rerender(view.tree());
      if (enabled) {
        await expectClientsLoaded();
      } else {
        expectClientsLinkOff();
        expect(location.search).toBe("?tab=clients");
      }
    },
  );
  it("core-MCP-off deep link cannot mount clients when disabled", () => {
    capability(false, false);
    mount("/dashboard/gateway/mcp?tab=clients&x=1");
    expectNoClients();
    expect(screen.queryByText("overview tab")).toBeNull();
    expect(location.search).toBe("?x=1");
    expect(
      screen.queryByRole("button", { name: "Manage MCP clients" }),
    ).toBeNull();
  });
});
