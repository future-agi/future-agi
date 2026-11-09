import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter, Outlet, useLocation, useRoutes } from "react-router-dom";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ShellTestProviders,
  ensureWebStorage,
} from "src/sections/omega/shell/__tests__/test-providers";
import {
  rememberHostPath,
  resetHostPathForTests,
} from "src/sections/omega/shell/products";

import { dashboardRoutes } from "../dashboard";
import { omegaDashboardRoutes, omegaStandaloneRoutes } from "../omega";

// The console route sits behind the real AuthGuard in the app; the guard's
// own behaviour is covered elsewhere, so it is a pass-through here.
vi.mock("src/auth/guard", () => ({
  AuthGuard: ({ children }) => children,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "u1" }, logout: () => {} }),
}));

// MemoryRouter + useRoutes rather than a data router: the data router builds
// fetch Requests, which jsdom's AbortSignal cannot satisfy under Node 22+.
function RouteTable() {
  return useRoutes([
    {
      path: "/dashboard",
      element: (
        <div data-testid="host-layout">
          <Outlet />
        </div>
      ),
      children: [
        { path: "develop", element: <div data-testid="host-develop" /> },
        ...omegaDashboardRoutes({ enabled: true }),
      ],
    },
    ...omegaStandaloneRoutes({ enabled: true }),
  ]);
}

function LocationProbe({ onChange }) {
  const location = useLocation();
  onChange(location.pathname);
  return null;
}

LocationProbe.propTypes = { onChange: PropTypes.func.isRequired };

function renderAt(path) {
  const current = { pathname: null };
  render(
    <ShellTestProviders>
      <MemoryRouter initialEntries={[path]}>
        <RouteTable />
        <LocationProbe
          onChange={(pathname) => {
            current.pathname = pathname;
          }}
        />
      </MemoryRouter>
    </ShellTestProviders>,
  );
  return current;
}

const TAB_ROUTES = [
  ["/dashboard/studio", "omega-tab-studio", "Studio"],
  ["/dashboard/deployments", "omega-tab-deployments", "Deployments"],
  ["/dashboard/deployments/new", "omega-tab-deployment-new", "New deployment"],
  [
    "/dashboard/deployments/dep_42",
    "omega-tab-deployment-detail",
    "Deployment · dep_42",
  ],
  [
    "/dashboard/deployments/dep_42/logs",
    "omega-tab-deployment-detail",
    "Deployment · dep_42",
  ],
  ["/dashboard/sandboxes", "omega-tab-sandboxes", "Sandboxes"],
  ["/dashboard/sandboxes/sbx_7", "omega-tab-sandbox-detail", "Sandbox · sbx_7"],
  [
    "/dashboard/sandboxes/sbx_7/files",
    "omega-tab-sandbox-detail",
    "Sandbox · sbx_7",
  ],
];

// Tab entries already replaced by their lane's real page, with the heading that
// page shows before its data arrives (a sandbox's name needs the API, so the
// detail page is titled "Sandbox" until then). The Deployments pages keep the
// stubs' headings.
const MERGED_TABS = new Map([
  ["omega-tab-studio", "Studio"],
  ["omega-tab-sandboxes", "Sandboxes"],
  ["omega-tab-sandbox-detail", "Sandbox"],
  ["omega-tab-deployments", "Deployments"],
  ["omega-tab-deployment-new", "New deployment"],
  ["omega-tab-deployment-detail", "Deployment · dep_42"],
]);

