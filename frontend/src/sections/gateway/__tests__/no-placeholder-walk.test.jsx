import React, { Suspense } from "react";
import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Outlet, useLocation, useRoutes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { paths } from "src/routes/paths";
import { dashboardRoutes } from "src/routes/sections/dashboard";
import { API_CONTRACT_EXCEPTIONS } from "src/api/contracts/api-contract-exceptions";

import { gatewayRailGroups } from "../shell/gateway-rail";
import { HIDDEN_GATEWAY_PAGES, HIDDEN_MCP_TABS } from "../shell/page-status";

// Walks the whole AI Gateway console the way a user would, on cloud and on a
// self-hosted deployment: every rail item, every tab on that page and every
// tab inside a tab (Gateway settings > Org config), then Settings > Billing
// and Usage Summary. Nothing reachable may say "coming soon", "in this build"
// or "preview"; every rail item must land on itself (a rail item that
// redirects is a dead link); every hidden page must redirect; and no page may
// send a request this API does not serve (the uncontracted endpoints in
// api-contract-exceptions.js, gateway credits among them).

const PLACEHOLDER = /coming soon|in this build|preview/i;

const state = vi.hoisted(() => ({ mode: "cloud", requests: [] }));

// GatewayProvider loads the org's gateway list and useDeploymentMode the
// deployment mode; every other request fails, so each page renders its own
// error or empty state, never live data. Every request is recorded.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const answer = (method) =>
    vi.fn(async (url) => {
      state.requests.push({ method, url: String(url) });
      if (method === "GET" && url === actual.endpoints.gateway.list) {
        return { data: { result: [{ id: "gw-1", name: "Default" }] } };
      }
      if (
        method === "GET" &&
        url === actual.endpoints.settings.v2.deploymentInfo
      ) {
        return { data: { result: { mode: state.mode } } };
      }
      throw new Error(`unmocked ${method} ${url}`);
    });
  return {
    ...actual,
    default: {
      ...actual.default,
      get: answer("GET"),
      post: answer("POST"),
      put: answer("PUT"),
      patch: answer("PATCH"),
      delete: answer("DELETE"),
    },
  };
});
// An organization Owner, so every admin-only control is on the page too.
vi.mock("src/auth/hooks", () => {
  const user = {
    id: "u1",
    email: "owner@example.com",
    name: "Owner",
    role: "Owner",
    organization_role: "Owner",
    ws_enabled: false,
    getStartedCompleted: true,
  };
  return {
    useAuthContext: () => ({ user, role: null, authenticated: true }),
  };
});
vi.mock("src/contexts/OrganizationContext", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useOrganization: () => ({
      currentOrganizationId: "org-1",
      currentOrganizationRole: "Owner",
      orgLevel: 15,
      isReady: true,
    }),
  };
});
vi.mock("ag-grid-react", async () => {
  const { forwardRef } = await import("react");
  return {
    AgGridReact: forwardRef(function MockGrid() {
      return <div data-testid="ag-grid" />;
    }),
  };
});
vi.mock("react-apexcharts", () => ({ default: () => null }));

const theme = createTheme({ palette: palette("light"), typography });
const gw = paths.dashboard.gateway;
const PROFILE_SETTINGS = "/dashboard/settings/profile-settings";

const MODES = [
  ["cloud", true],
  ["self-hosted", false],
];

// The paths this API does not serve, as patterns over a request URL.
const UNSERVED = Object.entries(API_CONTRACT_EXCEPTIONS)
  .filter(([, meta]) => meta.served !== true)
  .map(
    ([template]) =>
      new RegExp(`^${template.replace(/\{[^}]+\}/g, "[^/]+")}(\\?.*)?$`),
  );
const unservedRequests = () =>
  state.requests
    .map(({ method, url }) => `${method} ${url}`)
    .filter((request) =>
      UNSERVED.some((pattern) => pattern.test(request.split(" ")[1])),
    );

