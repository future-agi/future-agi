import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

import ErrorMetadataPanel from "../ErrorMetadataPanel";

// An Error Feed issue's Linear row offers each role only what it can do:
// Connect to an Owner, Admin or workspace Admin (who manage integrations),
// Create issue to every role with write access to the workspace. A Member
// sees Connect disabled with the reason, a Viewer both, and pressing them
// does nothing.

const h = vi.hoisted(() => ({
  role: "Owner",
  linear: { connected: false, teams: [] },
  navigate: vi.fn(),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: h.role }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-1" }),
}));
vi.mock("src/api/errorFeed/error-feed", () => ({
  useLinearTeams: () => ({ data: h.linear, isLoading: false, isError: false }),
  useCreateLinearIssue: () => ({ mutate: vi.fn(), isPending: false }),
  useErrorFeedSidebar: () => ({ data: null, isLoading: false }),
  useUpdateErrorFeedIssue: () => ({ mutate: vi.fn() }),
}));
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useOrgMembers: () => ({ data: [] }),
}));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => h.navigate,
}));

const action = (name) => screen.getByRole("button", { name });

beforeEach(() => {
  h.navigate.mockClear();
});

describe("The issue's Linear row", () => {
  it.each(["Owner", "Admin", "workspace_admin"])(
    "offers %s Connect, which opens Settings > Integrations",
    async (role) => {
      h.role = role;
      h.linear = { connected: false, teams: [] };
      render(<ErrorMetadataPanel error={{ cluster_id: "cl-1" }} />);
      expect(action("Connect")).not.toHaveAttribute("aria-disabled");
      await userEvent.setup().click(action("Connect"));
      expect(h.navigate).toHaveBeenCalledWith(
        "/dashboard/settings/integrations",
      );
    },
  );

  it.each([
    ["Member", "Only an Owner or Admin can connect Linear."],
    ["workspace_member", "Only an Owner or Admin can connect Linear."],
    ["Viewer", "Only an Owner or Admin can connect Linear."],
    ["workspace_viewer", "Only an Owner or Admin can connect Linear."],
  ])("shows %s Connect disabled with the reason", async (role, reason) => {
    const user = userEvent.setup();
    h.role = role;
    h.linear = { connected: false, teams: [] };
    render(<ErrorMetadataPanel error={{ cluster_id: "cl-1" }} />);
    expect(action("Connect")).toHaveAttribute("aria-disabled", "true");
    await user.click(action("Connect"));
    expect(h.navigate).not.toHaveBeenCalled();
    await user.hover(action("Connect"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(reason);
  });

  it.each(["Owner", "Member", "workspace_member"])(
    "offers %s Create issue once Linear is connected",
    (role) => {
      h.role = role;
      h.linear = {
        connected: true,
        teams: [{ id: "t", key: "E2E", name: "E2E" }],
      };
      render(<ErrorMetadataPanel error={{ cluster_id: "cl-1" }} />);
      expect(action("Create issue")).not.toHaveAttribute("aria-disabled");
    },
  );

  it("shows a Viewer Create issue disabled with the reason", async () => {
    const user = userEvent.setup();
    h.role = "Viewer";
    h.linear = {
      connected: true,
      teams: [{ id: "t", key: "E2E", name: "E2E" }],
    };
    render(<ErrorMetadataPanel error={{ cluster_id: "cl-1" }} />);
    expect(action("Create issue")).toHaveAttribute("aria-disabled", "true");
    await user.click(action("Create issue"));
    expect(screen.queryByText("Create Linear Issue")).toBeNull();
    await user.hover(action("Create issue"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Viewers cannot create issues.",
    );
  });
});
