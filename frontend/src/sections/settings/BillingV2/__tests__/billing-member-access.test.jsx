import React, { Suspense } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { dashboardRoutes } from "src/routes/sections/dashboard";

// Settings > Billing is open to an organization Owner or Admin and to a
// workspace admin. Anyone else who opens it (from a link, or the old AI
// Gateway billing URLs, which land there on cloud) is told who can, on the
// Billing URL itself: never the full-page 404, and the billing page never
// loads, so none of its requests is sent.

const state = vi.hoisted(() => ({
  role: "Member",
  wsRole: null,
  requests: [],
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: {
      id: "u1",
      role: state.role,
      organization_role: state.role,
      ws_enabled: false,
    },
    role: state.wsRole,
  }),
}));
// The billing page itself: it loads only for a role that may open Billing.
vi.mock("src/sections/settings/BillingV2/BillingPage", () => ({
  default: () => <h1>Billing page</h1>,
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isCloud: true,
    isOSS: false,
    isEE: false,
    isLoading: false,
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
  useWorkspaceFromList: () => ({ workspace: null }),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const record = (method) =>
    vi.fn(async (url) => {
      state.requests.push(`${method} ${url}`);
      throw new Error(`unmocked ${method} ${url}`);
    });
  return {
    ...actual,
    default: { ...actual.default, get: record("GET"), post: record("POST") },
  };
});

const billingRoute = (role, { isCloud }) => {
  const [dashboardRoute] = dashboardRoutes(
    { organization_role: role, ws_enabled: false },
    null,
    { isCloud },
  );
  return dashboardRoute.children
    .find((child) => child.path === "settings")
    .children.find((child) => child.path === "billing");
};

const NO_ACCESS = "Ask an organization Owner or Admin for access to Billing.";

describe("Settings > Billing for a Member", () => {
  beforeEach(() => {
    state.role = "Member";
    state.wsRole = null;
    state.requests.length = 0;
  });

  it("is a route for every role, on cloud and off", () => {
    for (const role of ["Member", "Viewer", "Owner", "Admin"]) {
      expect(billingRoute(role, { isCloud: true })).toBeDefined();
      expect(billingRoute(role, { isCloud: false })).toBeDefined();
    }
  });

  it("tells a Member who can open Billing, on the Billing URL, and loads no billing data", () => {
    render(
      <MemoryRouter initialEntries={["/dashboard/settings/billing"]}>
        <Routes>
          <Route
            path="/dashboard/settings/billing"
            element={billingRoute("Member", { isCloud: true }).element}
          />
          <Route path="*" element={<div>Somewhere else</div>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(
      screen.getByRole("heading", { name: "Billing" }),
    ).toBeInTheDocument();
    expect(screen.getByText(NO_ACCESS)).toBeInTheDocument();
    expect(screen.queryByText("Somewhere else")).toBeNull();
    expect(state.requests).toEqual([]);
  });

  it("tells a Viewer the same", () => {
    state.role = "Viewer";
    render(
      <MemoryRouter initialEntries={["/dashboard/settings/billing"]}>
        <Routes>
          <Route
            path="/dashboard/settings/billing"
            element={billingRoute("Viewer", { isCloud: false }).element}
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText(NO_ACCESS)).toBeInTheDocument();
  });
});

// Billing is the organization's: its plan, invoices and gateway credits. So
// an organization Owner or Admin opens it whatever their workspace role, a
// workspace admin opens it, and a Member or Viewer of the organization who is
// not a workspace admin is told who can.
describe("Settings > Billing access rule", () => {
  const openBilling = (role, wsRole, { isCloud }) => {
    state.role = role;
    state.wsRole = wsRole;
    render(
      <MemoryRouter initialEntries={["/dashboard/settings/billing"]}>
        <Suspense fallback={<div>Loading</div>}>
          <Routes>
            <Route
              path="/dashboard/settings/billing"
              element={billingRoute(role, { isCloud }).element}
            />
            <Route path="*" element={<div>Somewhere else</div>} />
          </Routes>
        </Suspense>
      </MemoryRouter>,
    );
  };

  beforeEach(() => {
    state.requests.length = 0;
  });

  for (const isCloud of [true, false]) {
    const where = isCloud ? "on cloud" : "off cloud";

    it(`opens for an organization Admin who is not a workspace admin, ${where}`, async () => {
      openBilling("Admin", "workspace_member", { isCloud });
      expect(await screen.findByText("Billing page")).toBeInTheDocument();
      expect(screen.queryByText(NO_ACCESS)).toBeNull();
      expect(screen.queryByText("Somewhere else")).toBeNull();
    });

    it(`opens for an organization Owner, ${where}`, async () => {
      openBilling("Owner", "workspace_viewer", { isCloud });
      expect(await screen.findByText("Billing page")).toBeInTheDocument();
    });

    it(`gates an organization Member with workspace-admin permissions, ${where}`, async () => {
      openBilling("Member", "workspace_admin", { isCloud });
      if (isCloud) {
        expect(await screen.findByText("Billing page")).toBeInTheDocument();
        expect(screen.queryByText(NO_ACCESS)).toBeNull();
      } else {
        expect(screen.getByText(NO_ACCESS)).toBeInTheDocument();
        expect(screen.queryByText("Billing page")).toBeNull();
        expect(screen.queryByText("Somewhere else")).toBeNull();
      }
      expect(state.requests).toEqual([]);
    });

    for (const role of ["Member", "Viewer"]) {
      it(`tells an organization ${role} who is not a workspace admin who can open it, ${where}`, () => {
        openBilling(role, "workspace_member", { isCloud });
        expect(screen.getByText(NO_ACCESS)).toBeInTheDocument();
        expect(screen.queryByText("Billing page")).toBeNull();
        expect(screen.queryByText("Somewhere else")).toBeNull();
        expect(state.requests).toEqual([]);
      });
    }
  }
});
