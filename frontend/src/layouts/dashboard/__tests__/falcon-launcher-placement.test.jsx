// Where the Falcon AI launcher sits (MA-N4 gap 4). On a phone-width screen the
// floating button covered the last line of Studio's step 2. On the Agent
// infrastructure routes below md (the routes that show the Omega mobile bar)
// the launcher is a button in that bar, out of the content flow; everywhere
// else it stays the floating button it was. What the launcher does is
// unchanged: it opens the sidebar (or the Falcon AI page when the feature is
// not allowed), hides while the sidebar is open, and Cmd/Ctrl+K toggles the
// sidebar exactly once per press.
import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter, Outlet, useLocation, useRoutes } from "react-router-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ShellTestProviders,
  ensureWebStorage,
  installViewport,
} from "src/sections/omega/shell/__tests__/test-providers";
import { resetHostPathForTests } from "src/sections/omega/shell/products";

import DashboardLayout from "..";

const falcon = vi.hoisted(() => ({
  allowed: true,
  state: {
    isSidebarOpen: false,
    toggleSidebar: () => {},
    pendingNavigation: null,
    clearPendingNavigation: () => {},
  },
}));

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
    isOSS: false,
    isCloud: true,
    isEE: false,
    isLoading: false,
  }),
}));
vi.mock("src/hooks/useCapabilities", async (original) => {
  const { capabilitiesAnswer } = await import(
    "src/sections/omega/shell/__tests__/rollout-answer"
  );
  return {
    .../** @type {typeof import("src/hooks/useCapabilities")} */ (
      await original()
    ),
    useFeatureLocked: () => ({
      locked: true,
      isLoading: false,
      isError: false,
    }),
    CAPABILITIES_QUERY_KEY: ["capabilities"],
    useFeatureAllowed: () => ({ allowed: falcon.allowed }),
    useCapabilities: () => ({
      data: capabilitiesAnswer(),
      isLoading: false,
      isError: false,
    }),
  };
});
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
vi.mock("src/layouts/dashboard/WorkspaceSwitcher/CreateWorkspaceModal", () => ({
  default: () => null,
}));
vi.mock("src/layouts/dashboard/NavGatewayPanel", () => ({
  default: () => null,
}));
vi.mock("src/components/two-factor-enforcement/TwoFactorBanner", () => ({
  default: () => null,
}));
// The sidebar opens a socket; this file tests where its launcher sits.
vi.mock("src/sections/falcon-ai/FalconAISidebar", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({
  default: (selector) => selector(falcon.state),
}));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeLayout: "vertical", onUpdate: () => {} }),
}));

// The layout reads only the path; stand-in pages keep the test on placement.
const page = (id) => <div data-testid={id}>page</div>;
function App() {
  return useRoutes([
    {
      path: "/dashboard",
      element: <DashboardLayoutOutlet />,
      children: [
        { path: "develop", element: page("host-develop") },
        { path: "falcon-ai", element: page("falcon-page") },
        { path: "studio", element: page("omega-tab-studio") },
        { path: "deployments", element: page("omega-tab-deployments") },
        { path: "deployments/new", element: page("omega-tab-deployment-new") },
        {
          path: "deployments/:id/:tab?",
          element: page("omega-tab-deployment"),
        },
        { path: "sandboxes", element: page("omega-tab-sandboxes") },
        { path: "sandboxes/:id/:tab?", element: page("omega-tab-sandbox") },
        { path: "settings/sandboxes", element: page("sandbox-settings") },
        { path: "hub", element: page("hub-browse") },
      ],
    },
  ]);
}

function DashboardLayoutOutlet() {
  return (
    <DashboardLayout>
      <Outlet />
    </DashboardLayout>
  );
}

function LocationProbe({ into }) {
  into.pathname = useLocation().pathname;
  return null;
}
LocationProbe.propTypes = { into: PropTypes.object.isRequired };

function renderAt(path) {
  const location = { pathname: null };
  render(
    <ShellTestProviders>
      <MemoryRouter initialEntries={[path]}>
        <App />
        <LocationProbe into={location} />
      </MemoryRouter>
    </ShellTestProviders>,
  );
  return location;
}

// The launcher's own name ("Falcon AI", or the floating button's tooltip name
// "Falcon AI (⌘K)"). The navigation's "Falcon AI" page entry is not it.
const LAUNCHER = /^(Open )?Falcon AI( \(⌘K\))?$/;
const launchers = () =>
  screen
    .queryAllByRole("button", { name: LAUNCHER })
    .filter((el) => !el.closest("#nav-section-vertical"));

const OMEGA_NARROW = [
  ["/dashboard/studio", "omega-tab-studio"],
  ["/dashboard/deployments", "omega-tab-deployments"],
  ["/dashboard/deployments/new", "omega-tab-deployment-new"],
  ["/dashboard/deployments/dep_1/overview", "omega-tab-deployment"],
  ["/dashboard/sandboxes", "omega-tab-sandboxes"],
  ["/dashboard/sandboxes/sbx_1/files", "omega-tab-sandbox"],
  ["/dashboard/settings/sandboxes", "sandbox-settings"],
];

