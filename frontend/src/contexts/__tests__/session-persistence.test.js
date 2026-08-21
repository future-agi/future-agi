import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";

// These cover the session-storage layer behind review comments 6, 7 and 8 on
// PR #2000. All three are the same defect: each key is written only when the
// incoming value is truthy, so a missing value leaves the previous org's data
// in place instead of clearing it.

vi.mock("src/utils/axios", () => ({
  default: {
    defaults: { headers: { common: {} } },
    get: vi.fn(),
    post: vi.fn(),
  },
  endpoints: {
    workspaces: { list: "/workspaces/", switch: "/workspaces/switch/" },
    organizations: {},
  },
}));

vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({}) }));
// The provider reads the org context; leaving it unsettled is what makes the
// pinned org the only remaining source for the switch payload.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: null, isReady: false }),
}));

import axios from "src/utils/axios";
import {
  readSessionWorkspaceForOrg,
  writeSessionWorkspace,
  useWorkspace,
  WorkspaceProvider,
} from "../WorkspaceContext";
import { pinResolvedOrganization } from "src/auth/context/jwt/auth-provider";

// switchWorkspace ends in a hard reload, which jsdom cannot perform.
const assigned = [];
vi.stubGlobal("location", { assign: (u) => assigned.push(u), href: "/" });

const ORG_A = "org-aaaa";
const ORG_B = "org-bbbb";

beforeEach(() => {
  sessionStorage.clear();
});

describe("comment 8 — a workspace saved without an org id is silently discarded", () => {
  it("keeps the workspace when the org id is known", () => {
    writeSessionWorkspace({
      id: "ws-1",
      name: "Analytics",
      displayName: "Analytics",
      role: "Owner",
      wsLevel: 15,
      orgId: ORG_A,
    });
    expect(readSessionWorkspaceForOrg(ORG_A)?.id).toBe("ws-1");
  });

  it("loses the workspace when the org id was not resolved at switch time", () => {
    // WorkspaceContext computes `currentOrganizationId || workspace.orgId || null`.
    // A null there removes workspaceOrgId, and the reader rejects the row on the
    // next load — the user lands back on their default workspace, no error shown.
    writeSessionWorkspace({
      id: "ws-1",
      name: "Analytics",
      displayName: "Analytics",
      role: "Owner",
      wsLevel: 15,
      orgId: null,
    });
    expect(sessionStorage.getItem("workspaceOrgId")).toBe(null);
    expect(readSessionWorkspaceForOrg(ORG_A)).toBe(null);
  });
});

describe("comment 7 — a workspace row must not carry another org's role", () => {
  it("rejects a stored workspace belonging to a different org", () => {
    writeSessionWorkspace({
      id: "ws-a",
      name: "A",
      displayName: "A",
      role: "Owner",
      wsLevel: 15,
      orgId: ORG_A,
    });
    expect(readSessionWorkspaceForOrg(ORG_B)).toBe(null);
  });
});

describe("comment 6 — pinning a new org must not leave the previous org's details", () => {
  it("writes the org details when they are supplied", () => {
    pinResolvedOrganization({
      organization: { id: ORG_A, name: "A", display_name: "A" },
      organization_role: "Owner",
      org_level: 15,
    });
    expect(sessionStorage.getItem("organizationId")).toBe(ORG_A);
    expect(sessionStorage.getItem("orgLevel")).toBe("15");
  });

  it("clears the previous org's role and level when the new org supplies none", () => {
    pinResolvedOrganization({
      organization: { id: ORG_A, name: "A", display_name: "A" },
      organization_role: "Owner",
      org_level: 15,
    });

    // Membership in A is deactivated; the backend resolves B and returns no
    // level for it. Org A's "Owner"/15 must not survive into org B.
    pinResolvedOrganization({
      organization: { id: ORG_B, name: "B", display_name: "B" },
      organization_role: null,
      org_level: null,
    });

    expect(sessionStorage.getItem("organizationId")).toBe(ORG_B);
    expect(sessionStorage.getItem("organizationName")).not.toBe("A");
    expect(sessionStorage.getItem("organizationRole")).not.toBe("Owner");
    expect(sessionStorage.getItem("orgLevel")).not.toBe("15");
  });
});

describe("comment 8 — the tab's pinned org is the last-resort owner", () => {
  beforeEach(() => {
    axios.post.mockReset();
    assigned.length = 0;
  });

  it("keeps the workspace when only sessionStorage knows the org", async () => {
    // Driven through switchWorkspace itself: inlining its orgId chain here
    // would assert only that writeSessionWorkspace stores what it is handed,
    // and would still pass with readSessionOrgId() removed from the chain.
    axios.post.mockResolvedValue({
      data: {
        workspace: { id: "ws-1", name: "Analytics", display_name: "Analytics" },
        user_role: "Owner",
      },
    });

    // The org context has not settled and no workspace row exists, so the
    // first two links of the chain are null — only the pinned org is left.
    sessionStorage.setItem("organizationId", ORG_A);

    const { result } = renderHook(() => useWorkspace(), {
      wrapper: WorkspaceProvider,
    });
    await act(() => result.current.switchWorkspace("ws-1", "ws-0"));

    expect(sessionStorage.getItem("workspaceOrgId")).toBe(ORG_A);
    expect(readSessionWorkspaceForOrg(ORG_A)?.id).toBe("ws-1");
    // The switch ends in a hard reload; without an owner on the row the
    // reader rejects it and the tab reseeds from the default workspace.
    expect(assigned).toEqual(["/dashboard/develop"]);
  });
});
