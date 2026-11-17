import React, { Suspense } from "react";
import PropTypes from "prop-types";
import { MemoryRouter, Outlet, useLocation, useRoutes } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { rolloutQueryClient } from "src/sections/omega/shell/__tests__/rollout-fixtures";
import {
  ShellTestProviders,
  ensureWebStorage,
} from "src/sections/omega/shell/__tests__/test-providers";
import { OMEGA_PRODUCTS } from "src/sections/omega/shell/rollout";

import { omegaDashboardRoutes } from "../omega";

// With VITE_OMEGA_CONSOLE=1 the Hub is gated like every other Omega product.
// An organisation admitted to it opens the Hub page at each Hub URL, never a
// "Not available in this deployment" state. One that is not admitted gets
// Future AGI's standard 404, and a paused Hub shows ProductGate's paused copy.
// Every product is admitted unless a case says otherwise (ShellTestProviders'
// default rollout answer).

vi.mock("src/auth/guard", () => ({ AuthGuard: ({ children }) => children }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "u1" }, logout: () => {} }),
}));
// Stand-ins for the Hub pages these URLs open: a page that mounts is seen as
// itself, not as a failed registry request.
vi.mock("src/sections/omega/hub/HubBrowsePage", () => ({
  default: () => <div data-testid="hub-page-browse" />,
}));
vi.mock("src/sections/omega/hub/HubReviewQueuePage", () => ({
  default: () => <div data-testid="hub-page-review" />,
}));
vi.mock("src/sections/omega/hub/HubEntryPage", () => ({
  default: () => <div data-testid="hub-page-entry" />,
}));

const NOT_AVAILABLE = "Not available in this deployment";

const HUB_URLS = [
  ["/dashboard/hub", "hub-page-browse"],
  ["/dashboard/hub/review", "hub-page-review"],
  ["/dashboard/hub/future-agi/first-agent/files", "hub-page-entry"],
];

function RouteTable() {
  return useRoutes([
    {
      path: "/dashboard",
      element: (
        <div data-testid="host-layout">
          <Suspense fallback={null}>
            <Outlet />
          </Suspense>
        </div>
      ),
      children: [
        { path: "develop", element: <div data-testid="host-develop" /> },
        ...omegaDashboardRoutes(),
      ],
    },
    { path: "/404", element: <div data-testid="fa-404" /> },
  ]);
}

function LocationProbe({ into }) {
  into.pathname = useLocation().pathname;
  return null;
}

LocationProbe.propTypes = { into: PropTypes.object.isRequired };

function renderAt(path, queryClient) {
  const location = { pathname: null };
  render(
    <ShellTestProviders queryClient={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <RouteTable />
        <LocationProbe into={location} />
      </MemoryRouter>
    </ShellTestProviders>,
  );
  return location;
}

describe("Hub URLs with VITE_OMEGA_CONSOLE=1", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    ensureWebStorage();
  });
  afterEach(() => vi.unstubAllEnvs());

  it.each(HUB_URLS)(
    "%s opens the Hub page for an admitted organisation",
    async (url, hubPage) => {
      const location = renderAt(url);
      // RED at the base: the not-available state shows and this never mounts.
      const page = await screen.findByTestId(hubPage, {}, { timeout: 10_000 });
      expect(screen.getByTestId("host-layout")).toContainElement(page);
      expect(screen.queryByText(NOT_AVAILABLE)).toBeNull();
      expect(screen.queryByTestId("fa-404")).toBeNull();
      expect(location.pathname).toBe(url);
    },
  );

  it.each(HUB_URLS)(
    "%s lands on the standard 404 for an organisation not admitted to the Hub",
    async (url, hubPage) => {
      const others = OMEGA_PRODUCTS.filter((product) => product !== "hub");
      const location = renderAt(url, rolloutQueryClient({ admitted: others }));
      expect(await screen.findByTestId("fa-404")).toBeInTheDocument();
      expect(location.pathname).toBe("/404");
      expect(screen.queryByTestId(hubPage)).toBeNull();
      expect(screen.queryByText(NOT_AVAILABLE)).toBeNull();
    },
  );

  it("a paused Hub shows the paused copy, not a Hub page or Not available", async () => {
    const location = renderAt(
      "/dashboard/hub",
      rolloutQueryClient({ paused: ["hub"] }),
    );
    // RED at the base: the not-available state (an h1 "Hub") shows instead.
    expect(
      await screen.findByRole("heading", { name: "Hub is paused" }),
    ).toBeVisible();
    expect(
      screen.getByText("This product is paused. Try again later."),
    ).toBeVisible();
    expect(screen.queryByText(NOT_AVAILABLE)).toBeNull();
    expect(screen.queryByTestId("hub-page-browse")).toBeNull();
    expect(screen.queryByTestId("fa-404")).toBeNull();
    expect(location.pathname).toBe("/dashboard/hub");
  });
});