describe("Falcon AI launcher placement", () => {
  const originalMatchMedia = window.matchMedia;
  let toggleSidebar;
  beforeEach(() => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    ensureWebStorage();
    toggleSidebar = vi.fn();
    falcon.allowed = true;
    falcon.state = {
      isSidebarOpen: false,
      toggleSidebar,
      pendingNavigation: null,
      clearPendingNavigation: () => {},
    };
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    window.matchMedia = originalMatchMedia;
    resetHostPathForTests();
  });

  it.each(OMEGA_NARROW)(
    "at 390 px on %s the launcher is a button in the mobile bar, not a floating button",
    async (path, testId) => {
      installViewport(390);
      renderAt(path);
      await screen.findByTestId(testId);
      const bar = screen.getByTestId("omega-mobile-bar");
      const docked = within(bar).getByRole("button", { name: LAUNCHER });
      expect(launchers()).toEqual([docked]);
      expect(document.querySelector(".MuiFab-root")).toBeNull();
      // The bar is sticky chrome above the page, never inside its content.
      expect(
        within(screen.getByTestId(testId)).queryByRole("button"),
      ).toBeNull();
      expect(docked).toHaveAttribute("data-placement", "bar");
    },
  );

  it.each([
    [390, "/dashboard/develop", "host-develop"],
    [1280, "/dashboard/develop", "host-develop"],
    [1280, "/dashboard/studio", "omega-tab-studio"],
    [1280, "/dashboard/deployments", "omega-tab-deployments"],
    [1280, "/dashboard/sandboxes/sbx_1/files", "omega-tab-sandbox"],
  ])(
    "at %i px on %s the launcher stays the floating button where it was",
    async (width, path, testId) => {
      installViewport(width);
      renderAt(path);
      await screen.findByTestId(testId);
      const [only, ...rest] = launchers();
      expect(rest).toEqual([]);
      expect(only).toHaveClass("MuiFab-root");
      expect(only).toHaveStyle({
        position: "fixed",
        bottom: "72px",
        right: "12px",
        width: "40px",
        height: "40px",
      });
      expect(screen.queryByTestId("omega-mobile-bar")).toBeNull();
    },
  );

  it.each([390, 1280])(
    "shows no launcher on the Hub at %i px",
    async (width) => {
      installViewport(width);
      renderAt("/dashboard/hub");
      await screen.findByTestId("hub-browse");
      expect(launchers()).toEqual([]);
      expect(document.querySelector(".MuiFab-root")).toBeNull();
    },
  );

  it("reaches the docked launcher by keyboard and opens the sidebar with Enter and Space", async () => {
    installViewport(390);
    renderAt("/dashboard/deployments");
    await screen.findByTestId("omega-tab-deployments");
    const docked = within(screen.getByTestId("omega-mobile-bar")).getByRole(
      "button",
      { name: LAUNCHER },
    );
    const user = userEvent.setup();
    for (let i = 0; i < 10 && document.activeElement !== docked; i += 1) {
      await user.tab();
    }
    expect(docked).toHaveFocus();
    expect(docked).toHaveAttribute("aria-keyshortcuts", "Meta+K Control+K");
    await user.keyboard("{Enter}");
    expect(toggleSidebar).toHaveBeenCalledTimes(1);
    await user.keyboard(" ");
    expect(toggleSidebar).toHaveBeenCalledTimes(2);
  });

  it("exposes neither a launcher nor a keyboard route when the feature is not allowed", async () => {
    falcon.allowed = false;
    installViewport(390);
    const location = renderAt("/dashboard/studio");
    await screen.findByTestId("omega-tab-studio");
    expect(
      within(screen.getByTestId("omega-mobile-bar")).queryByRole("button", {
        name: LAUNCHER,
      }),
    ).toBeNull();
    expect(launchers()).toEqual([]);
    expect(document.querySelector(".MuiFab-root")).toBeNull();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(location.pathname).toBe("/dashboard/studio");
    expect(screen.queryByTestId("falcon-page")).toBeNull();
    expect(toggleSidebar).not.toHaveBeenCalled();
  });

  it.each([
    [390, "/dashboard/sandboxes", "omega-tab-sandboxes"],
    [1280, "/dashboard/sandboxes", "omega-tab-sandboxes"],
    [390, "/dashboard/develop", "host-develop"],
  ])(
    "at %i px on %s Cmd+K and Ctrl+K toggle the sidebar exactly once per press",
    async (width, path, testId) => {
      installViewport(width);
      renderAt(path);
      await screen.findByTestId(testId);
      fireEvent.keyDown(window, { key: "k", metaKey: true });
      expect(toggleSidebar).toHaveBeenCalledTimes(1);
      fireEvent.keyDown(window, { key: "k", ctrlKey: true });
      expect(toggleSidebar).toHaveBeenCalledTimes(2);
    },
  );

  it.each([390, 1280])(
    "hides the launcher while the sidebar is open at %i px",
    async (width) => {
      falcon.state = { ...falcon.state, isSidebarOpen: true };
      installViewport(width);
      renderAt("/dashboard/studio");
      await screen.findByTestId("omega-tab-studio");
      expect(launchers()).toEqual([]);
    },
  );
});
