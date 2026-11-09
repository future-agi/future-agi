import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { dashboardRoutes } from "../dashboard";
import { useNavData } from "src/layouts/dashboard/config-navigation";

const identity = vi.hoisted(() => ({ user: {}, workspaceRole: null }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: identity.user }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: identity.workspaceRole }),
}));
vi.mock("src/sections/omega/shell/useOmegaProducts", () => ({
  useOmegaProducts: () => ({}),
}));
vi.mock("src/hooks/useRestrictSimulateToEnvironments", () => ({
  useRestrictSimulateToEnvironments: () => false,
}));
vi.mock("src/routes/hooks", () => ({
  usePathname: () => "/dashboard/keys",
  useRouter: () => ({}),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: () => {},
  Events: {},
  PropertyName: {},
}));

afterEach(() => vi.unstubAllEnvs());
const roles = [
  ["Owner", "workspace_admin"],
  ["Admin", "workspace_admin"],
  ["Member", "workspace_member"],
  ["Viewer", "workspace_member"],
  ["Viewer", "workspace_viewer"],
];
describe("personal Keys navigation", () => {
  it.each(roles)(
    "registers the Keys page for %s with %s",
    (organizationRole, workspaceRole) => {
      const [dashboard] = dashboardRoutes(
        { organization_role: organizationRole },
        workspaceRole,
      );
      expect(
        dashboard.children.filter(({ path }) => path === "keys"),
      ).toHaveLength(1);
      expect(
        dashboard.children.find(({ path }) => path === "keys").element,
      ).toBeTruthy();
    },
  );
  it.each(roles)(
    "advertises the same Keys page for %s with %s",
    (organizationRole, workspaceRole) => {
      vi.stubEnv("VITE_OMEGA_CONSOLE", "");
      identity.user = { organization_role: organizationRole };
      identity.workspaceRole = workspaceRole;
      const { result } = renderHook(() => useNavData());
      const keys = result.current
        .flatMap(({ items }) => items)
        .filter(({ path }) => path === "/dashboard/keys");
      expect(keys).toHaveLength(1);
      expect(keys[0].title).toBe("Keys");
    },
  );
});
