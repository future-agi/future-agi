/* eslint-disable react/prop-types */
import React, { Suspense } from "react";
import { MemoryRouter, useLocation, useRoutes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { dashboardRoutes } from "src/routes/sections/dashboard";

// /dashboard/settings/usage-summary is where the workspace menu's
// "Organization settings" leads every role but organization Owner and Admin.
// Where Usage Summary is not offered (off cloud without gateway credits), the
// route lands on the organization page this person's role may open, decided
// by the router's own route table and role guards: Org Settings for an Owner
// or Admin, and their Profile when no organization page admits them (a
// Member who administers a workspace included: License would tell them "No
// license yet"). Never on a page that refuses them.

const h = vi.hoisted(() => ({
  mode: "oss",
  orgRole: "Owner",
  wsRole: "workspace_admin",
  ledger: null,
}));

const LEDGER_NOT_INSTALLED = {
  isError: true,
  isSuccess: false,
  isLoading: false,
  isFetching: false,
  data: undefined,
  error: {
    statusCode: 501,
    response: { status: 501, data: { code: "billing_unavailable" } },
  },
  refetch: vi.fn(),
};
const LEDGER_READ_FAILED = {
  ...LEDGER_NOT_INSTALLED,
  error: { statusCode: 500, response: { status: 500, data: {} } },
};
const LEDGER_WITH_CREDITS = {
  isError: false,
  isSuccess: true,
  isLoading: false,
  isFetching: false,
  data: { enabled: true, available_usd: 1 },
  error: null,
  refetch: vi.fn(),
};

const user = () => ({
  id: "u1",
  organization_role: h.orgRole,
  ws_enabled: false,
});

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    mode: h.mode,
    isCloud: h.mode === "cloud",
    isOSS: h.mode === "oss",
    isEE: h.mode === "ee",
    isLoading: false,
    isSuccess: true,
  }),
}));
// The router's two role sources: the organization role on the user, and the
// current workspace role (WorkspaceContext for the route table, the auth
// context for RoleProtection).
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: user(), role: h.wsRole }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: h.wsRole }),
}));
vi.mock("src/sections/gateway/shell/page-status", async (importOriginal) => ({
  ...(await importOriginal()),
  isGatewayControlHidden: () => false,
}));
vi.mock("src/sections/settings/BillingV2/gateway-credits/api", () => ({
  useGatewayCreditSummary: ({ enabled } = {}) =>
    enabled
      ? h.ledger
      : { isError: false, isSuccess: false, isLoading: false, data: undefined },
}));
// The pages the route can land on; the routes and their guards stay real.
vi.mock("src/pages/dashboard/settings/OrgSettings", () => ({
  default: () => <h1>Organization settings</h1>,
}));
vi.mock("src/sections/settings/License/LicensePage", () => ({
  default: () => <h1>License</h1>,
}));
vi.mock("src/pages/dashboard/settings/ProfileSettings", () => ({
  default: () => <h1>Profile</h1>,
}));
vi.mock("src/sections/settings/UsageSummaryV2/UsageSummaryV2", () => ({
  default: () => <div>Platform usage</div>,
}));
vi.mock("src/sections/settings/UsageSummaryV2/GatewayUsageSplit", () => ({
  default: () => <div>AI Gateway usage split</div>,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

function Where() {
  return <output aria-label="Current page">{useLocation().pathname}</output>;
}

function SettingsRoutes() {
  const [dashboard] = dashboardRoutes(user(), h.wsRole, {
    isCloud: h.mode === "cloud",
  });
  const settings = dashboard.children.find(
    (child) => child.path === "settings",
  );
  return useRoutes([
    { path: "/dashboard/settings", children: settings.children },
  ]);
}

function openUsageSummaryRoute() {
  render(
    <HelmetProvider>
      <MemoryRouter initialEntries={["/dashboard/settings/usage-summary"]}>
        <Suspense fallback={<p>Loading</p>}>
          <SettingsRoutes />
        </Suspense>
        <Where />
      </MemoryRouter>
    </HelmetProvider>,
  );
  return screen.getByLabelText("Current page");
}

const NO_ACCESS = "You don't have access to this page";

beforeEach(() => {
  h.mode = "oss";
  h.orgRole = "Owner";
  h.wsRole = "workspace_admin";
  h.ledger = LEDGER_NOT_INSTALLED;
  LEDGER_READ_FAILED.refetch.mockReset();
});
afterEach(cleanup);

describe.each(["oss", "ee"])(
  "Usage Summary route where it is not offered (%s)",
  (mode) => {
    beforeEach(() => {
      h.mode = mode;
    });

    it.each([
      ["Owner", "workspace_admin", "Organization settings", "org-settings"],
      ["Admin", "workspace_member", "Organization settings", "org-settings"],
      ["Member", "workspace_admin", "Profile", "profile-settings"],
      ["Member", "workspace_member", "Profile", "profile-settings"],
      ["Viewer", "workspace_viewer", "Profile", "profile-settings"],
    ])(
      "lands an organization %s with %s on %s, a page their role opens",
      async (orgRole, wsRole, heading, path) => {
        h.orgRole = orgRole;
        h.wsRole = wsRole;
        const where = openUsageSummaryRoute();
        expect(
          await screen.findByRole("heading", { name: heading }),
        ).toBeInTheDocument();
        expect(where).toHaveTextContent(`/dashboard/settings/${path}`);
        expect(screen.queryByText(NO_ACCESS)).toBeNull();
      },
    );

    // An Owner or Admin whose credit ledger read failed sees Usage Summary
    // listed in the Settings menu; the page keeps its heading and says the
    // usage could not be loaded, with Retry, instead of opening another page.
    it.each(["Owner", "Admin"])(
      "keeps Usage Summary for an organization %s when the credit ledger cannot be read, with Retry",
      async (orgRole) => {
        h.orgRole = orgRole;
        h.ledger = LEDGER_READ_FAILED;
        const where = openUsageSummaryRoute();
        expect(
          await screen.findByRole("heading", { name: "Usage summary" }),
        ).toBeInTheDocument();
        expect(screen.getByRole("alert")).toHaveTextContent(
          "AI Gateway usage couldn't be loaded",
        );
        fireEvent.click(screen.getByRole("button", { name: "Retry" }));
        expect(LEDGER_READ_FAILED.refetch).toHaveBeenCalledTimes(1);
        expect(where).toHaveTextContent("/dashboard/settings/usage-summary");
        expect(screen.queryByText("Organization settings")).toBeNull();
      },
    );

    // Guard (passes at the base too): where gateway credits exist off cloud,
    // Usage Summary is offered and nothing changes.
    it("keeps Usage Summary where the organization has gateway credits", async () => {
      h.ledger = LEDGER_WITH_CREDITS;
      const where = openUsageSummaryRoute();
      expect(
        await screen.findByText("AI Gateway usage split"),
      ).toBeInTheDocument();
      expect(where).toHaveTextContent("/dashboard/settings/usage-summary");
    });
  },
);

// Guard (passes at the base too): on cloud Usage Summary is offered to every
// role, and the route keeps showing it.
describe("Usage Summary route on cloud", () => {
  it.each([
    ["Owner", "workspace_admin"],
    ["Admin", "workspace_member"],
    ["Member", "workspace_admin"],
    ["Member", "workspace_member"],
    ["Viewer", "workspace_viewer"],
  ])(
    "keeps Usage Summary for an organization %s with %s",
    async (orgRole, wsRole) => {
      h.mode = "cloud";
      h.orgRole = orgRole;
      h.wsRole = wsRole;
      const where = openUsageSummaryRoute();
      expect(await screen.findByText("Platform usage")).toBeInTheDocument();
      expect(where).toHaveTextContent("/dashboard/settings/usage-summary");
    },
  );
});
