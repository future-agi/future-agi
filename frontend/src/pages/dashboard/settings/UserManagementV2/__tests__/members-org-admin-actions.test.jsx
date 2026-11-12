import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({ auth: {} }));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => mocks.auth,
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false, isSuccess: true }),
}));
// The chosen action opens AllActionForm, which sends the request; no form means no request.
vi.mock("../AllActionForm", () => ({
  default: ({ openActionForm, workspaceId }) => (
    <div data-testid="selected-member-action">
      {openActionForm.action}:{workspaceId}
    </div>
  ),
}));
vi.mock("src/components/iconify", () => ({
  default: React.forwardRef(function ActionIcon(_props, ref) {
    return <span ref={ref} data-testid="member-actions-trigger" />;
  }),
}));

import { ActionRender } from "../CellRender";
import { LEVELS } from "../constant";

// Production (15b3e8a6) gave an organization Owner these entries on Workspace
// Members rows of organization Admins and Owners. The workspace role and
// workspace removal endpoints refuse such a target (WS_ROLE_MODIFY_ORG_ADMIN,
// CANNOT_REMOVE_ORG_ADMIN_FROM_WS in accounts/views/rbac_views.py); the invite
// cancel endpoint has no target-rank check for an organization Admin or Owner.
const REASON =
  "Organization Admins and Owners are managed from Organization Members.";

const orgAdmin = {
  id: "member-2",
  email: "org.admin@example.test",
  status: "Active",
  type: "member",
  org_level: LEVELS.ADMIN,
  ws_level: LEVELS.WORKSPACE_ADMIN,
};

const renderActions = (data) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ActionRender data={data} workspaceScope workspaceId="workspace-1" />
    </QueryClientProvider>,
  );
};

const openActions = () => {
  fireEvent.click(screen.getByTestId("member-actions-trigger"));
};

const disabledItem = (title) =>
  screen.getByText(title).closest('[aria-disabled="true"]');

const selectedAction = () => screen.queryByTestId("selected-member-action");

describe("Workspace Members: row menu on an organization Admin or Owner", () => {
  beforeEach(() => {
    mocks.auth = {
      user: { id: "actor-1", email: "owner@example.test" },
      orgLevel: LEVELS.OWNER,
      wsLevel: LEVELS.WORKSPACE_ADMIN,
      effectiveLevel: LEVELS.OWNER,
    };
  });

  it.each([
    ["Admin", LEVELS.ADMIN],
    ["Owner", LEVELS.OWNER],
  ])(
    "OA01: an Owner sees Edit user info and Remove from workspace on an Active organization %s, disabled with the reason",
    (_role, orgLevel) => {
      renderActions({ ...orgAdmin, org_level: orgLevel });
      openActions();

      for (const title of ["Edit user info", "Remove from workspace"]) {
        const item = disabledItem(title);
        expect(item).not.toBeNull();
        expect(item).toBeVisible();
        expect(item).toHaveAccessibleDescription(REASON);
      }
      expect(screen.getAllByText(REASON).length).toBeGreaterThan(0);
      expect(screen.getAllByText(REASON)[0]).toBeVisible();
    },
  );

  it("OA02: the disabled entries send nothing on click, Enter or Space", () => {
    renderActions(orgAdmin);
    openActions();

    for (const title of ["Edit user info", "Remove from workspace"]) {
      const item = disabledItem(title);
      expect(item).not.toBeNull();
      fireEvent.click(item);
      fireEvent.keyDown(item, { key: "Enter" });
      fireEvent.keyDown(item, { key: " " });
      expect(selectedAction()).toBeNull();
      // The menu stays open, so the reason stays readable.
      expect(screen.getByText(title)).toBeVisible();
    }
  });

  it.each(["Pending", "Expired"])(
    "OA03: an Owner can cancel a %s invite to an organization Admin, as on production",
    (status) => {
      renderActions({ ...orgAdmin, status, type: "invite" });
      openActions();

      const cancel = screen.getByText("Cancel invite");
      expect(cancel.closest('[aria-disabled="true"]')).toBeNull();
      expect(screen.getByText("Resend the invite")).toBeVisible();
      expect(screen.queryByText(REASON)).toBeNull();

      fireEvent.click(cancel);
      expect(selectedAction()).toHaveTextContent("cancel-invite:workspace-1");
    },
  );

  it("OA04: members below Admin keep working entries with no reason", () => {
    renderActions({
      ...orgAdmin,
      org_level: LEVELS.MEMBER,
      ws_level: LEVELS.WORKSPACE_MEMBER,
    });
    openActions();

    for (const title of ["Edit user info", "Remove from workspace"]) {
      expect(screen.getByText(title)).toBeVisible();
      expect(
        screen.getByText(title).closest('[aria-disabled="true"]'),
      ).toBeNull();
    }
    expect(screen.queryByText(REASON)).toBeNull();
    fireEvent.click(screen.getByText("Remove from workspace"));
    expect(selectedAction()).toHaveTextContent("remove-ws-member:workspace-1");
  });

  it("OA05: an organization Admin still gets a disabled menu on another Admin, as on production", () => {
    mocks.auth.orgLevel = LEVELS.ADMIN;
    renderActions(orgAdmin);
    openActions();

    expect(screen.queryByText("Edit user info")).toBeNull();
    expect(screen.queryByText("Remove from workspace")).toBeNull();
    expect(screen.queryByText(REASON)).toBeNull();
  });
});
