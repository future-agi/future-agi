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
import ShowActionMenus from "../ShowActionMenus";
import { LEVELS, wsActionMenusByStatus } from "../constant";

const teammate = {
  id: "teammate-1",
  email: "teammate@example.test",
  status: "Active",
  org_level: LEVELS.MEMBER,
  ws_level: LEVELS.WORKSPACE_MEMBER,
  // These scenarios assume two active memberships. The backend checks the count;
  // the workspace list does not expose it, so it is not a UI permission input.
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

const expectWorkspaceActions = (canEdit, canRemove) => {
  for (const [title, allowed] of [
    ["Edit user info", canEdit],
    ["Remove from workspace", canRemove],
  ]) {
    const item = screen.queryByText(title);
    if (allowed) {
      expect(item).toBeVisible();
    } else {
      expect(item).toBeNull();
    }
  }
  expect(screen.queryByText("Remove from organization")).toBeNull();
};

const renderMenu = (permissions) =>
  render(
    <ShowActionMenus
      actionRef={{ current: document.body }}
      open
      onClose={vi.fn()}
      data={teammate}
      setOpenActionForm={vi.fn()}
      canResend={false}
      menusByStatus={wsActionMenusByStatus}
      {...permissions}
    />,
  );

describe("Members workspace removal authorization", () => {
  beforeEach(() => {
    mocks.auth = {
      user: { id: "actor-1", email: "actor@example.test" },
      orgLevel: LEVELS.MEMBER,
      wsLevel: LEVELS.WORKSPACE_ADMIN,
      effectiveLevel: LEVELS.WORKSPACE_ADMIN,
    };
  });

  // id, case, actor overrides, target overrides, edit allowed, remove allowed.
  // Unlike role editing, removal has no target workspace-rank comparison.
  it.each([
    ["R01", "org Viewer can manage a lower-workspace-rank org Member",
      { orgLevel: LEVELS.VIEWER }, {}, true, true],
    ["R02", "lower-org equal-workspace-rank target is removable but not editable",
      {}, { org_level: LEVELS.VIEWER, ws_level: LEVELS.WORKSPACE_ADMIN }, false, true],
    ["R03", "higher-org equal-workspace-rank target is removable but not editable",
      { orgLevel: LEVELS.VIEWER }, { ws_level: LEVELS.WORKSPACE_ADMIN }, false, true],
    ["R04", "higher target workspace rank does not block removal",
      {}, { org_level: LEVELS.VIEWER, ws_level: LEVELS.OWNER }, false, true],
    ["R05", "unknown target workspace rank blocks editing only",
      {}, { ws_level: null }, false, true],
    ["R06", "workspace admin cannot manage an Org Admin target",
      {}, { org_level: LEVELS.ADMIN }, false, false],
    ["R08", "Org Admin cannot manage an Owner target from a workspace",
      { orgLevel: LEVELS.ADMIN }, { org_level: LEVELS.OWNER }, false, false],
    ["R09", "workspace admin cannot edit or remove themselves",
      {}, { id: "actor-1", email: "actor@example.test" }, false, false],
    ["R10", "workspace Member lacks removal authority even over a Viewer",
      { wsLevel: LEVELS.WORKSPACE_MEMBER },
      { org_level: LEVELS.VIEWER, ws_level: LEVELS.WORKSPACE_VIEWER }, false, false],
    ["R11", "workspace Viewer lacks removal authority",
      { wsLevel: LEVELS.WORKSPACE_VIEWER }, {}, false, false],
    ["R12", "unknown actor workspace rank does not grant authority",
      { wsLevel: null }, {}, false, false],
    ["R13", "Org Admin bypasses missing actor workspace rank",
      { orgLevel: LEVELS.ADMIN, wsLevel: null },
      { ws_level: LEVELS.WORKSPACE_ADMIN }, true, true],
    ["R14", "Owner bypasses lower actor workspace rank",
      { orgLevel: LEVELS.OWNER, wsLevel: LEVELS.WORKSPACE_VIEWER },
      { ws_level: LEVELS.WORKSPACE_ADMIN }, true, true],
    ["R15", "unknown target organization role remains denied",
      {}, { org_level: null }, false, false],
    ["R16", "unknown actor organization role remains denied",
      { orgLevel: null }, {}, false, false],
  ])("%s: %s", (_id, _case, actor, target, canEdit, canRemove) => {
    mocks.auth = { ...mocks.auth, ...actor };
    renderActions({ data: { ...teammate, ...target } });
    openActions();
    expectWorkspaceActions(canEdit, canRemove);
  });

  it("R07: an Owner sees edit/remove for an Org Admin target in a workspace, disabled and inert", () => {
    mocks.auth = { ...mocks.auth, orgLevel: LEVELS.OWNER };
    renderActions({ data: { ...teammate, org_level: LEVELS.ADMIN } });
    openActions();
    for (const title of ["Edit user info", "Remove from workspace"]) {
      const item = screen.getByText(title).closest('[aria-disabled="true"]');
      expect(item).not.toBeNull();
      fireEvent.click(item);
      fireEvent.keyDown(item, { key: "Enter" });
      expect(screen.queryByTestId("selected-member-action")).toBeNull();
    }
    expect(screen.queryByText("Remove from organization")).toBeNull();
  });

  it("R17: user-info role fallback allows an org Viewer to manage an org Member", () => {
    mocks.auth.orgLevel = undefined;
    mocks.auth.wsLevel = undefined;
    mocks.auth.user = {
      ...mocks.auth.user,
      org_level: LEVELS.VIEWER,
      ws_level: LEVELS.WORKSPACE_ADMIN,
    };
    renderActions();
    openActions();
    expectWorkspaceActions(true, true);
  });

  it("R18: removal-only permission enables the trigger and opens the scoped removal form", () => {
    renderActions({
      data: { ...teammate, org_level: LEVELS.VIEWER, ws_level: LEVELS.WORKSPACE_ADMIN },
    });
    openActions();
    expectWorkspaceActions(false, true);
    fireEvent.click(screen.getByText("Remove from workspace"));
    expect(screen.getByTestId("selected-member-action")).toHaveTextContent(
      "remove-ws-member:workspace-1",
    );
  });

  // On the Members page the page holds the open form, so a table reload,
  // which builds this cell again, does not close it
  // (members-dialog-survives-reload.test.jsx): the cell hands the chosen
  // action and its row to the page and renders no form itself.
  it("R18b: with the page's onOpenActionForm, the removal entry asks the page to open the form", () => {
    const onOpenActionForm = vi.fn();
    const data = {
      ...teammate,
      org_level: LEVELS.VIEWER,
      ws_level: LEVELS.WORKSPACE_ADMIN,
    };
    renderActions({ data, onOpenActionForm });
    openActions();
    expectWorkspaceActions(false, true);
    fireEvent.click(screen.getByText("Remove from workspace"));
    expect(onOpenActionForm).toHaveBeenCalledTimes(1);
    expect(onOpenActionForm).toHaveBeenCalledWith(
      expect.objectContaining({ action: "remove-ws-member" }),
      data,
    );
    expect(screen.queryByTestId("selected-member-action")).toBeNull();
  });

  it("R19: removal recomputes after target, actor authority, and scope changes", () => {
    const target = { ...teammate, ws_level: LEVELS.WORKSPACE_ADMIN };
    const { rerenderActions } = renderActions({ data: target });
    openActions();
    expectWorkspaceActions(false, true);

    rerenderActions({ data: { ...target, org_level: LEVELS.ADMIN } });
    expectWorkspaceActions(false, false);

    rerenderActions({ data: { ...target, email: "actor@example.test" } });
    expectWorkspaceActions(false, false);

    rerenderActions({ data: target });
    expectWorkspaceActions(false, true);

    mocks.auth.wsLevel = LEVELS.WORKSPACE_MEMBER;
    rerenderActions({ data: target });
    expectWorkspaceActions(false, false);

    mocks.auth.wsLevel = LEVELS.WORKSPACE_ADMIN;
    rerenderActions({ data: target, workspaceScope: false });
    expectWorkspaceActions(false, false);

    rerenderActions({ data: target });
    expectWorkspaceActions(false, true);
  });

  it("R20: an explicit canRemove denial does not hide the workspace edit action", () => {
    renderMenu({ canEdit: true, canRemove: false });
    expectWorkspaceActions(true, false);
  });

  it("R21: canRemove never grants organization actions independently of canEdit", () => {
    renderMenu({ canEdit: false, canRemove: true, menusByStatus: undefined });
    expect(screen.queryByText("Edit user info")).toBeNull();
    expect(screen.queryByText("Remove from organization")).toBeNull();
    expect(screen.queryByText("Remove from workspace")).toBeNull();
  });

  it("R22: callers omitting canRemove retain the existing canEdit fallback", () => {
    renderMenu({ canEdit: false });
    expectWorkspaceActions(false, false);
  });
});
