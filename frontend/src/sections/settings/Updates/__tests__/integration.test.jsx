import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, renderHook, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import DashboardLayout from "src/layouts/dashboard";
import { useNavSettingsData } from "src/layouts/dashboard/config-navigation";
import { paths } from "src/routes/paths";
import { responseFixture } from "src/api/hostUpdates/__tests__/fixtures";

const mocks = vi.hoisted(() => ({
  allowed: true,
  role: "Owner",
  data: null,
  layout: "vertical",
}));
vi.mock("src/api/hostUpdates/host-update-status", () => ({
  useHostUpdateAccess: () => ({ allowed: mocks.allowed }),
  useHostUpdateStatus: () => ({ data: mocks.allowed ? mocks.data : undefined }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { organization_role: mocks.role } }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: "workspace_admin" }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: false }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
  useWorkspaceFromList: vi.fn(),
}));
vi.mock("src/hooks/useRestrictSimulateToEnvironments", () => ({
  useRestrictSimulateToEnvironments: () => false,
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeLayout: mocks.layout }),
}));
vi.mock("src/hooks/useCapabilities", () => ({
  useFeatureAllowed: () => ({ allowed: false }),
}));
vi.mock("src/hooks/use-responsive", () => ({ useResponsive: () => true }));
vi.mock("src/routes/hooks", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/dashboard/develop",
}));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({
  default: (select) =>
    select({ pendingNavigation: null, clearPendingNavigation: vi.fn() }),
}));
vi.mock("src/layouts/dashboard/nav-mini", () => ({ default: () => null }));
vi.mock("src/layouts/dashboard/nav-horizontal", () => ({
  default: () => null,
}));
vi.mock("src/layouts/dashboard/nav-vertical", () => ({ default: () => null }));
vi.mock("src/layouts/dashboard/NavGatewayPanel", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/FalconAISidebar", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/FalconAIFab", () => ({
  default: () => null,
}));
vi.mock("src/layouts/dashboard/WorkspaceSwitcher/CreateWorkspaceModal", () => ({
  default: () => null,
}));
vi.mock("src/components/two-factor-enforcement/TwoFactorBanner", () => ({
  default: () => <div>Security message</div>,
}));

beforeEach(() => {
  mocks.allowed = true;
  mocks.role = "Owner";
  mocks.data = responseFixture().result;
  mocks.data.snapshot.discovery.state = "available";
});
afterEach(cleanup);

describe("live shell and navigation integration", () => {
  it.each(["vertical", "horizontal", "mini"])(
    "renders a single notice in the live %s shell after security messaging",
    (layout) => {
      mocks.layout = layout;
      render(
        <MemoryRouter>
          <DashboardLayout>
            <div>Ordinary app content</div>
          </DashboardLayout>
        </MemoryRouter>,
      );
      expect(
        screen.getAllByRole("link", { name: "View update details" }),
      ).toHaveLength(1);
      expect(screen.getByRole("main")).toContainElement(
        screen.getByRole("status"),
      );
      expect(
        screen
          .getByText("Security message")
          .compareDocumentPosition(screen.getByRole("status")) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      expect(screen.getByText("Ordinary app content")).toBeInTheDocument();
    },
  );
  it("adds an Updates destination to live settings navigation", () => {
    const { result } = renderHook(useNavSettingsData);
    const updates = result.current
      .flatMap((section) => section.items)
      .find((item) => item.title === "Updates");
    expect(updates.path).toBe(paths.dashboard.settings.updates);
  });
  it.each(["Owner", "Admin", "Member", "workspace_admin"])(
    "does not expose the Updates item when strict access is denied for %s",
    (role) => {
      mocks.role = role;
      mocks.allowed = false;
      const { result } = renderHook(useNavSettingsData);
      expect(
        result.current
          .flatMap((section) => section.items)
          .find((item) => item.title === "Updates"),
      ).toBeUndefined();
    },
  );
  it("does not obstruct normal app rendering when reporting is unavailable", () => {
    mocks.data = { availability: "unavailable", snapshot: null };
    render(
      <MemoryRouter>
        <DashboardLayout>
          <div>Ordinary app content</div>
        </DashboardLayout>
      </MemoryRouter>,
    );
    expect(screen.getByText("Ordinary app content")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
