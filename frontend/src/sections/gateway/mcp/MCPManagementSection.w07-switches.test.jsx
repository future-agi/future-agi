import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
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
const matrix = Array.from({ length: 8 }, (_, bits) => [
  !!(bits & 4),
  !!(bits & 2),
  !!(bits & 1),
]);
describe("W07 independent NEW-only switches", () => {
  // A switch that is off keeps its tab,
  // disabled, instead of removing it.
  it.each(matrix)(
    "catalog=%s vault=%s virtual=%s preserves legacy tabs",
    (catalog, vault, virtual) => {
      statusQuery.current = {
        data: {
          enabled: true,
          features: {
            catalog,
            credential_vault: vault,
            virtual_servers: virtual,
          },
        },
      };
      mount("/dashboard/gateway/mcp?tab=servers");
      const offTab = (name) =>
        screen.getByRole("tab", { name }).getAttribute("aria-disabled") ===
        "true";
      expect(offTab("Credentials")).toBe(!vault);
      expect(offTab("Virtual servers")).toBe(!virtual);
      expect(screen.getByText("servers tab")).toBeInTheDocument();
      expect(
        screen.getByRole("tab", { name: "Try a tool" }),
      ).toBeInTheDocument();
    },
  );
  it.each(["credentials", "virtual"])(
    "%s OFF keeps query and path deep links on the tab, unmounted",
    (slug) => {
      statusQuery.current = {
        data: {
          enabled: true,
          features: { credential_vault: false, virtual_servers: false },
        },
      };
      const view = mount(`/dashboard/gateway/mcp/${slug}?x=1`);
      expect(screen.queryByText(`${slug} tab`)).toBeNull();
      expect(screen.queryByText("overview tab")).toBeNull();
      expect(
        screen.getByText("Not available in this deployment"),
      ).toBeInTheDocument();
      expect(location.search).toBe("?x=1");
      view.unmount();
      mount(`/dashboard/gateway/mcp?tab=${slug}&x=1`);
      expect(screen.queryByText(`${slug} tab`)).toBeNull();
      expect(location.search).toBe(`?tab=${slug}&x=1`);
    },
  );
  it.each([
    ["credentials", "credential_vault"],
    ["virtual", "virtual_servers"],
  ])("%s ON mounts and stays through a status error", (slug, feature) => {
    statusQuery.current = {
      data: { enabled: true, features: { [feature]: true } },
    };
    const view = mount(`/dashboard/gateway/mcp?tab=${slug}`);
    expect(screen.getByText(`${slug} tab`)).toBeInTheDocument();
    statusQuery.current.isError = true;
    view.rerender(view.tree());
    expect(screen.getByText(`${slug} tab`)).toBeInTheDocument();
  });
  it("legacy playground mounts with every new capability OFF", () => {
    statusQuery.current = { data: { enabled: true, features: {} } };
    mount("/dashboard/gateway/mcp?tab=playground");
    expect(screen.getByText("playground tab")).toBeInTheDocument();
  });
});

describe("c26 retained-OFF allowlist with W07 composed", () => {
  it.each(matrix)(
    "catalog=%s vault=%s virtual=%s cannot widen the seven saved tabs",
    (catalog, vault, virtual) => {
      statusQuery.current = {
        data: {
          enabled: false,
          reason: "gateway_mcp_disabled",
          features: {
            catalog,
            credential_vault: vault,
            virtual_servers: virtual,
            tool_call_explorer: true,
            tool_call_download: true,
            client_installs: true,
          },
        },
      };
      const view = mount("/dashboard/gateway/mcp?tab=overview");
      const expected = [
        "Overview",
        "Tools",
        "Servers",
        "Resources",
        "Prompts",
        "Guardrails",
        "Playground",
      ];
      expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(
        expected,
      );
      expect(screen.getByText("saved overview tab")).toBeInTheDocument();
      statusQuery.current.isError = true;
      view.rerender(view.tree());
      expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(
        expected,
      );
    },
  );
});
