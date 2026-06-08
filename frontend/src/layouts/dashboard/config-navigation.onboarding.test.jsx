import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { paths } from "src/routes/paths";
import {
  useNavDashBoardData,
  useNavData,
} from "src/layouts/dashboard/config-navigation";

const mocks = vi.hoisted(() => ({
  currentWorkspaceRole: "workspace_admin",
  isOSS: true,
  user: {
    default_workspace_role: "workspace_admin",
    getStartedCompleted: false,
    organization_role: "Owner",
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: mocks.user }),
}));

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: mocks.currentWorkspaceRole }),
}));

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: mocks.isOSS }),
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: { click: "click", source: "source" },
  trackEvent: vi.fn(),
}));

vi.mock("src/components/svg-color", () => ({
  default: ({ src }) => <span data-icon={src} />,
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));

describe("dashboard navigation onboarding state", () => {
  it("uses Home, not Get Started, as the first-run dashboard entry", () => {
    mocks.user = { ...mocks.user, getStartedCompleted: false };

    const { result } = renderHook(() => useNavDashBoardData());

    expect(result.current[0].items).toEqual([
      expect.objectContaining({
        path: paths.dashboard.home,
        title: "Home",
      }),
    ]);
  });

  it("does not show unavailable Falcon navigation in OSS", () => {
    mocks.isOSS = true;

    const { result } = renderHook(() => useNavData());
    const titles = result.current.flatMap((section) =>
      section.items.map((item) => item.title),
    );

    expect(titles).not.toContain("Falcon AI");
  });

  it("keeps Falcon navigation available outside OSS", () => {
    mocks.isOSS = false;

    const { result } = renderHook(() => useNavData());
    const titles = result.current.flatMap((section) =>
      section.items.map((item) => item.title),
    );

    expect(titles).toContain("Falcon AI");
  });
});
