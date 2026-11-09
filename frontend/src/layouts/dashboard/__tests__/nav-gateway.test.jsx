import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter, useLocation } from "react-router-dom";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { resetHostPathForTests } from "src/sections/omega/shell/products";

import NavVertical from "../nav-vertical";
import NavMini from "../nav-mini";
import { useSettingsOpen } from "../states";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: {
      id: "u1",
      role: "Owner",
      organization_role: "Owner",
      getStartedCompleted: true,
      ws_enabled: false,
    },
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isOSS: true,
    isCloud: false,
    isEE: false,
    isLoading: false,
  }),
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useCapabilities: () => ({
    data: { features: {} },
    isLoading: false,
    isError: false,
  }),
  useFeatureAllowed: () => ({
    allowed: false,
    isLoading: false,
    isError: false,
  }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
  useWorkspaceFromList: () => ({ workspace: null }),
}));
vi.mock("src/utils/Mixpanel", () => {
  const names = new Proxy({}, { get: (_, key) => String(key) });
  return { trackEvent: () => {}, Events: names, PropertyName: names };
});
vi.mock("src/layouts/dashboard/WorkspaceSwitcher/WorkspaceSwitcher", () => ({
  default: () => <div data-testid="workspace-switcher" />,
}));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeLayout: "vertical", onUpdate: () => {} }),
}));

// Central billing asks the gateway's credit ledger; these tests are about the
// rail, so the answer is "no" and no request is made.
vi.mock("src/sections/settings/BillingV2/useCentralBilling", () => ({
  useCentralBilling: () => ({ visible: false, isLoading: false }),
}));

// The rail waits for the managed-models status (nav-gateway-catalog.test.jsx
// covers it); here it has answered, for a deployment with no managed models.
vi.mock("src/sections/gateway/models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => ({ data: { available: false } }),
}));

function LocationProbe({ into }) {
  into.pathname = useLocation().pathname;
  return null;
}
LocationProbe.propTypes = { into: PropTypes.object.isRequired };

function renderNav(Nav, path, props = {}) {
  const location = { pathname: null };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ThemeProvider
        theme={createTheme({ palette: palette("light"), typography })}
      >
        <MemoryRouter initialEntries={[path]}>
          <Nav openNav={false} onCloseNav={() => {}} {...props} />
          <LocationProbe into={location} />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return location;
}

describe("sidebar on Future AGI pages", () => {
  beforeAll(() => {
    window.matchMedia = (query) => ({
      matches: /min-width/.test(query),
      media: query,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
    });
  });
  afterEach(() => resetHostPathForTests());

  it("puts the product switcher on top and no gateway group in the nav", () => {
    renderNav(NavVertical, "/dashboard/develop");
    expect(
      screen.getByRole("button", {
        name: "Switch product (current: Future AGI)",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Dataset")).toBeInTheDocument();
    expect(screen.queryByText("Gateway")).not.toBeInTheDocument();
    expect(screen.queryByTestId("gateway-rail")).not.toBeInTheDocument();
  });
});

describe("sidebar on AI Gateway pages", () => {
  afterEach(() => resetHostPathForTests());

  it("replaces the Future AGI nav with the gateway rail", () => {
    renderNav(NavVertical, "/dashboard/gateway/logs");
    expect(
      screen.getByRole("button", {
        name: "Switch product (current: AI Gateway)",
      }),
    ).toBeInTheDocument();
    const rail = screen.getByRole("navigation", { name: "AI Gateway" });
    expect(within(rail).getByText("Logs")).toBeInTheDocument();
    expect(within(rail).getByText("Auto routing")).toBeInTheDocument();
    expect(screen.queryByText("Dataset")).not.toBeInTheDocument();
    expect(screen.queryByText("Tracing")).not.toBeInTheDocument();
  });

  // The rail scrolls inside the area that shows a fade
  // while more items are below (sections/gateway/shell/GatewayRailScroll).
  it("scrolls the rail inside the overflow-cue area", () => {
    renderNav(NavVertical, "/dashboard/gateway/logs");
    const area = screen.getByTestId("gateway-rail-scroll");
    expect(
      within(area).getByRole("navigation", { name: "AI Gateway" }),
    ).toBeInTheDocument();
  });

  it("marks only the open page active", () => {
    renderNav(NavVertical, "/dashboard/gateway/monitoring/rules");
    const rail = screen.getByRole("navigation", { name: "AI Gateway" });
    const current = rail.querySelectorAll("[aria-current='page']");
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("Alerts");
  });

  it("has a visible way back to Future AGI", async () => {
    const location = renderNav(NavVertical, "/dashboard/gateway/logs");
    await userEvent.click(
      screen.getByRole("button", { name: "Back to Future AGI" }),
    );
    expect(location.pathname).toBe("/dashboard/develop");
  });

  it("shows the rail in the collapsed sidebar too", () => {
    renderNav(NavMini, "/dashboard/gateway/models/auto");
    const rail = screen.getByRole("navigation", { name: "AI Gateway" });
    const current = rail.querySelectorAll("[aria-current='page']");
    expect(current).toHaveLength(1);
    expect(screen.queryByTestId("workspace-switcher")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /current: AI Gateway/ }),
    ).toBeInTheDocument();
  });

  it("opens as a temporary drawer on a narrow screen", () => {
    renderNav(NavVertical, "/dashboard/gateway", {
      temporary: true,
      openNav: true,
    });
    const navigation = screen.getByRole("navigation", { name: "AI Gateway" });
    expect(navigation).toBeInTheDocument();
    expect(navigation.closest('.MuiDrawer-root[role="presentation"]')).not.toBeNull();
    expect(screen.getAllByRole("presentation").filter((element) =>
      within(element).queryByRole("navigation", { name: "AI Gateway" }),
    )).toHaveLength(1);
  });
});

// The Future AGI settings panel slides over the sidebar on /settings/ pages.
// An AI Gateway page is never one of them: an old
// /dashboard/gateway/settings/<tab> link (it redirects to ?tab=) and any gateway
// page keep the gateway rail, and a panel left open on Future AGI settings
// closes when the user opens the gateway.
describe("the Future AGI settings panel and AI Gateway pages", () => {
  afterEach(() => {
    resetHostPathForTests();
    useSettingsOpen.getState().setSettingOpen(false);
  });

  it.each([
    ["the expanded sidebar", NavVertical],
    ["the collapsed sidebar", NavMini],
  ])(
    "an old gateway settings link keeps the gateway rail in %s",
    (_label, Nav) => {
      renderNav(Nav, "/dashboard/gateway/settings/org-config");
      expect(useSettingsOpen.getState().settingOpen).toBe(false);
      expect(
        screen.getByRole("navigation", { name: "AI Gateway" }),
      ).toBeInTheDocument();
    },
  );

  it.each([
    ["the expanded sidebar", NavVertical],
    ["the collapsed sidebar", NavMini],
  ])(
    "a panel left open on Future AGI settings closes on a gateway page in %s",
    (_label, Nav) => {
      useSettingsOpen.getState().setSettingOpen(true);
      renderNav(Nav, "/dashboard/gateway/logs");
      expect(useSettingsOpen.getState().settingOpen).toBe(false);
    },
  );

  it("still opens on a Future AGI settings page", () => {
    renderNav(NavVertical, "/dashboard/settings/profile-settings");
    expect(useSettingsOpen.getState().settingOpen).toBe(true);
  });
});