function dashboardChildren(isCloud) {
  const [dashboardRoute] = dashboardRoutes(null, null, { isCloud });
  return dashboardRoute.children;
}

// The gateway route as the app has it; the settings pages without their
// layout (Billing and Usage Summary only: anything else they send to lands
// outside the walk).
function walkRoutes(isCloud) {
  const children = dashboardChildren(isCloud);
  const gateway = children.find((child) => child.path === "gateway");
  const settings = children.find((child) => child.path === "settings");
  return [
    {
      path: "/dashboard",
      element: (
        <Suspense fallback={null}>
          <Outlet />
        </Suspense>
      ),
      children: [
        gateway,
        {
          path: "settings",
          element: <Outlet />,
          children: settings.children.filter((child) =>
            ["billing", "usage-summary"].includes(child.path),
          ),
        },
        { path: "*", element: <div data-testid="outside-gateway" /> },
      ],
    },
  ];
}

function renderUrl(url, isCloud) {
  const location = { pathname: null, search: null };
  const Probe = () => {
    ({ pathname: location.pathname, search: location.search } = useLocation());
    return null;
  };
  const routes = walkRoutes(isCloud);
  const Routes = () => useRoutes(routes);
  render(
    <HelmetProvider>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ThemeProvider theme={theme}>
          <MemoryRouter initialEntries={[url]}>
            <Probe />
            <Routes />
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>,
  );
  return location;
}

// The gateway guard resolves, the lazy page renders, then its failed requests
// settle into error or empty states. A placeholder is static text, so it is
// on screen as soon as the page is. (waitFor polls inside act; a bare timer
// inside act does not let React Query's answer through.)
async function settle() {
  await waitFor(
    () => {
      expect(screen.queryByTestId("gateway-guard-loading")).toBeNull();
      // Some pages show only their own skeleton while a request fails.
      expect(document.body.innerHTML.length).toBeGreaterThan(100);
    },
    { timeout: 15000 },
  );
  let ticks = 0;
  await waitFor(() => {
    ticks += 1;
    expect(ticks).toBeGreaterThan(3);
  });
}

const tabs = () => screen.queryAllByRole("tab");

function expectNoPlaceholder(where) {
  expect(document.body.textContent, where).not.toMatch(PLACEHOLDER);
}

// Opens `path`, clicks the tabs at `indexes` in turn (each index read after
// the previous click), and checks the page. Returns the tab labels showing.
async function openTabs(path, indexes, isCloud) {
  cleanup();
  renderUrl(path, isCloud);
  await settle();
  for (const index of indexes) {
    fireEvent.click(tabs()[index]);
    await settle();
  }
  return tabs().map((tab) => tab.textContent);
}

const railItems = gatewayRailGroups({ integrations: true }).flatMap(
  (group) => group.items,
);
const gatewayItems = railItems.filter((item) => item.path.startsWith(gw.root));

