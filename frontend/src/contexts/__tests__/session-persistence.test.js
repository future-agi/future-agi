import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";

// These cover the per-tab session-storage layer: which org and workspace a tab
// is pinned to, and when that pin is allowed to survive. Most of them are the
// same defect seen from different angles — each key is written only when the
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
// Mutable so a test can drive the auth transitions the provider reacts to
// (login -> logout -> a different user logging in in the same tab). Defaults to
// an unsettled context, which is what the switch tests below expect.
const auth = vi.hoisted(() => ({ current: {} }));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => auth.current }));
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
  auth.current = {};
});

const signedIn = (orgId, ws) => ({
  authenticated: true,
  loading: false,
  user: {
    organization: { id: orgId },
    default_workspace_id: ws.id,
    default_workspace_name: ws.name,
    default_workspace_display_name: ws.name,
    default_workspace_role: ws.role,
    ws_level: ws.wsLevel,
  },
});

const signedOut = { authenticated: false, loading: false, user: null };

describe("a workspace saved without an org id is silently discarded", () => {
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

describe("a workspace row must not carry another org's role", () => {
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

describe("pinning a new org must not leave the previous org's details", () => {
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

describe("the tab's pinned org is the last-resort owner", () => {
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

describe("logging out must leave nothing for the next user in the tab", () => {
  // sessionStorage survives navigation within a tab and logout() itself only
  // removes the 2FA and user-id keys, so the org and workspace rows are cleared
  // by the providers reacting to the auth drop. Without that, the next user to
  // log in in the same tab inherits the previous user's workspace.
  it("clears the workspace row when auth drops", () => {
    auth.current = signedIn(ORG_A, {
      id: "ws-a",
      name: "Alpha Default",
      role: "Owner",
      wsLevel: 15,
    });
    const { rerender } = renderHook(() => useWorkspace(), {
      wrapper: WorkspaceProvider,
    });
    expect(sessionStorage.getItem("workspaceId")).toBe("ws-a");

    auth.current = signedOut;
    rerender();

    expect(sessionStorage.getItem("workspaceId")).toBe(null);
    expect(sessionStorage.getItem("workspaceOrgId")).toBe(null);
    expect(sessionStorage.getItem("workspaceRole")).toBe(null);
    expect(sessionStorage.getItem("wsLevel")).toBe(null);
    expect(readSessionWorkspaceForOrg(ORG_A)).toBe(null);
  });

  it("gives the next user their own workspace, not the previous user's", () => {
    auth.current = signedIn(ORG_A, {
      id: "ws-a",
      name: "Alpha Default",
      role: "Owner",
      wsLevel: 15,
    });
    const { rerender } = renderHook(() => useWorkspace(), {
      wrapper: WorkspaceProvider,
    });

    auth.current = signedOut;
    rerender();

    // A different user signs in in the same tab. Even in the same org, the row
    // must be seeded from their own user-info, never adopted from the tab.
    auth.current = signedIn(ORG_A, {
      id: "ws-b",
      name: "B Default",
      role: "Member",
      wsLevel: 5,
    });
    rerender();

    expect(sessionStorage.getItem("workspaceId")).toBe("ws-b");
    expect(sessionStorage.getItem("workspaceRole")).toBe("Member");
    expect(sessionStorage.getItem("wsLevel")).toBe("5");
  });

  it("keeps the row while auth is still loading, so a refresh survives", () => {
    // The clear is gated on !loading for exactly this reason: on a reload the
    // tab is briefly unauthenticated while user-info is in flight.
    writeSessionWorkspace({
      id: "ws-a",
      name: "Alpha Default",
      displayName: "Alpha Default",
      role: "Owner",
      wsLevel: 15,
      orgId: ORG_A,
    });

    auth.current = { authenticated: false, loading: true, user: null };
    renderHook(() => useWorkspace(), { wrapper: WorkspaceProvider });

    expect(sessionStorage.getItem("workspaceId")).toBe("ws-a");
    expect(readSessionWorkspaceForOrg(ORG_A)?.id).toBe("ws-a");
  });
});
