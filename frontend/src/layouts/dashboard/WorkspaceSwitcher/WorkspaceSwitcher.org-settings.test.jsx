/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import WorkspaceSwitcher from "./WorkspaceSwitcher";

// "Organization settings" in the workspace menu opens Org Settings for the
// users the settings menu lists it for (organization Owner or Admin). On
// cloud everyone else keeps the usage summary, which every role may open.
// Off cloud the usage summary is not offered to them, so the entry opens
// something only when an organization settings page admits their role;
// otherwise it would open a page that refuses them, or a License page with
// nothing on it for them. The row is still listed for every role, as in
// production: for them it is disabled and says why.
const h = vi.hoisted(() => ({
  orgRole: "Owner",
  wsRole: "workspace_member",
  mode: "oss",
  modeConfirmed: true,
  prototype: false,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { name: "Pat", ws_enabled: true, organization_role: h.orgRole },
    role: h.wsRole,
    logout: vi.fn(),
  }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    mode: h.mode,
    isCloud: h.mode === "cloud",
    isOSS: h.mode === "oss",
    isEE: h.mode === "ee",
    isLoading: !h.modeConfirmed,
    isSuccess: h.modeConfirmed,
  }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    currentOrganizationDisplayName: "Acme",
    currentOrganizationRole: h.orgRole,
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceId: "w1",
    currentWorkspaceDisplayName: "First workspace",
    currentWorkspaceRole: h.wsRole,
  }),
}));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => h.prototype,
}));
vi.mock("src/auth/sign-out", () => ({ signOutToSignIn: vi.fn() }));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/pages/dashboard/settings/UserManagementV2/AllActionForm", () => ({
  default: () => null,
}));
vi.mock("./CreateOrganizationModal", () => ({ default: () => null }));
vi.mock("./SelectWorkspace", () => ({
  default: React.forwardRef(function SelectWorkspace() {
    return null;
  }),
}));
vi.mock("./SelectOrganization", () => ({
  default: React.forwardRef(function SelectOrganization() {
    return null;
  }),
}));
vi.mock("./ThemeSelector", () => ({
  default: React.forwardRef(function ThemeSelector() {
    return null;
  }),
}));

function Location() {
  return <output data-testid="path">{useLocation().pathname}</output>;
}

function openMenu() {
  render(
    <MemoryRouter initialEntries={["/dashboard/gateway/models"]}>
      <WorkspaceSwitcher />
      <Location />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Switch workspace" }));
}

const entry = () =>
  screen.queryByRole("button", { name: "Organization settings", exact: true });

const NO_PAGE_REASON = "No organization settings page is open to your role.";

function openOrganizationSettings() {
  openMenu();
  fireEvent.click(entry());
  return screen.getByTestId("path");
}

beforeEach(() => {
  h.orgRole = "Owner";
  h.wsRole = "workspace_member";
  h.mode = "oss";
  h.modeConfirmed = true;
  h.prototype = false;
});
afterEach(cleanup);

describe.each(["oss", "ee", "cloud"])(
  "Organization settings in the workspace menu (%s)",
  (mode) => {
    beforeEach(() => {
      h.mode = mode;
    });

    it.each(["Owner", "Admin"])(
      "opens Org Settings for an organization %s",
      (orgRole) => {
        h.orgRole = orgRole;
        expect(openOrganizationSettings()).toHaveTextContent(
          /^\/dashboard\/settings\/org-settings$/,
        );
      },
    );

    it("keeps the Workspaces page in a prototype build, even for an Owner", () => {
      h.prototype = true;
      expect(openOrganizationSettings()).toHaveTextContent(
        /^\/dashboard\/settings\/workspace$/,
      );
    });
  },
);

describe("Organization settings for other roles on cloud", () => {
  it.each([
    ["Member", "workspace_member"],
    ["Viewer", "workspace_viewer"],
    [undefined, "workspace_member"],
  ])("keeps the usage summary for organization role %s", (orgRole, wsRole) => {
    h.mode = "cloud";
    h.orgRole = orgRole;
    h.wsRole = wsRole;
    expect(openOrganizationSettings()).toHaveTextContent(
      /^\/dashboard\/settings\/usage-summary$/,
    );
  });
});

describe.each(["oss", "ee"])(
  "Organization settings for other roles off cloud (%s)",
  (mode) => {
    beforeEach(() => {
      h.mode = mode;
    });

    it.each([
      ["Member", "workspace_member"],
      ["Member", "workspace_admin"],
      ["Viewer", "workspace_viewer"],
      [undefined, "workspace_member"],
    ])(
      "is listed but disabled, with the reason, for organization role %s with %s, whom no organization settings page admits",
      (orgRole, wsRole) => {
        h.orgRole = orgRole;
        h.wsRole = wsRole;
        openMenu();
        const row = screen.getByRole("button", {
          name: "Organization settings",
          exact: true,
        });
        expect(screen.getByRole("button", { name: "Log out" })).toBeVisible();
        expect(row).toHaveAttribute("aria-disabled", "true");
        // Still reachable by keyboard so the reason can be read.
        expect(row).not.toHaveAttribute("disabled");
        expect(row).toHaveAccessibleDescription(NO_PAGE_REASON);
        expect(screen.getByText(NO_PAGE_REASON)).toBeVisible();
        // It goes nowhere.
        fireEvent.click(row);
        expect(screen.getByTestId("path")).toHaveTextContent(
          /^\/dashboard\/gateway\/models$/,
        );
        fireEvent.keyDown(row, { key: "Enter" });
        expect(screen.getByTestId("path")).toHaveTextContent(
          /^\/dashboard\/gateway\/models$/,
        );
      },
    );

    // Guard (passes at the base too): where a page is open the row is unchanged.
    it("stays enabled, with no reason, for an organization Owner", () => {
      h.orgRole = "Owner";
      h.wsRole = "workspace_member";
      openMenu();
      const row = screen.getByRole("button", {
        name: "Organization settings",
        exact: true,
      });
      expect(row).not.toHaveAttribute("aria-disabled");
      expect(row).toHaveAccessibleDescription("");
      expect(screen.queryByText(NO_PAGE_REASON)).toBeNull();
      fireEvent.click(row);
      expect(screen.getByTestId("path")).toHaveTextContent(
        /^\/dashboard\/settings\/org-settings$/,
      );
    });

    it("stays offered while the deployment mode is not yet known", () => {
      h.orgRole = "Member";
      h.modeConfirmed = false;
      expect(openOrganizationSettings()).toHaveTextContent(
        /^\/dashboard\/settings\/usage-summary$/,
      );
    });
  },
);
