import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

import { useNavSettingsData } from "../config-navigation";

// Settings lists Integrations for the roles that manage integrations
// (futureagi/integrations/permissions.py): an organization Owner or Admin, and
// an Admin of the current workspace, whose organization role is Member. A
// Member or Viewer of the workspace gets no entry.

const h = vi.hoisted(() => ({ orgRole: "Owner", workspaceRole: null }));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { organization_role: h.orgRole, ws_enabled: false },
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: h.workspaceRole }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: false, isOSS: true, isEE: false }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
  useWorkspaceFromList: () => ({ workspace: null }),
}));
// The settings navigation reads the current path (as in the sibling Falcon
// connectors test).
vi.mock("src/routes/hooks", () => ({
  usePathname: () => "/dashboard/settings/integrations",
}));
vi.mock("src/sections/settings/BillingV2/useCentralBilling", () => ({
  useCentralBilling: () => ({ visible: false }),
}));
vi.mock("src/sections/omega/shell/useEarlyAccess", async (importOriginal) => ({
  ...(await importOriginal()),
  useEarlyAccess: () => ({ data: undefined, isLoading: false, isError: false }),
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  // No capability is allowed, and nothing is loading (as in the sibling
  // Falcon connectors test, which also stubs the capability read).
  useCapabilities: () => ({
    data: { features: {} },
    isLoading: false,
    isError: false,
  }),
  useFeatureLocked: () => ({ locked: true, isLoading: false, isError: false }),
  useFeatureAllowed: () => ({
    allowed: false,
    reasonCode: null,
    isLoading: false,
    isError: false,
  }),
}));

const integrationsEntry = () => {
  const { result } = renderHook(() => useNavSettingsData());
  return result.current
    .flatMap((section) => section.items)
    .filter((item) => item.title === "Integrations");
};

beforeEach(() => {
  h.orgRole = "Owner";
  h.workspaceRole = null;
});

describe("Settings navigation: Integrations", () => {
  it.each([
    ["Owner", null],
    ["Admin", null],
    ["Member", "workspace_admin"],
  ])("is listed for org %s, workspace %s", (orgRole, workspaceRole) => {
    h.orgRole = orgRole;
    h.workspaceRole = workspaceRole;
    expect(integrationsEntry()).toEqual([
      expect.objectContaining({ path: "/dashboard/settings/integrations" }),
    ]);
  });

  it.each([
    ["Member", "workspace_member"],
    ["Member", null],
    ["Viewer", "workspace_viewer"],
  ])("is not listed for org %s, workspace %s", (orgRole, workspaceRole) => {
    h.orgRole = orgRole;
    h.workspaceRole = workspaceRole;
    expect(integrationsEntry()).toEqual([]);
  });
});
