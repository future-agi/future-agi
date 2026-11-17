import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import MCPManagementSection from "./MCPManagementSection";

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
vi.mock("./OffInventory", () => ({
  SavedOverview: () => <div>saved overview tab</div>,
  SavedTools: () => <div>saved tools tab</div>,
}));
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
afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());
const NOT_AVAILABLE = "Not available in this deployment";
// Connect stays in the bar, disabled
// with this line, unless client installs is on too, so "on" includes it.
// Tool calls (the explorer switch) and
// MCP clients (client OAuth) stay the same way, so "on" includes both.
const ON = {
  credential_vault: true,
  virtual_servers: true,
  client_installs: true,
  tool_call_explorer: true,
  client_oauth: true,
};
const tabNames = () => screen.getAllByRole("tab").map((tab) => tab.textContent);

// Credentials and Virtual servers stay in the
// tab bar, and their links stay on them, while mcp-status loads, fails or
// reports an unreachable gateway. A switch the status carries as false keeps
// its tab, disabled, and a link to it shows the deployment's line.
describe("MCP tabs stay while status loads, fails or a switch is off", () => {
  it("loading: keeps the tabs and the deep link", () => {
    statusQuery.current = { isLoading: true, isPending: true };
    mount("/dashboard/gateway/mcp?tab=credentials");
    expect(tabNames()).toEqual(
      expect.arrayContaining(["Credentials", "Virtual servers"]),
    );
    expect(location.search).toBe("?tab=credentials");
  });

  it.each(["credentials", "virtual"])(
    "status read error: keeps the tabs and opens %s",
    (slug) => {
      statusQuery.current = { isError: true };
      mount(`/dashboard/gateway/mcp?tab=${slug}`);
      expect(tabNames()).toEqual(
        expect.arrayContaining(["Credentials", "Virtual servers"]),
      );
      expect(screen.getByText(`${slug} tab`)).toBeInTheDocument();
      expect(location.search).toBe(`?tab=${slug}`);
    },
  );

  it.each(["credentials", "virtual"])(
    "gateway unreachable: reads features and opens %s",
    (slug) => {
      statusQuery.current = {
        data: { enabled: false, reason: "gateway_unreachable", features: ON },
      };
      mount(`/dashboard/gateway/mcp?tab=${slug}`);
      expect(screen.getByText(`${slug} tab`)).toBeInTheDocument();
      expect(location.search).toBe(`?tab=${slug}`);
    },
  );

  it.each([
    ["credentials", "Credentials", "credential_vault"],
    ["virtual", "Virtual servers", "virtual_servers"],
  ])(
    "switch off: %s stays, disabled, and its link shows the line",
    (slug, label, feature) => {
      statusQuery.current = {
        data: { enabled: true, features: { ...ON, [feature]: false } },
      };
      mount(`/dashboard/gateway/mcp?tab=${slug}&x=1`);
      const tab = screen.getByRole("tab", { name: label });
      expect(tab).toHaveAttribute("aria-disabled", "true");
      expect(tab).toHaveAccessibleDescription(NOT_AVAILABLE);
      expect(screen.getByText(NOT_AVAILABLE)).toBeInTheDocument();
      expect(screen.queryByText(`${slug} tab`)).toBeNull();
      expect(screen.queryByText("overview tab")).toBeNull();
      expect(location.search).toBe(`?tab=${slug}&x=1`);
    },
  );

  it("healthy: tabs enabled and the link opens the tab", () => {
    statusQuery.current = { data: { enabled: true, features: ON } };
    mount("/dashboard/gateway/mcp?tab=credentials");
    expect(screen.getByRole("tab", { name: "Credentials" })).toBeEnabled();
    expect(screen.getByRole("tab", { name: "Virtual servers" })).toBeEnabled();
    expect(screen.getByText("credentials tab")).toBeInTheDocument();
    expect(screen.queryByText(NOT_AVAILABLE)).toBeNull();
  });
});

// An off tab says why where the person is. It stays in
// the bar with aria-disabled (not the disabled attribute), so hover and
// keyboard focus reach it and show the line in a tooltip, and its accessible
// description is the line, from an element that is always in the DOM.
describe("an off tab explains itself on hover, focus and to a screen reader", () => {
  it.each([
    ["Credentials", "credential_vault"],
    ["Virtual servers", "virtual_servers"],
  ])("%s off, seen from another tab", async (label, feature) => {
    statusQuery.current = {
      data: { enabled: true, features: { ...ON, [feature]: false } },
    };
    mount("/dashboard/gateway/mcp?tab=servers");
    expect(screen.getByText("servers tab")).toBeInTheDocument();
    const tab = screen.getByRole("tab", { name: label });
    expect(tab).toHaveAttribute("aria-disabled", "true");
    expect(tab).not.toHaveAttribute("disabled");
    expect(tab).toHaveAttribute("tabindex", "0");
    expect(tab).toHaveAccessibleDescription(NOT_AVAILABLE);
    expect(screen.queryByRole("tooltip")).toBeNull();

    fireEvent.mouseOver(tab);
    expect(
      within(await screen.findByRole("tooltip")).getByText(NOT_AVAILABLE),
    ).toBeInTheDocument();
    fireEvent.mouseLeave(tab);

    fireEvent.focus(tab);
    expect(
      within(await screen.findByRole("tooltip")).getByText(NOT_AVAILABLE),
    ).toBeInTheDocument();

    fireEvent.click(tab);
    expect(screen.getByText("servers tab")).toBeInTheDocument();
    expect(location.search).toBe("?tab=servers");
  });

  it("an enabled tab carries no off description", () => {
    statusQuery.current = { data: { enabled: true, features: ON } };
    mount("/dashboard/gateway/mcp?tab=servers");
    const tab = screen.getByRole("tab", { name: "Credentials" });
    expect(tab).not.toHaveAttribute("aria-disabled");
    expect(tab).not.toHaveAccessibleDescription(NOT_AVAILABLE);
    expect(screen.queryByText(NOT_AVAILABLE)).toBeNull();
  });
});
