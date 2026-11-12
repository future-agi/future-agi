import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import WorkspaceSwitcher from "../WorkspaceSwitcher";

const h = vi.hoisted(() => ({ user: null, open: vi.fn() }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: h.user, logout: vi.fn() }),
}));
vi.mock("src/auth/sign-out", () => ({ signOutToSignIn: vi.fn() }));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceId: "ws",
    currentWorkspaceRole: "Owner",
  }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    currentOrganizationDisplayName: "Signed-in org",
    currentOrganizationRole: "Owner",
  }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));
// WorkspaceSwitcher reads the deployment mode through react-query; this test
// renders without a QueryClientProvider, so pin the hook.
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: false, isSuccess: true }),
}));
vi.mock("react-router", () => ({
  useLocation: () => ({ pathname: "/dashboard/develop" }),
  useNavigate: () => vi.fn(),
}));
vi.mock("../../states", () => ({
  useSettingsOpen: { setState: vi.fn() },
  useCreateWorkspaceModal: { getState: () => ({ setOpen: vi.fn() }) },
  useCreateOrganizationModal: { getState: () => ({ setOpen: h.open }) },
}));
vi.mock("../SelectWorkspace", () => ({ default: () => null }));
vi.mock("../SelectOrganization", () => ({ default: () => null }));
vi.mock("../ThemeSelector", () => ({ default: () => null }));
vi.mock("../CreateOrganizationModal", () => ({
  default: () => <div data-testid="org-create-modal" />,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));
vi.mock("src/components/custom-dialog", () => ({ ConfirmDialog: () => null }));
vi.mock("src/pages/dashboard/settings/UserManagementV2/AllActionForm", () => ({
  default: () => null,
}));
afterEach(cleanup);

describe("J-AUTH-WSL-002 organization creation capability", () => {
  it.each([false, true])(
    "respects can_create_organization=%s in both the control and modal",
    (allowed) => {
      h.user = { can_create_organization: allowed };
      render(<WorkspaceSwitcher />);
      fireEvent.click(screen.getByTestId("workspace-switcher"));
      // The control is never removed (owner rule): a SAML-scoped session sees
      // it disabled with the reason, everyone else can use it.
      const add = screen.getByRole("button", { name: /^Create organization/ });
      expect(add.getAttribute("aria-disabled") === "true").toBe(!allowed);
      if (!allowed) {
        expect(add.getAttribute("aria-label")).toContain(
          "Creating organizations isn't available in single sign-on sessions.",
        );
        fireEvent.click(add);
        expect(h.open).not.toHaveBeenCalled();
      }
      expect(Boolean(screen.queryByTestId("org-create-modal"))).toBe(allowed);
    },
  );
});
