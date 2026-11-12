import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

import { useNavSettingsData } from "../config-navigation";

// Settings lists "Falcon AI Connectors" only on a deployment whose
// capabilities allow Falcon AI (hosted sets features.falcon_ai.allowed). Every
// other build, and a deployment whose capabilities are still loading or failed
// to load, shows no such item.

const h = vi.hoisted(() => ({
  role: "Owner",
  falcon: { allowed: false, isLoading: false, isError: false },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { organization_role: h.role, ws_enabled: false },
  }),
}));

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceRole: h.role.startsWith("workspace_") ? h.role : null,
  }),
}));

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: false, isOSS: true, isEE: false }),
}));

vi.mock("src/routes/hooks", () => ({
  usePathname: () => "/dashboard/settings/integrations",
}));

vi.mock("src/sections/settings/BillingV2/useCentralBilling", () => ({
  useCentralBilling: () => ({ visible: false }),
}));

vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
  useWorkspaceFromList: () => ({ workspace: null }),
}));

vi.mock("src/hooks/useCapabilities", async (importOriginal) => {
  const actual = /** @type {typeof import("src/hooks/useCapabilities")} */ (
    await importOriginal()
  );
  return {
    ...actual,
    useCapabilities: () => ({
      data: { features: { falcon_ai: { allowed: h.falcon.allowed } } },
      isLoading: h.falcon.isLoading,
      isError: h.falcon.isError,
    }),
    useFeatureLocked: () => ({
      locked: true,
      isLoading: false,
      isError: false,
    }),
    useFeatureAllowed: (feature) =>
      feature === actual.CAPABILITY.FALCON_AI
        ? { reasonCode: null, ...h.falcon }
        : {
            allowed: false,
            reasonCode: null,
            isLoading: false,
            isError: false,
          },
  };
});

vi.mock("src/sections/omega/shell/useEarlyAccess", async (importOriginal) => ({
  .../** @type {typeof import("src/sections/omega/shell/useEarlyAccess")} */ (
    await importOriginal()
  ),
  useEarlyAccess: () => ({ data: undefined, isLoading: false, isError: false }),
}));

const settingsTitles = () => {
  const { result } = renderHook(() => useNavSettingsData());
  return result.current.flatMap((section) =>
    section.items.map((item) => item.title),
  );
};

beforeEach(() => {
  h.role = "Owner";
  h.falcon = { allowed: false, isLoading: false, isError: false };
});

describe("Settings navigation: Falcon AI Connectors", () => {
  it.each(["Owner", "Admin", "workspace_admin"])(
    "is not listed for %s when the deployment does not allow Falcon AI",
    (role) => {
      h.role = role;
      const titles = settingsTitles();
      expect(titles).toContain("Integrations");
      expect(titles).not.toContain("Falcon AI connectors");
    },
  );

  it("is not listed while capabilities are loading", () => {
    h.falcon = { allowed: false, isLoading: true, isError: false };
    expect(settingsTitles()).not.toContain("Falcon AI connectors");
  });

  it("is not listed when capabilities failed to load", () => {
    h.falcon = { allowed: false, isLoading: false, isError: true };
    expect(settingsTitles()).not.toContain("Falcon AI connectors");
  });

  it.each(["Owner", "Admin", "workspace_admin"])(
    "is listed for %s when the deployment allows Falcon AI",
    (role) => {
      h.role = role;
      h.falcon = { allowed: true, isLoading: false, isError: false };
      expect(settingsTitles()).toContain("Falcon AI connectors");
    },
  );

  it("stays off the list for a Member even when Falcon AI is allowed", () => {
    h.role = "Member";
    h.falcon = { allowed: true, isLoading: false, isError: false };
    expect(settingsTitles()).not.toContain("Falcon AI connectors");
  });
});
