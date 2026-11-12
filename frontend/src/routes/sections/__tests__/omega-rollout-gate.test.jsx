import React from "react";
import PropTypes from "prop-types";
import { QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Outlet, useLocation, useRoutes } from "react-router-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import axios from "src/utils/axios";
import { rolloutQueryClient } from "src/sections/omega/shell/__tests__/rollout-fixtures";
import {
  ShellTestProviders,
  ensureWebStorage,
} from "src/sections/omega/shell/__tests__/test-providers";
import { OMEGA_PRODUCTS } from "src/sections/omega/shell/rollout";

import { omegaDashboardRoutes, omegaStandaloneRoutes } from "../omega";

// RC-01's route part (READINESS 3.13): no route of a product renders unless
// the server admits the org and workspace. B (nothing admitted) and A2 (the
// product denied for its workspace) get Future AGI's standard 404 at every
// product URL, exactly like an unknown URL; A gets the page; a paused product
// shows the paused copy, never a 404; nothing renders while the answer loads
// or when it cannot be read.

vi.mock("src/auth/guard", () => ({ AuthGuard: ({ children }) => children }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "u1" }, logout: () => {} }),
}));

const PRODUCT_URLS = [
  ["studio_tab", "/dashboard/studio"],
  ["studio_tab", "/dashboard/studio/projects/p1"],
  ["deployments", "/dashboard/deployments"],
  ["deployments", "/dashboard/deployments/new"],
  ["deployments", "/dashboard/deployments/dep_42/logs"],
  ["sandboxes", "/dashboard/sandboxes"],
  ["sandboxes", "/dashboard/sandboxes/sb_1/files"],
  ["hub", "/dashboard/hub"],
  ["hub", "/dashboard/hub/publish"],
  ["hub", "/dashboard/hub/review/audit"],
  ["hub", "/dashboard/hub/someone/entry"],
  ["console", "/dashboard/console"],
  ["console", "/dashboard/console/agents/a1"],
];

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
    { path: "/404", element: <div data-testid="fa-404" /> },
    { path: "*", element: <div data-testid="fa-catch-all" /> },
  ]);
}

function LocationProbe({ onChange }) {
  onChange(useLocation().pathname);
  return null;
}

LocationProbe.propTypes = { onChange: PropTypes.func.isRequired };

