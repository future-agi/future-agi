import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";

// Review comment 7 on PR #2000: the org-switch branch re-points the stored
// workspace at the new org, which makes readSessionWorkspaceForOrg accept it.
// The previous org's workspaceRole / wsLevel must not ride along.

const h = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: {
    defaults: { headers: { common: {} } },
    get: vi.fn(),
    post: h.post,
  },
  endpoints: {
    organizations: {
      switch: "/organizations/switch/",
      list: "/organizations/",
    },
  },
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
// No authenticated user, so the seeding effect stays out of the way.
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: null, authenticated: false, loading: false }),
}));

import { OrganizationProvider, useOrganization } from "../OrganizationContext";

const ORG_A = "org-aaaa";
const ORG_B = "org-bbbb";

const wrapper = ({ children }) => (
  <OrganizationProvider>{children}</OrganizationProvider>
);

beforeEach(() => {
  sessionStorage.clear();
  h.post.mockReset();
  // the switch ends in a hard reload; jsdom cannot navigate
  Object.defineProperty(window, "location", {
    writable: true,
    value: { assign: vi.fn(), href: "http://localhost/" },
  });
});

const seedOrgAWorkspace = () => {
  sessionStorage.setItem("organizationId", ORG_A);
  sessionStorage.setItem("workspaceId", "ws-a");
  sessionStorage.setItem("workspaceName", "A workspace");
  sessionStorage.setItem("workspaceDisplayName", "A workspace");
  sessionStorage.setItem("workspaceRole", "Owner");
  sessionStorage.setItem("wsLevel", "15");
  sessionStorage.setItem("workspaceOrgId", ORG_A);
};

describe("switchOrganization — workspace permissions do not survive the switch", () => {
  it("replaces the workspace role with the new org's and drops the stale level", async () => {
    seedOrgAWorkspace();
    h.post.mockResolvedValue({
      data: {
        result: {
          organization: { id: ORG_B, name: "B", display_name: "B" },
          org_role: "Member",
          org_level: 3,
          workspace_role: "workspace_member",
          workspace: {
            id: "ws-b",
            name: "B workspace",
            display_name: "B workspace",
          },
        },
      },
    });

    const { result } = renderHook(() => useOrganization(), { wrapper });
    await act(async () => {
      await result.current.switchOrganization(ORG_B);
    });

    expect(sessionStorage.getItem("workspaceOrgId")).toBe(ORG_B);
    expect(sessionStorage.getItem("workspaceRole")).toBe("workspace_member");
    // No ws_level in the switch payload — better absent than org A's 15.
    expect(sessionStorage.getItem("wsLevel")).toBe(null);
  });

  it("clears the workspace role when the new org supplies none", async () => {
    seedOrgAWorkspace();
    h.post.mockResolvedValue({
      data: {
        result: {
          organization: { id: ORG_B, name: "B", display_name: "B" },
          org_role: null,
          org_level: null,
          workspace_role: null,
          workspace: {
            id: "ws-b",
            name: "B workspace",
            display_name: "B workspace",
          },
        },
      },
    });

    const { result } = renderHook(() => useOrganization(), { wrapper });
    await act(async () => {
      await result.current.switchOrganization(ORG_B);
    });

    expect(sessionStorage.getItem("workspaceOrgId")).toBe(ORG_B);
    expect(sessionStorage.getItem("workspaceRole")).toBe(null);
    expect(sessionStorage.getItem("wsLevel")).toBe(null);
  });

  it("still clears everything when the new org has no workspace", async () => {
    seedOrgAWorkspace();
    h.post.mockResolvedValue({
      data: {
        result: {
          organization: { id: ORG_B, name: "B", display_name: "B" },
          org_role: "Member",
          org_level: 3,
        },
      },
    });

    const { result } = renderHook(() => useOrganization(), { wrapper });
    await act(async () => {
      await result.current.switchOrganization(ORG_B);
    });

    ["workspaceId", "workspaceRole", "wsLevel", "workspaceOrgId"].forEach((k) =>
      expect(sessionStorage.getItem(k)).toBe(null),
    );
  });
});