describe.each(MODES)("walking the AI Gateway console on %s", (_m, isCloud) => {
  const originalMatchMedia = window.matchMedia;
  beforeAll(() => {
    window.matchMedia = (query) => ({
      matches: false,
      media: query,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
    });
  });
  afterAll(() => {
    window.matchMedia = originalMatchMedia;
  });
  beforeEach(() => {
    state.mode = isCloud ? "cloud" : "oss";
    state.requests.length = 0;
  });

  it("covers the rail", () => {
    expect(gatewayItems.length).toBeGreaterThanOrEqual(15);
  });

  it.each(gatewayItems.map((item) => [item.title, item.path]))(
    "%s (%s), each of its tabs and each tab inside them show no placeholder",
    async (_title, path) => {
      const location = renderUrl(path, isCloud);
      await settle();
      expect(location.pathname).toBe(path);
      expectNoPlaceholder(path);

      const top = tabs().map((tab) => tab.textContent);
      // Each tab from a fresh page, so a tab that replaces the whole page
      // (a skeleton while its request fails) cannot hide the next one.
      for (const [index, label] of top.entries()) {
        const showing = await openTabs(path, [index], isCloud);
        expectNoPlaceholder(`${path} tab "${label}"`);
        // Tabs inside this tab (Org config's sections) come after the page's
        // own tab bar.
        const inner = showing.slice(top.length);
        for (const [innerIndex, innerLabel] of inner.entries()) {
          await openTabs(path, [index, top.length + innerIndex], isCloud);
          expectNoPlaceholder(`${path} tab "${label}" > "${innerLabel}"`);
        }
      }
      expect(unservedRequests(), path).toEqual([]);
      cleanup();
    },
    180000,
  );

  // Settings > Billing and Usage Summary show their page on cloud, and where
  // there is nothing to bill (off cloud while gateway credits are hidden)
  // send the user to their profile; neither may show a placeholder. The page
  // may render a moment before its access answer sends the user away, so the
  // outcome expected for this mode is awaited, not the first render.
  it.each([
    ["Billing", paths.dashboard.settings.billing],
    ["Usage Summary", "/dashboard/settings/usage-summary"],
  ])(
    "Settings > %s shows no placeholder and sends no unserved request",
    async (_title, path) => {
      const location = renderUrl(path, isCloud);
      // This fixture refuses the served summary too, so self-hosted has no
      // authorized ledger to display even though the global hide is lifted.
      const nothingToBill = !isCloud;
      if (nothingToBill) {
        await waitFor(() => expect(location.pathname).toBe(PROFILE_SETTINGS), {
          timeout: 15000,
        });
      } else {
        await settle();
        expect(location.pathname).toBe(path);
      }
      expectNoPlaceholder(path);
      const labels = tabs().map((tab) => tab.textContent);
      for (const [index, label] of labels.entries()) {
        await openTabs(path, [index], isCloud);
        expectNoPlaceholder(`${path} tab "${label}"`);
      }
      expect(unservedRequests(), path).toEqual([]);
      cleanup();
    },
    60000,
  );
});

describe.each(MODES)("hidden gateway pages on %s", (_m, isCloud) => {
  beforeEach(() => {
    state.mode = isCloud ? "cloud" : "oss";
    state.requests.length = 0;
  });

  const hiddenUrls = [
    // No managed-models status is available in this walk.
    [gw.capabilities, gw.overview],
    [gw.policies.root, HIDDEN_GATEWAY_PAGES.policies.redirectTo],
    ...Object.keys(gw.policies)
      .filter((tab) => tab !== "root")
      .map((tab) => [
        gw.policies[tab],
        HIDDEN_GATEWAY_PAGES.policies.redirectTo,
      ]),
  ];

  it("lists no hidden page in the rail", () => {
    const railPaths = railItems.map((item) => item.path);
    for (const [url] of hiddenUrls) {
      expect(railPaths).not.toContain(url);
    }
  });

  it.each(hiddenUrls)(
    "%s redirects to %s",
    async (url, target) => {
      const location = renderUrl(`${url}?from=bookmark#top`, isCloud);
      await waitFor(() => expect(location.pathname).toBe(target));
      expect(location.search).toBe("?from=bookmark");
      await settle();
      expectNoPlaceholder(url);
      expect(unservedRequests(), url).toEqual([]);
      cleanup();
    },
    30000,
  );

  // The MCP page is shown; a link to one of its tabs with no backend opens
  // the MCP overview, and the URL drops the tab.
  it.each(Object.keys(HIDDEN_MCP_TABS))(
    "a link to the hidden MCP tab %s opens the MCP overview",
    async (slug) => {
      const location = renderUrl(`${gw.mcp}?tab=${slug}`, isCloud);
      await waitFor(() => expect(location.search).toBe(""));
      expect(location.pathname).toBe(gw.mcp);
      await settle();
      expectNoPlaceholder(`${gw.mcp}?tab=${slug}`);
      expect(unservedRequests(), slug).toEqual([]);
      cleanup();
    },
    30000,
  );
});