describe("Omega routes", () => {
  beforeEach(() => ensureWebStorage());
  afterEach(() => {
    vi.unstubAllEnvs();
    resetHostPathForTests();
  });

  it.each(TAB_ROUTES)(
    "%s renders its tab entry inside the dashboard layout",
    async (path, testId, title) => {
      renderAt(path);
      // A merged tab's page is a larger lazy chunk than a stub; allow its first import.
      const page = await screen.findByTestId(testId, {}, { timeout: 10_000 });
      expect(screen.getByTestId("host-layout")).toContainElement(page);
      expect(within(page).getByRole("heading", { level: 1 })).toHaveTextContent(
        MERGED_TABS.get(testId) ?? title,
      );
      // Every stub is honest: an empty state, never sample data. A tab whose
      // lane has merged renders its real page instead (Studio's connect screen).
      if (!MERGED_TABS.has(testId)) {
        expect(within(page).getByRole("status")).toBeInTheDocument();
      }
    },
  );

  it("renders the Console full width, outside the dashboard layout", async () => {
    renderAt("/dashboard/console/agents/a1");
    const page = await screen.findByTestId("omega-tab-console");
    expect(screen.getByTestId("omega-console-layout")).toContainElement(page);
    expect(screen.queryByTestId("host-layout")).not.toBeInTheDocument();
    // While no frame is shown, the slim bar keeps a way back to Future AGI.
    expect(screen.getByTestId("omega-console-bar")).toBeInTheDocument();
  });

  it("keeps the host's 2FA enforcement overlay on the Console", async () => {
    renderAt("/dashboard/console");
    await screen.findByTestId("omega-tab-console");
    act(() => {
      window.dispatchEvent(new Event("2fa-enforcement-block"));
    });
    expect(
      await screen.findByText("Two-factor authentication required"),
    ).toBeInTheDocument();
  });

  it.each(["/dashboard/squad", "/dashboard/squad/agents/a1"])(
    "redirects %s to /dashboard/console",
    async (path) => {
      const location = renderAt(path);
      await screen.findByTestId("omega-tab-console");
      expect(location.pathname).toBe("/dashboard/console");
    },
  );

  it("the Console bar's switcher returns to the last Future AGI page", async () => {
    // The shell's placeholder had a "Back to Future AGI" button; the framed
    // Console (lane console-frame) leaves the way back to the slim bar's
    // product switcher while no frame is shown, and to the Console's own
    // "Future AGI" entry once it is.
    rememberHostPath("/dashboard/develop");
    const location = renderAt("/dashboard/console");
    const bar = await screen.findByTestId("omega-console-bar");
    await userEvent.click(
      within(bar).getByRole("button", { name: /Switch product/ }),
    );
    await userEvent.click(
      await screen.findByTestId("omega-product-option-future-agi"),
    );
    await waitFor(() => expect(location.pathname).toBe("/dashboard/develop"));
    expect(screen.getByTestId("host-develop")).toBeInTheDocument();
  });

  it("registers the Omega Hub routes under /dashboard (SPEC §7.1)", () => {
    const childPaths = omegaDashboardRoutes({ enabled: true }).map(
      (route) => route.path,
    );
    for (const path of [
      "hub",
      "hub/:publisher/:name/:tab?",
      "hub/publishers",
      "hub/publishers/:handle",
      "hub/publish",
      "hub/submissions",
      "hub/submissions/:id",
      "hub/settings",
      "hub/review",
      "hub/review/:id",
      "hub/review/reports",
      "hub/review/picks",
      "hub/review/publishers",
      "hub/review/blocklist",
      "hub/review/audit",
    ]) {
      expect(childPaths).toContain(path);
    }
  });

  it("registers nothing when VITE_OMEGA_CONSOLE is unset", () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "");
    expect(omegaDashboardRoutes()).toEqual([]);
    expect(omegaStandaloneRoutes()).toEqual([]);
    const [dashboard] = dashboardRoutes(null, null, { isCloud: true });
    const childPaths = dashboard.children.map((child) => child.path);
    // "home": where sign-in lands with the switch on (DEMO-FIX-2 item 8).
    for (const path of [
      "home",
      "studio",
      "deployments",
      "sandboxes",
      "hub",
      "squad/*",
    ]) {
      expect(childPaths).not.toContain(path);
    }
  });

  it("adds the Omega children to the dashboard route when the flag is on", () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    const [dashboard] = dashboardRoutes(null, null, { isCloud: true });
    const childPaths = dashboard.children.map((child) => child.path);
    // "home": where sign-in lands with the switch on (DEMO-FIX-2 item 8).
    for (const path of [
      "home",
      "studio",
      "deployments",
      "sandboxes",
      "hub",
      "squad/*",
    ]) {
      expect(childPaths).toContain(path);
    }
    expect(omegaStandaloneRoutes().map((route) => route.path)).toEqual([
      "/dashboard/console/*",
    ]);
  });
});
