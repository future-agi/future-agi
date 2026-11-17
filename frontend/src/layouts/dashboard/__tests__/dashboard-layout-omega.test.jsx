import React, { Suspense } from "react";
import PropTypes from "prop-types";
import { MemoryRouter, Outlet, useLocation, useRoutes } from "react-router-dom";
import {
  configure,
  getConfig,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import {
  omegaDashboardRoutes,
  omegaStandaloneRoutes,
} from "src/routes/sections/omega";
import {
  ShellTestProviders,
  ensureWebStorage,
  installViewport,
} from "src/sections/omega/shell/__tests__/test-providers";
import { resetHostPathForTests } from "src/sections/omega/shell/products";

import DashboardLayout from "..";

vi.mock("src/auth/guard", () => ({
  AuthGuard: ({ children }) => children,
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
    useFeatureAllowed: () => ({ allowed: false }),
    // Every Omega product admitted (READINESS 3.13): this suite tests the layout.
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
vi.mock("src/sections/falcon-ai/FalconAISidebar", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/FalconAIFab", () => ({
  default: () => <div data-testid="falcon-ai-fab" />,
}));
// The Hub's real pages need the API; the layout only cares about their paths.
vi.mock("src/sections/omega/hub/routes", () => ({
  HUB_ROUTES: [
    { path: "hub", element: <h1 data-testid="hub-browse">Hub</h1> },
    { path: "hub/review", element: <h1 data-testid="hub-review">Review</h1> },
    {
      path: "hub/:publisher/:name/:tab?",
      element: <h1 data-testid="hub-entry">Entry</h1>,
    },
  ],
}));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({
  default: (selector) =>
    selector({ pendingNavigation: null, clearPendingNavigation: () => {} }),
}));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeLayout: "vertical", onUpdate: () => {} }),
}));

