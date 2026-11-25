import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { ROLES } from "src/utils/rolePermissionMapping";

vi.unmock("src/sections/simulate/environments/hooks/useSimulationAccess");

const auth = vi.hoisted(() => ({ role: null }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: auth.role }),
}));

const { default: useSimulationAccess } = await import("../useSimulationAccess");

const accessFor = (role) => {
  auth.role = role;
  return renderHook(() => useSimulationAccess()).result.current;
};

describe("useSimulationAccess", () => {
  it.each([ROLES.VIEWER, ROLES.WORKSPACE_VIEWER])(
    "gives a %s no edit or run",
    (role) => {
      expect(accessFor(role)).toEqual({
        canEdit: false,
        canRun: false,
        canUnlockRelease: false,
      });
    },
  );

  it.each([
    ROLES.OWNER,
    ROLES.ADMIN,
    ROLES.MEMBER,
    ROLES.WORKSPACE_ADMIN,
    ROLES.WORKSPACE_MEMBER,
  ])("lets a %s edit and run", (role) => {
    expect(accessFor(role)).toMatchObject({ canEdit: true, canRun: true });
  });

  it.each([ROLES.OWNER, ROLES.ADMIN, ROLES.WORKSPACE_ADMIN])(
    "lets a %s unlock a release",
    (role) => {
      expect(accessFor(role).canUnlockRelease).toBe(true);
    },
  );

  it.each([ROLES.MEMBER, ROLES.WORKSPACE_MEMBER])(
    "keeps a %s from unlocking a release",
    (role) => {
      expect(accessFor(role).canUnlockRelease).toBe(false);
    },
  );

  it("gives an unknown role nothing", () => {
    expect(accessFor(undefined)).toEqual({
      canEdit: false,
      canRun: false,
      canUnlockRelease: false,
    });
  });
});
