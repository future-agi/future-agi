/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import AllActionForm from "../AllActionForm";

// The Members table names its columns "Organization role" and "Workspace
// role". The confirmation dialogs (remove a member, cancel an invite, and the
// others built on the same dialog) show the person's roles with the same
// names, not "Org Role:" and "WS Role:".

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), delete: vi.fn() },
  endpoints: { rbac: {}, workspace: {} },
}));
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: true, isSuccess: true }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { organization_role: "Owner" },
    orgLevel: 15,
  }),
}));
vi.mock("../UserManagementStore", () => ({
  useUserManagementStore: () => ({ usersList: [] }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

afterEach(cleanup);

const member = {
  id: "00000000-0000-4000-8000-000000000001",
  type: "member",
  name: "Sam Lee",
  email: "sam@acme.test",
  status: "Active",
  org_role: "Member",
  ws_role: "Workspace Admin",
};
const invite = {
  ...member,
  id: "00000000-0000-4000-8000-000000000002",
  type: "invite",
  name: "",
  email: "new@acme.test",
  status: "Pending",
  ws_role: "Workspace Member",
};

describe("Role labels in the member dialogs", () => {
  it.each([
    ["remove-member", member, "Are you sure you want to remove this member?"],
    ["cancel-invite", invite, "Are you sure you want to cancel this invite?"],
  ])(
    "the %s dialog says 'Organization role:' and 'Workspace role:'",
    (action, userData, title) => {
      render(
        <AllActionForm
          openActionForm={{ action }}
          onClose={vi.fn()}
          userData={userData}
          workspaceId="w1"
        />,
      );
      const dialog = screen.getByRole("dialog");
      expect(within(dialog).getByText(title)).toBeInTheDocument();
      expect(
        within(dialog).getByText("Organization role:"),
      ).toBeInTheDocument();
      expect(within(dialog).getByText("Workspace role:")).toBeInTheDocument();
      expect(within(dialog).getByText(userData.ws_role)).toBeInTheDocument();
      expect(within(dialog).queryByText("Org Role:")).toBeNull();
      expect(within(dialog).queryByText("WS Role:")).toBeNull();
    },
  );
});