function App() {
  return useRoutes([
    {
      path: "/dashboard",
      element: (
        <DashboardLayout>
          <Suspense fallback={null}>
            <Outlet />
          </Suspense>
        </DashboardLayout>
      ),
      children: [
        { path: "develop", element: <div data-testid="host-develop" /> },
        { path: "observe", element: <div data-testid="host-observe" /> },
        ...omegaDashboardRoutes({ enabled: true }),
      ],
    },
    ...omegaStandaloneRoutes({ enabled: true }),
  ]);
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

// The layout's outer flex box: the parent of <main>.
function layoutBox() {
  return document.querySelector("main").parentElement;
}

describe("DashboardLayout with VITE_OMEGA_CONSOLE=1", () => {
  const originalMatchMedia = window.matchMedia;
  // The merged tabs' pages are large lazy chunks. Under a full parallel run the
  // first import of one took 10.1 s and failed a 10 s wait, and another missed
  // the default 1 s wait. Every page this file waits on is loaded here, on the
  // hook's 30 s budget, and this file's waits get 10 s instead of 1 s.
  const asyncUtilTimeout = getConfig().asyncUtilTimeout;
  beforeAll(async () => {
    configure({ asyncUtilTimeout: 10_000 });
    await Promise.all([
      import("src/sections/omega/studio/StudioPage"),
      import("src/sections/omega/deployments/DeploymentsListPage"),
      import("src/sections/omega/deployments/DeploymentNewPage"),
      import("src/sections/omega/sandboxes/SandboxesListPage"),
      import("src/sections/omega/sandboxes/SandboxDetailPage"),
      import("src/sections/omega/console/ConsoleFramePage"),
    ]);
  }, 30_000);
  afterAll(() => configure({ asyncUtilTimeout }));
  beforeEach(() => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    ensureWebStorage();
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    window.matchMedia = originalMatchMedia;
    resetHostPathForTests();
  });

  it("keeps the 1200 px minimum width on Future AGI routes", async () => {
    installViewport(1280);
    renderAt("/dashboard/develop");
    await screen.findByTestId("host-develop");
    expect(layoutBox()).toHaveStyle({ minWidth: "0" });
    expect(screen.queryByTestId("omega-mobile-bar")).toBeNull();
  });

  it.each([
    "/dashboard/studio",
    "/dashboard/deployments/new",
    "/dashboard/sandboxes/sbx_1/files",
  ])("drops the minimum width on %s", async (path) => {
    installViewport(1280);
    renderAt(path);
    // A merged tab's real page is a larger lazy chunk than the shell's stub
    // (warmed in beforeAll); rendering it can still outlast the default 1 s.
    await screen.findByRole("heading", { level: 1 }, { timeout: 20_000 });
    expect(layoutBox()).toHaveStyle({ minWidth: "0" });
    // Wide screens keep the permanent sidebar.
    expect(screen.queryByTestId("omega-mobile-bar")).toBeNull();
    const sidebar = document.querySelectorAll("#nav-section-vertical")[1];
    expect(
      within(sidebar).getByRole("link", { name: "Deployments" }),
    ).toBeVisible();
  });

  it("shows Falcon AI on Future AGI routes", async () => {
    installViewport(1280);
    renderAt("/dashboard/develop");
    await screen.findByTestId("host-develop");
    expect(screen.getByTestId("falcon-ai-fab")).toBeInTheDocument();
  });

  it.each([
    ["/dashboard/hub", "hub-browse"],
    ["/dashboard/hub/review", "hub-review"],
    ["/dashboard/hub/future-agi/first-agent/files", "hub-entry"],
  ])("hides Falcon AI and relaxes the width on %s", async (path, testId) => {
    installViewport(1280);
    renderAt(path);
    await screen.findByTestId(testId);
    expect(screen.queryByTestId("falcon-ai-fab")).toBeNull();
    expect(layoutBox()).toHaveStyle({ minWidth: "0" });
    const sidebar = document.querySelectorAll("#nav-section-vertical")[1];
    expect(within(sidebar).getByRole("link", { name: "Hub" })).toBeVisible();
  });

  it("keeps Future AGI routes unchanged at 390 px", async () => {
    installViewport(390);
    renderAt("/dashboard/develop");
    await screen.findByTestId("host-develop");
    expect(layoutBox()).toHaveStyle({ minWidth: "0" });
    expect(screen.queryByTestId("omega-mobile-bar")).toBeNull();
  });

  it("moves the sidebar into a drawer on narrow Agent infrastructure routes", async () => {
    installViewport(390);
    const location = renderAt("/dashboard/deployments");
    await screen.findByTestId("omega-tab-deployments");
    expect(screen.getByTestId("omega-mobile-bar")).toBeInTheDocument();
    // The sidebar is not on the page until the drawer opens.
    expect(screen.queryByRole("link", { name: "Sandboxes" })).toBeNull();

    await userEvent.click(
      screen.getByRole("button", { name: "Open navigation" }),
    );
    const drawer = (await screen.findByRole("link", { name: "Sandboxes" })).closest('[role="presentation"]');
    expect(drawer).not.toBeNull();
    expect(screen.getAllByRole("presentation").filter((element) =>
      within(element).queryByRole("link", { name: "Sandboxes" }),
    )).toHaveLength(1);
    // Nothing to collapse inside the drawer.
    expect(
      within(drawer).queryByRole("button", { name: "Collapse sidebar" }),
    ).toBeNull();
    await userEvent.click(
      within(drawer).getByRole("link", { name: "Sandboxes" }),
    );
    expect(location.pathname).toBe("/dashboard/sandboxes");
    await screen.findByTestId("omega-tab-sandboxes");
    // Navigating closes the drawer.
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "Sandboxes" })).toBeNull(),
    );
  });

  // Models (the AI Gateway catalog) is one Future AGI sidebar entry; the
  // Console's own shell shows no Future AGI nav, so it has no Models entry of
  // any name.
  it("lists Models once on Future AGI routes and none on the Console", async () => {
    installViewport(1280);
    renderAt("/dashboard/develop");
    await screen.findByTestId("host-develop");
    const sidebar = document.querySelectorAll("#nav-section-vertical")[1];
    const models = within(sidebar).getByRole("link", { name: "Models" });
    expect(models).toHaveAttribute("href", "/dashboard/gateway/models");
    expect(
      document.querySelectorAll('a[href="/dashboard/gateway/models"]'),
    ).toHaveLength(1);

    await userEvent.click(screen.getByTestId("omega-product-switcher"));
    await userEvent.click(
      await screen.findByRole("menuitem", { name: /Omega Console/ }),
    );
    await screen.findByTestId("omega-tab-console");
    expect(document.querySelector("#nav-section-vertical")).toBeNull();
    expect(
      document.querySelectorAll('a[href="/dashboard/gateway/models"]'),
    ).toHaveLength(0);
    expect(screen.queryByRole("link", { name: /models/i })).toBeNull();
  });

  it("returns from the Console to the Future AGI page the user left", async () => {
    installViewport(1280);
    const location = renderAt("/dashboard/observe");
    await screen.findByTestId("host-observe");

    await userEvent.click(screen.getByTestId("omega-product-switcher"));
    await userEvent.click(
      await screen.findByRole("menuitem", { name: /Omega Console/ }),
    );
    await screen.findByTestId("omega-tab-console");
    expect(location.pathname).toBe("/dashboard/console");

    await userEvent.click(screen.getByTestId("omega-product-switcher"));
    await userEvent.click(
      await screen.findByRole("menuitem", { name: /Future AGI/ }),
    );
    await screen.findByTestId("host-observe");
    expect(location.pathname).toBe("/dashboard/observe");
  });
});
