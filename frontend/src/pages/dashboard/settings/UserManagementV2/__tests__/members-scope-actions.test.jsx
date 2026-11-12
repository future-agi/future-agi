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
vi.mock("../AllActionForm", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({
  default: React.forwardRef(function ActionIcon(_props, ref) {
    return <span ref={ref} data-testid="member-actions-trigger" />;
  }),
}));

import { ActionRender } from "../CellRender";
import { LEVELS } from "../constant";

const teammate = {
  id: "teammate-1",
  email: "teammate@example.test",
  status: "Active",
  org_level: LEVELS.MEMBER,
  ws_level: LEVELS.WORKSPACE_MEMBER,
  // The removal endpoint requires another active workspace in the organization.
  workspaces: [{ workspace_id: "workspace-1" }, { workspace_id: "workspace-2" }],
};

const renderActions = (props = {}) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = (nextProps) => (
    <QueryClientProvider client={client}>
      <ActionRender
        data={teammate}
        workspaceScope
        workspaceId="workspace-1"
        {...nextProps}
      />
    </QueryClientProvider>
  );
  const result = render(view(props));
  return {
    ...result,
    rerenderActions: (nextProps) => result.rerender(view(nextProps)),
  };
};

const openActions = () => {
  fireEvent.click(screen.getByTestId("member-actions-trigger"));
};

const expectWorkspaceActions = (canEdit, canRemove = canEdit) => {
  const edit = screen.queryByText("Edit user info");
  const remove = screen.queryByText("Remove from workspace");
  if (canEdit) {
    expect(edit).toBeVisible();
  } else {
    expect(edit).toBeNull();
  }
  if (canRemove) {
    expect(remove).toBeVisible();
  } else {
    expect(remove).toBeNull();
  }
  expect(screen.queryByText("Remove from organization")).toBeNull();
};

describe("Members scope actions", () => {
  beforeEach(() => {
    mocks.auth = {
      user: { id: "actor-1", email: "actor@example.test" },
      orgLevel: LEVELS.MEMBER,
      wsLevel: LEVELS.WORKSPACE_ADMIN,
      effectiveLevel: LEVELS.WORKSPACE_ADMIN,
    };
  });

  it("S01: workspace admin can edit/remove a same-org-role workspace member", () => {
    renderActions();
    openActions();
    expectWorkspaceActions(true);
  });

  it("S02: org Viewer with workspace-admin authority can manage an org Member", () => {
    mocks.auth.orgLevel = LEVELS.VIEWER;
    renderActions();
    openActions();
    expectWorkspaceActions(true);
  });

  it("S03: workspace admin cannot manage an organization admin", () => {
    renderActions({ data: { ...teammate, org_level: LEVELS.ADMIN } });
    openActions();
    expectWorkspaceActions(false);
  });

  it("S04: an owner sees edit/remove for an organization admin in workspace scope, disabled with the reason", () => {
    mocks.auth.orgLevel = LEVELS.OWNER;
    renderActions({ data: { ...teammate, org_level: LEVELS.ADMIN } });
    openActions();
    for (const title of ["Edit user info", "Remove from workspace"]) {
      const item = screen.getByText(title).closest('[aria-disabled="true"]');
      expect(item).not.toBeNull();
      expect(item).toBeVisible();
    }
    expect(
      screen.getByText(
        "Organization Admins and Owners are managed from Organization Members.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("Remove from organization")).toBeNull();
  });

  it("S05: higher org rank does not give a workspace member workspace-admin powers", () => {
    mocks.auth.wsLevel = LEVELS.WORKSPACE_MEMBER;
    mocks.auth.effectiveLevel = LEVELS.WORKSPACE_MEMBER;
    renderActions({
      data: {
        ...teammate,
        org_level: LEVELS.VIEWER,
        ws_level: LEVELS.WORKSPACE_VIEWER,
      },
    });
    openActions();
    expectWorkspaceActions(false);
  });

  it.each([
    ["S06", LEVELS.WORKSPACE_ADMIN],
    ["S07", LEVELS.OWNER],
  ])("%s: workspace admin can remove but not edit an equal/higher workspace level (%s)", (_id, wsLevel) => {
    renderActions({ data: { ...teammate, ws_level: wsLevel } });
    openActions();
    expectWorkspaceActions(false, true);
  });

  it("S08: organization admin can manage a non-org-admin workspace admin", () => {
    mocks.auth.orgLevel = LEVELS.ADMIN;
    mocks.auth.wsLevel = null;
    renderActions({
      data: { ...teammate, ws_level: LEVELS.WORKSPACE_ADMIN },
    });
    openActions();
    expectWorkspaceActions(true);
  });

  it("S09: workspace permissions never allow editing yourself", () => {
    renderActions({
      data: { ...teammate, id: "actor-1", email: "actor@example.test" },
    });
    openActions();
    expectWorkspaceActions(false);
  });

  it("S10: workspace admin authority does not leak into the organization page", () => {
    renderActions({ workspaceScope: false });
    openActions();
    expectWorkspaceActions(false);
  });

  it("S11: organization-page admin actions retain organization scope", () => {
    mocks.auth.orgLevel = LEVELS.ADMIN;
    renderActions({ workspaceScope: false });
    openActions();
    expect(screen.getByText("Edit user info")).toBeVisible();
    expect(screen.getByText("Remove from organization")).toBeVisible();
    expect(screen.queryByText("Remove from workspace")).toBeNull();
  });

  it("S12: missing actor workspace level does not grant workspace-admin authority", () => {
    mocks.auth.wsLevel = null;
    renderActions();
    openActions();
    expectWorkspaceActions(false);
  });

  it("S13: missing target workspace level blocks edits but not removal", () => {
    renderActions({ data: { ...teammate, ws_level: null } });
    openActions();
    expectWorkspaceActions(false, true);
  });

  it("S14: workspace permission accepts the user-info level fallback", () => {
    mocks.auth.orgLevel = undefined;
    mocks.auth.wsLevel = undefined;
    mocks.auth.user = {
      ...mocks.auth.user,
      org_level: LEVELS.MEMBER,
      ws_level: LEVELS.WORKSPACE_ADMIN,
    };
    renderActions();
    openActions();
    expectWorkspaceActions(true);
  });

  it("S15: action permissions update when scope or workspace levels change", () => {
    const { rerenderActions } = renderActions();
    openActions();
    expectWorkspaceActions(true);

    rerenderActions({ workspaceScope: false });
    expectWorkspaceActions(false);

    rerenderActions({ workspaceScope: true });
    expectWorkspaceActions(true);

    mocks.auth.wsLevel = LEVELS.WORKSPACE_MEMBER;
    rerenderActions({ workspaceScope: true });
    expectWorkspaceActions(false);

    mocks.auth.wsLevel = LEVELS.WORKSPACE_ADMIN;
    rerenderActions({ data: { ...teammate, ws_level: LEVELS.WORKSPACE_ADMIN } });
    expectWorkspaceActions(false, true);
  });
});
