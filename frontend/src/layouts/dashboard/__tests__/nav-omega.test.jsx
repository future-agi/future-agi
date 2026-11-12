import React, { Suspense } from "react";
import PropTypes from "prop-types";
import { MemoryRouter, Outlet, useLocation, useRoutes } from "react-router-dom";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import {
  omegaDashboardRoutes,
  omegaStandaloneRoutes,
} from "src/routes/sections/omega";
import {
  ShellTestProviders,
  ensureWebStorage,
  installViewport,
} from "src/sections/omega/shell/__tests__/test-providers";

import NavVertical from "../nav-vertical";
import NavMini from "../nav-mini";
import NavHorizontal from "../nav-horizontal";

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

function App({ Nav }) {
  return useRoutes([
    {
      path: "/dashboard",
      element: (
        <div style={{ display: "flex" }}>
          <Nav openNav={false} onCloseNav={() => {}} />
          <Suspense fallback={null}>
            <Outlet />
          </Suspense>
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

function LocationProbe({ into }) {
  into.pathname = useLocation().pathname;
  return null;
}

LocationProbe.propTypes = { into: PropTypes.object.isRequired };

function renderShell(Nav, path) {
  const location = { pathname: null };
  render(
    <ShellTestProviders>
      <MemoryRouter initialEntries={[path]}>
        <App Nav={Nav} />
        <LocationProbe into={location} />
      </MemoryRouter>
    </ShellTestProviders>,
  );
  return location;
}

const MODELS_PATH = "/dashboard/gateway/models";

function sidebarGroups() {
  const nav = document.querySelectorAll("#nav-section-vertical")[1];
  return Array.from(nav.querySelectorAll(":scope > div")).map((group) => ({
    subheader: group.querySelector(".MuiListSubheader-root")?.textContent || null,
    items: Array.from(group.querySelectorAll("a")).map((a) => [
      a.textContent,
      a.getAttribute("href"),
    ]),
  }));
}

describe("Future AGI sidebar with VITE_OMEGA_CONSOLE=1", () => {
  const originalMatchMedia = window.matchMedia;
  beforeAll(() => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    ensureWebStorage();
    installViewport(1280);
  });
  afterAll(() => {
    vi.unstubAllEnvs();
    window.matchMedia = originalMatchMedia;
  });

  it("adds an 'Agent infrastructure' group right after Build", () => {
    renderShell(NavVertical, "/dashboard/develop");
    const groups = sidebarGroups();
    const names = groups.map((g) => g.subheader);
    expect(names).toContain("Build");
    // Group labels are sentence case since the design system pass (b9c482fe76).
    expect(names.indexOf("Agent infrastructure")).toBe(
      names.indexOf("Build") + 1,
    );
    expect(
      groups.find((g) => g.subheader === "Agent infrastructure").items,
    ).toEqual([
      ["Studio", "/dashboard/studio"],
      ["Deployments", "/dashboard/deployments"],
      ["Sandboxes", "/dashboard/sandboxes"],
      ["Hub", "/dashboard/hub"],
    ]);
  });

  // Models (the AI Gateway catalog) ends Build with the flag on too; the
  // Agent infrastructure group still follows Build.
  it("keeps Models at the end of Build, once", () => {
    renderShell(NavVertical, "/dashboard/develop");
    const groups = sidebarGroups();
    const build = groups.find((g) => g.subheader === "Build");
    expect(build.items.at(-1)).toEqual(["Models", MODELS_PATH]);
    expect(document.querySelectorAll(`a[href="${MODELS_PATH}"]`)).toHaveLength(
      1,
    );
  });

  // The Console's own shell has no Future AGI nav, so no Models entry under
  // any name: the switcher is the way to the AI Gateway there.
  it("shows no Future AGI nav and no Models entry on the Console", async () => {
    renderShell(NavVertical, "/dashboard/console");
    await screen.findByTestId("omega-console-bar");
    expect(document.querySelector("#nav-section-vertical")).toBeNull();
    expect(document.querySelectorAll(`a[href="${MODELS_PATH}"]`)).toHaveLength(
      0,
    );
    expect(screen.queryByRole("link", { name: /models/i })).toBeNull();
  });

  it("navigates to the real Deployments route when its entry is clicked", async () => {
    const location = renderShell(NavVertical, "/dashboard/develop");
    await userEvent.click(screen.getByRole("link", { name: "Deployments" }));
    expect(location.pathname).toBe("/dashboard/deployments");
    // The merged Deployments page is a larger lazy chunk than the shell's stub;
    // its first import can outlast the default 1 s wait.
    expect(
      await screen.findByTestId(
        "omega-tab-deployments",
        {},
        { timeout: 10_000 },
      ),
    ).toBeInTheDocument();
    // The entry is marked active once its route is open.
    expect(
      screen.getByRole("link", { name: "Deployments" }).firstChild,
    ).toHaveClass("active");
  });

  it("turns the top-left mark into a Future AGI | AI Gateway | Omega Console switcher", async () => {
    const location = renderShell(NavVertical, "/dashboard/develop");
    const brandRow = screen.getByTestId("omega-nav-brand-row");
    const trigger = within(brandRow).getByTestId("omega-product-switcher");
    expect(trigger).toHaveAccessibleName(
      "Switch product (current: Future AGI)",
    );
    // The collapse toggle stays beside the mark, as Logo draws it.
    expect(
      within(brandRow).getByRole("button", { name: "Collapse sidebar" }),
    ).toBeInTheDocument();

    await userEvent.click(trigger);
    const menu = await screen.findByRole("menu", { name: "Products" });
    const options = within(menu).getAllByRole("menuitem");
    // The AI Gateway is always listed (gateway UI v2, merged in round 7); the
    // Console follows the rollout.
    expect(options.map((o) => o.textContent)).toEqual([
      expect.stringContaining("Future AGI"),
      expect.stringContaining("AI Gateway"),
      expect.stringContaining("Omega Console"),
    ]);
    expect(options[0]).toHaveClass("Mui-selected");

    await userEvent.click(options[2]);
    await waitFor(() => expect(location.pathname).toBe("/dashboard/console"));
    expect(
      await screen.findByTestId("omega-console-layout"),
    ).toBeInTheDocument();
    // The Console is full width: no Future AGI sidebar on the page.
    expect(document.querySelector("#nav-section-vertical")).toBeNull();
  });

  it("shows the switcher as current product 'Omega Console' on the Console", async () => {
    const location = renderShell(NavVertical, "/dashboard/console");
    const bar = await screen.findByTestId("omega-console-bar");
    const trigger = within(bar).getByTestId("omega-product-switcher");
    expect(trigger).toHaveAccessibleName(
      "Switch product (current: Omega Console)",
    );
    await userEvent.click(trigger);
    const menu = await screen.findByRole("menu", { name: "Products" });
    await userEvent.click(within(menu).getByText("Future AGI"));
    await waitFor(() => expect(location.pathname).toBe("/dashboard/develop"));
  });

  it("puts a collapsed switcher and the three entries in the mini nav", async () => {
    const location = renderShell(NavMini, "/dashboard/develop");
    expect(screen.getByTestId("omega-product-switcher")).toHaveAccessibleName(
      "Switch product (current: Future AGI)",
    );
    const nav = document.querySelectorAll("#nav-section-mini")[1];
    const hrefs = Array.from(nav.querySelectorAll("a")).map((a) =>
      a.getAttribute("href"),
    );
    const build = hrefs.indexOf("/dashboard/develop");
    expect(hrefs).toContain("/dashboard/studio");
    expect(hrefs.indexOf("/dashboard/studio")).toBeGreaterThan(build);
    expect(hrefs.indexOf("/dashboard/studio")).toBeLessThan(
      hrefs.indexOf("/dashboard/observe"),
    );
    await userEvent.click(nav.querySelector('a[href="/dashboard/sandboxes"]'));
    expect(location.pathname).toBe("/dashboard/sandboxes");
    // The Sandboxes page is a large lazy chunk; under a full parallel run its
    // first import can pass the default 1 s (as in omega-routes.test.jsx).
    expect(
      await screen.findByTestId("omega-tab-sandboxes", {}, { timeout: 10_000 }),
    ).toBeInTheDocument();
  });

  it("lists the three entries in the horizontal nav", async () => {
    const location = renderShell(NavHorizontal, "/dashboard/develop");
    const nav = document.querySelector("#nav-section-horizontal");
    const hrefs = Array.from(nav.querySelectorAll("a")).map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs).toEqual(
      expect.arrayContaining([
        "/dashboard/studio",
        "/dashboard/deployments",
        "/dashboard/sandboxes",
      ]),
    );
    await userEvent.click(nav.querySelector('a[href="/dashboard/studio"]'));
    expect(location.pathname).toBe("/dashboard/studio");
    // A lazy tab chunk; under a full parallel run its first import can pass 1 s.
    expect(
      await screen.findByTestId("omega-tab-studio", {}, { timeout: 10_000 }),
    ).toBeInTheDocument();
  });
});
