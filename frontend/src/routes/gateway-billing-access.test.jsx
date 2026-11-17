/* eslint-disable react/prop-types */
import React, { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Outlet, useLocation, useRoutes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { dashboardRoutes } from "./sections/dashboard";
import {
  useNavGatewayData,
  useNavSettingsData,
} from "src/layouts/dashboard/config-navigation";

const h = vi.hoisted(() => ({
  org: "Owner",
  workspace: "workspace_admin",
  cloud: false,
  calls: [],
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: {
      id: "u1",
      role: h.org,
      organization_role: h.org,
      ws_enabled: false,
      default_workspace_role: h.workspace,
    },
    role: h.workspace,
  }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isCloud: h.cloud,
    isOSS: !h.cloud,
    isEE: false,
    isLoading: false,
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: h.workspace }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
  useWorkspaceFromList: () => ({ workspace: null }),
}));
vi.mock("src/sections/gateway/components/GatewayGuard", () => ({
  default: () => <div>Active gateway required</div>,
}));
vi.mock("src/sections/settings/BillingV2/BudgetManager", () => ({
  default: () => <div>Budgets</div>,
}));
vi.mock("src/sections/settings/BillingV2/PaymentMethods", () => ({
  default: () => <div>Payment methods</div>,
}));
vi.mock("ag-grid-react", () => ({ AgGridReact: () => <div>Ledger grid</div> }));
vi.mock("src/utils/axios", async (original) => {
  const actual = await original();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(async (url) => {
        h.calls.push(url);
        const gw = actual.endpoints.gateway;
        if (url === gw.managedCredits)
          return {
            data: {
              result: {
                enabled: true,
                available_usd: 1.23,
                granted_usd: 1.23,
                settled_spend_usd: 0,
                billing_available: false,
                auto_reload: { enabled: false },
                markup_percent: 5,
              },
            },
          };
        if (url === gw.managedCreditEntries)
          return { data: { result: { entries: [], next_cursor: null } } };
        if (url === gw.managedCreditStatement)
          return {
            data: {
              result: {
                month: "2026-10",
                opening_balance: 0,
                closing_balance: 1.23,
                credits_by_kind: { grant: 1.23 },
                spend_by_model: [],
              },
            },
          };
        if (String(url).includes("/checkout/"))
          return { data: { result: { credited: false } } };
        return { data: { result: {} } };
      }),
    },
  };
});

function Providers({ children, initial = "/dashboard/gateway/logs" }) {
  return (
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ThemeProvider
        theme={createTheme({ palette: palette("light"), typography })}
      >
        <MemoryRouter initialEntries={[initial]}>{children}</MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
function Routed() {
  const location = useLocation();
  const routes = dashboardRoutes(
    { organization_role: h.org, ws_enabled: false },
    h.workspace,
    { isCloud: h.cloud },
  );
  const dashboard = routes[0];
  dashboard.element = <Outlet />;
  dashboard.children = dashboard.children.map((route) =>
    ["gateway", "settings"].includes(route.path)
      ? { ...route, element: <Outlet /> }
      : route,
  );
  const element = useRoutes(routes);
  return (
    <>
      <output data-testid="location">
        {location.pathname}
        {location.search}
        {location.hash}
      </output>
      <Suspense fallback={<div>Loading</div>}>{element}</Suspense>
    </>
  );
}
beforeEach(() => {
  h.org = "Owner";
  h.workspace = "workspace_admin";
  h.cloud = false;
  h.calls = [];
});

describe.each([false, true])("central credit billing, cloud=%s", (cloud) => {
  it.each(["Owner", "Admin"])(
    "gives %s Settings Billing but no gateway rail Billing item",
    async (role) => {
      h.org = role;
      h.cloud = cloud;
      const hooks = renderHook(
        () => ({
          gateway: useNavGatewayData(),
          settings: useNavSettingsData(),
        }),
        {
          wrapper: ({ children }) => (
            <Providers initial="/dashboard/settings/profile-settings">
              {children}
            </Providers>
          ),
        },
      );
      await waitFor(() =>
        expect(
          hooks.result.current.settings
            .flatMap((g) => g.items)
            .find((item) => item.title === "Billing")?.path,
        ).toBe("/dashboard/settings/billing"),
      );
      expect(
        hooks.result.current.gateway
          .flatMap((g) => g.items)
          .some((item) => /Billing/.test(item.title)),
      ).toBe(false);
    },
  );

  it.each(["Member", "Viewer", undefined])(
    "has no gateway Billing item for org role %s",
    (role) => {
      h.org = role;
      h.cloud = cloud;
      const { result } = renderHook(() => useNavGatewayData(), {
        wrapper: Providers,
      });
      expect(
        result.current
          .flatMap((g) => g.items)
          .some((item) => /Billing/.test(item.title)),
      ).toBe(false);
      expect(
        h.calls.filter((url) => url.includes("/managed-credits/")),
      ).toEqual([]);
    },
  );

  describe.each([
    "/dashboard/gateway/billing",
    "/dashboard/gateway/billing/credits",
  ])("legacy %s", (url) => {
    it.each(["Owner", "Admin"])(
      "redirects %s to v2 Billing with checkout query and hash",
      async (role) => {
        h.org = role;
        h.cloud = cloud;
        render(
          <Providers initial={`${url}?checkout_session=cs_fixture#statement`}>
            <Routed />
          </Providers>,
        );
        expect(
          await screen.findByRole("region", { name: "AI Gateway credits" }),
        ).toBeInTheDocument();
        expect(screen.getByTestId("location")).toHaveTextContent(
          "/dashboard/settings/billing?checkout_session=cs_fixture#statement",
        );
        expect(screen.queryByText("Active gateway required")).toBeNull();
        expect(h.calls).toContain("/agentcc/managed-credits/");
      },
    );

    it.each(["Member", "Viewer"])(
      "refuses %s on central Billing before any billing request",
      async (role) => {
        h.org = role;
        h.workspace = "workspace_member";
        h.cloud = cloud;
        render(
          <Providers initial={url}>
            <Routed />
          </Providers>,
        );
        expect(
          await screen.findByText(
            /Ask an organization Owner or Admin for access to Billing\./,
          ),
        ).toBeInTheDocument();
        expect(screen.getByTestId("location")).toHaveTextContent(
          "/dashboard/settings/billing",
        );
        expect(
          screen.queryByRole("region", { name: "AI Gateway credits" }),
        ).toBeNull();
        expect(h.calls).toEqual([]);
      },
    );
  });

  it.each(["Owner", "Admin"])(
    "loads v2 credit history directly for %s",
    async (role) => {
      h.org = role;
      h.cloud = cloud;
      render(
        <Providers initial="/dashboard/settings/billing">
          <Routed />
        </Providers>,
      );
      expect(
        await screen.findByRole("region", { name: "AI Gateway credits" }),
      ).toBeInTheDocument();
      expect(
        await screen.findByRole("region", { name: "Monthly statement" }),
      ).toBeInTheDocument();
    },
  );

  it("preserves platform card parameters through the legacy redirect", async () => {
    h.cloud = cloud;
    render(
      <Providers initial="/dashboard/gateway/billing/platform?card=added&session_id=cs_card&keep=1#card">
        <Routed />
      </Providers>,
    );
    expect(
      await screen.findByRole("region", { name: "AI Gateway credits" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/settings/billing?card=added&session_id=cs_card&keep=1#card",
    );
    expect(
      h.calls.some((url) => url.includes("/managed-credits/checkout/")),
    ).toBe(false);
  });
});
