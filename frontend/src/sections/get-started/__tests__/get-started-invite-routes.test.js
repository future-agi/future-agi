import { matchRoutes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { dashboardRoutes } from "src/routes/sections/dashboard";
import RoleProtection from "src/routes/components/role-protection";
import WorkspaceRoleProtection from "src/routes/components/workspace-role-protection";
import { paths } from "src/routes/paths";
import * as inviteAccess from "../useInviteAccess";

// The invite step restates who the two Members routes let in, because
// neither guard has a helper to import. These rows read the route table the
// router builds and fail when a route's allowed roles and the step's list
// differ, so the copy cannot drift.

// The route the router matches for a path, with the guard around its page.
const guardOn = (path) => {
  // Every route registered: workspaces on, as for a signed-out build.
  const leaf = matchRoutes(dashboardRoutes(null, null), path)?.at(-1);
  expect(leaf?.route.path).not.toBe("*");
  return leaf?.route.element;
};

const sorted = (roles) => [...(roles ?? [])].sort();

describe("Get started: the Members routes the invite step names", () => {
  it("the organization Members route lets in exactly the roles the step reads", () => {
    const guard = guardOn(paths.dashboard.settings.manageteam);

    expect(guard?.type).toBe(RoleProtection);
    expect(inviteAccess.MEMBERS_ROLES).toBeDefined();
    expect(sorted(guard?.props.allowedRoles)).toEqual(
      sorted(inviteAccess.MEMBERS_ROLES),
    );
  });

  it("the workspace Members route the step links to lets in exactly the roles the step reads", () => {
    expect(typeof inviteAccess.workspaceMembersPath).toBe("function");
    const guard = guardOn(inviteAccess.workspaceMembersPath("ws-1"));

    expect(guard?.type).toBe(WorkspaceRoleProtection);
    expect(inviteAccess.WORKSPACE_MEMBERS_ROLES).toBeDefined();
    expect(sorted(guard?.props.allowedRoles)).toEqual(
      sorted(inviteAccess.WORKSPACE_MEMBERS_ROLES),
    );
  });
});
