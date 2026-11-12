import React, { Suspense, forwardRef } from "react";
import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, useLocation, useRoutes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { dashboardRoutes } from "src/routes/sections/dashboard";

// The Models URLs through the app's real dashboard routes and gateway guard:
// the catalog, auto routing, the old tab URL, and the model page deep links
// futureagi.com/models depends on.
const availability = vi.hoisted(() => ({ offered: true }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const { DEMO_CATALOG, DEMO_PROVIDERS } = await import(
    "../test-fixtures/demoCatalog"
  );
  const MANAGED_ON = {
    available: true,
    enabled: true,
    balance_usd: 34.999336,
    markup_percent: 5,
  };
  const gw = actual.endpoints.gateway;
  const answers = {
    [gw.list]: {
      result: [
        {
          id: "default",
          name: "Default",
          base_url: "http://localhost:28490/v1",
        },
      ],
    },
    [gw.managedModels]: { status: true, result: MANAGED_ON },
    [gw.managedModelsCatalog]: { status: true, result: DEMO_CATALOG },
    [gw.providers("default")]: { status: true, result: DEMO_PROVIDERS },
    [gw.analyticsModels]: { status: true, result: { models: [] } },
    [gw.config("default")]: { status: true, result: { routing: {} } },
    [gw.requestLogs]: { count: 0, results: [] },
  };
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(async (url) => {
        if (url === gw.managedModels && !availability.offered) {
          return {
            data: {
              status: true,
              result: { available: false, enabled: false },
            },
          };
        }
        if (url in answers) return { data: answers[url] };
        throw new Error(`unmocked GET ${url}`);
      }),
      post: vi.fn(async (url) => {
        throw new Error(`unmocked POST ${url}`);
      }),
    },
  };
});
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid() {
    return <div data-testid="ag-grid" />;
  }),
}));
vi.mock("react-apexcharts", () => ({ default: () => null }));

function renderUrl(url) {
  const location = { pathname: null, search: null };
  const Probe = () => {
    ({ pathname: location.pathname, search: location.search } = useLocation());
    return null;
  };
  const [dashboard] = dashboardRoutes(null, null, { isCloud: true });
  const gateway = dashboard.children.find((c) => c.path === "gateway");
  const Routes = () =>
    useRoutes([
      {
        path: "/dashboard",
        element: (
          <Suspense fallback={null}>
            <Outlet />
          </Suspense>
        ),
        children: [
          gateway,
          { path: "*", element: <div data-testid="outside" /> },
        ],
      },
    ]);
  render(
    <HelmetProvider>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ThemeProvider
          theme={createTheme({ palette: palette("light"), typography })}
        >
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

const heading = (name) =>
  screen.findByRole("heading", { level: 1, name }, { timeout: 15000 });

describe("Models URLs", () => {
  const originalMatchMedia = window.matchMedia;
  beforeAll(() => {
    window.matchMedia = (query) => ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
    });
  });
  afterAll(() => {
    window.matchMedia = originalMatchMedia;
  });

  beforeEach(() => {
    availability.offered = true;
  });

  it.each(["/dashboard/gateway/models", "/dashboard/gateway/models/catalog"])(
    "opens Your models at %s when managed models are unavailable",
    async (url) => {
      availability.offered = false;
      const location = renderUrl(`${url}?q=1`);
      expect(await heading("Your models")).toBeInTheDocument();
      expect(location.pathname).toBe("/dashboard/gateway/models");
      expect(location.search).toBe("?q=1");
      expect(
        screen.getByRole("link", { name: "Auto routing" }),
      ).toHaveAttribute("href", "/dashboard/gateway/models/auto");
    },
  );

  it("opens the catalog", async () => {
    renderUrl("/dashboard/gateway/models");
    expect(await heading("Models")).toBeInTheDocument();
  });

  it("switches between the managed catalog and Your models through visible links", async () => {
    const location = renderUrl("/dashboard/gateway/models");
    await heading("Models");
    await userEvent.click(screen.getByRole("link", { name: "Your models" }));
    expect(await heading("Your models")).toBeInTheDocument();
    expect(location.pathname).toBe("/dashboard/gateway/models/your");
    await userEvent.click(screen.getByRole("link", { name: "Catalog" }));
    expect(await heading("Models")).toBeInTheDocument();
    expect(location.pathname).toBe("/dashboard/gateway/models");
  });

  it("opens auto routing as its own page", async () => {
    renderUrl("/dashboard/gateway/models/auto");
    expect(await heading("Auto routing")).toBeInTheDocument();
  });

  it("sends the old catalog tab URL to the catalog", async () => {
    const location = renderUrl("/dashboard/gateway/models/catalog?q=1");
    expect(await heading("Models")).toBeInTheDocument();
    await waitFor(() =>
      expect(location.pathname).toBe("/dashboard/gateway/models"),
    );
    expect(location.search).toBe("?q=1");
  });

  it.each([
    [
      "/dashboard/gateway/models/anthropic/claude-haiku-5.5",
      "Claude Haiku 5.5",
      "Overview",
    ],
    [
      "/dashboard/gateway/models/anthropic/claude-haiku-5.5?tab=try",
      "Claude Haiku 5.5",
      "Try it",
    ],
    ["/dashboard/gateway/models/openai/gpt-4o?tab=api", "GPT-4o", "API"],
    [
      "/dashboard/gateway/models/cohere/north-mini-code%3Afree",
      /North Mini Code/,
      "Overview",
    ],
  ])("deep-links %s to the model page", async (url, name, tab) => {
    const location = renderUrl(url);
    expect(await heading(name)).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: tab, selected: true }),
    ).toBeInTheDocument();
    expect(location.pathname + location.search).toBe(url);
  });
});