function renderAt(path, queryClient) {
  const current = { pathname: null };
  render(
    <ShellTestProviders queryClient={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <RouteTable />
        <LocationProbe onChange={(p) => (current.pathname = p)} />
      </MemoryRouter>
    </ShellTestProviders>,
  );
  return current;
}

describe("Omega product routes behind the rollout gate", () => {
  beforeEach(() => ensureWebStorage());
  afterEach(() => vi.restoreAllMocks());

  it.each(PRODUCT_URLS)(
    "B (nothing admitted) typing %s's URL %s lands on the standard 404",
    async (_product, url) => {
      const location = renderAt(url, rolloutQueryClient({ admitted: [] }));
      expect(await screen.findByTestId("fa-404")).toBeInTheDocument();
      expect(location.pathname).toBe("/404");
      expect(screen.queryByTestId("host-layout")).not.toBeInTheDocument();
      expect(screen.queryByText(/paused/i)).not.toBeInTheDocument();
    },
  );

  it.each(PRODUCT_URLS)(
    "A2 (every product but %s admitted) gets the 404 at %s",
    async (product, url) => {
      const others = OMEGA_PRODUCTS.filter((p) => p !== product);
      const location = renderAt(url, rolloutQueryClient({ admitted: others }));
      expect(await screen.findByTestId("fa-404")).toBeInTheDocument();
      expect(location.pathname).toBe("/404");
    },
  );

  it("A (admitted) gets the page, not a 404", async () => {
    const location = renderAt("/dashboard/sandboxes", rolloutQueryClient());
    expect(
      await screen.findByTestId("omega-tab-sandboxes", {}, { timeout: 10_000 }),
    ).toBeInTheDocument();
    expect(location.pathname).toBe("/dashboard/sandboxes");
    expect(screen.queryByTestId("fa-404")).not.toBeInTheDocument();
  });

  it.each([
    ["deployments", "/dashboard/deployments", "Deployments is paused"],
    ["console", "/dashboard/console", "Omega Console is paused"],
  ])(
    "A with %s paused sees the paused copy at %s, never a 404",
    async (product, url, title) => {
      const location = renderAt(url, rolloutQueryClient({ paused: [product] }));
      expect(
        await screen.findByRole("heading", { name: title }),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/Your work and data are kept/),
      ).toBeInTheDocument();
      expect(location.pathname).toBe(url);
      expect(screen.queryByTestId("fa-404")).not.toBeInTheDocument();
    },
  );

  // The Hub is checked on its own, against ProductGate's paused copy as it
  // reads today ("This product is paused. Try again later.").
  it("A with hub paused sees the paused copy at /dashboard/hub, never a 404", async () => {
    const location = renderAt(
      "/dashboard/hub",
      rolloutQueryClient({ paused: ["hub"] }),
    );
    expect(
      await screen.findByRole("heading", { name: "Hub is paused" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("This product is paused. Try again later."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Not available in this deployment"),
    ).not.toBeInTheDocument();
    expect(location.pathname).toBe("/dashboard/hub");
    expect(screen.queryByTestId("fa-404")).not.toBeInTheDocument();
  });

  it("renders nothing of the product while the answer is loading", async () => {
    const pending = new QueryClient();
    // A query that never settles: the gate must hold the loading screen.
    pending.prefetchQuery({
      queryKey: CAPABILITIES_QUERY_KEY,
      queryFn: () => new Promise(() => {}),
    });
    renderAt("/dashboard/sandboxes", pending);
    await waitFor(() =>
      expect(screen.getByTestId("host-layout")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("omega-tab-sandboxes")).not.toBeInTheDocument();
    expect(screen.queryByTestId("fa-404")).not.toBeInTheDocument();
  });

  it("an unreadable answer shows a retry state and never the product", async () => {
    const get = vi.spyOn(axios, "get").mockRejectedValue(new Error("503"));
    const failing = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    await failing
      .prefetchQuery({
        queryKey: CAPABILITIES_QUERY_KEY,
        queryFn: () => Promise.reject(new Error("503")),
      })
      .catch(() => {});
    renderAt("/dashboard/hub", failing);
    // The hook asks again once (retry: 1) before it settles on the error.
    expect(
      await screen.findByRole(
        "button",
        { name: "Try again" },
        { timeout: 10_000 },
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Hub can't be opened right now/),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("fa-404")).not.toBeInTheDocument();
    // The retry state does not ask again by itself (no refetch loop): two calls,
    // the hook's own retry, then nothing until "Try again".
    const settled = get.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 1500));
    expect(get.mock.calls.length).toBe(settled);
  });

  it("the server's RESOLVER_UNAVAILABLE reads as unavailable, not hidden", async () => {
    const client = rolloutQueryClient();
    client.setQueryData(CAPABILITIES_QUERY_KEY, (old) => ({
      data: {
        ...old.data,
        features: {
          ...old.data.features,
          omega_sandboxes: {
            allowed: false,
            reason_code: "RESOLVER_UNAVAILABLE",
          },
        },
      },
    }));
    renderAt("/dashboard/sandboxes", client);
    expect(
      await screen.findByText(/Sandboxes can't be opened right now/),
    ).toBeInTheDocument();
  });

  it("an answer without the product (an older backend) keeps it hidden", async () => {
    const client = rolloutQueryClient();
    client.setQueryData(CAPABILITIES_QUERY_KEY, { data: { features: {} } });
    const location = renderAt("/dashboard/deployments", client);
    expect(await screen.findByTestId("fa-404")).toBeInTheDocument();
    expect(location.pathname).toBe("/404");
  });
});
