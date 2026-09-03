import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

// Effects run child-first and WorkspaceProvider
// is nested inside OrganizationProvider, so on the render where a new `user`
// arrives this provider still sees the *previous* currentOrganizationId. When
// the backend has resolved a different org, preferring that stale value adopts
// the old org's workspace row — and with it the old org's role and wsLevel.

const h = vi.hoisted(() => ({
  auth: { authenticated: false, user: null, loading: false },
  org: { currentOrganizationId: null },
  axios: { defaults: { headers: { common: {} } }, get: vi.fn(), post: vi.fn() },
}));

vi.mock("src/utils/axios", () => ({
  default: h.axios,
  endpoints: {
    workspaces: { list: "/workspaces/", switch: "/workspaces/switch/" },
    organizations: {},
  },
}));

vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => h.auth }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => h.org,
}));

import {
  writeSessionWorkspace,
  useWorkspace,
  WorkspaceProvider,
} from "../WorkspaceContext";

const ORG_A = "org-aaaa";
const ORG_B = "org-bbbb";

beforeEach(() => {
  sessionStorage.clear();
  h.axios.defaults.headers.common = {};
  // The tab is pinned to org A and holds org A's workspace.
  sessionStorage.setItem("organizationId", ORG_A);
  writeSessionWorkspace({
    id: "ws-a",
    name: "Alpha Analytics",
    displayName: "Alpha Analytics",
    role: "Workspace Admin",
    wsLevel: 15,
    orgId: ORG_A,
  });
  // Membership in A was deactivated, so the backend resolved org B instead.
  h.auth = {
    authenticated: true,
    loading: false,
    user: {
      organization: { id: ORG_B },
      default_workspace_id: "ws-b",
      default_workspace_name: "Beta Default",
      default_workspace_display_name: "Beta Default",
      default_workspace_role: "Member",
      ws_level: 3,
    },
  };
  // OrganizationProvider has not processed this user yet: still the old org.
  h.org = { currentOrganizationId: ORG_A };
});

describe("workspace org precedence — the resolved org wins over stale context", () => {
  it("does not adopt the previous org's workspace", async () => {
    const { result } = renderHook(() => useWorkspace(), {
      wrapper: WorkspaceProvider,
    });

    await waitFor(() => expect(result.current.isReady).toBe(true));
    expect(result.current.currentWorkspaceId).toBe("ws-b");
  });

  it("does not carry the previous org's role and level into the new org", async () => {
    // These drive useNavSettingsData (WORKSPACE_ADMIN) and the wsLevel >= 8
    // gates, which is the leak this PR exists to close.
    const { result } = renderHook(() => useWorkspace(), {
      wrapper: WorkspaceProvider,
    });

    await waitFor(() => expect(result.current.isReady).toBe(true));
    expect(result.current.currentWorkspaceRole).not.toBe("Workspace Admin");
    expect(result.current.wsLevel).not.toBe(15);
  });

  it("sends the new org's workspace on the wire, not the old one", async () => {
    renderHook(() => useWorkspace(), { wrapper: WorkspaceProvider });

    await waitFor(() =>
      expect(h.axios.defaults.headers.common["X-Workspace-Id"]).toBe("ws-b"),
    );
  });

  it("still trusts the stored row while the org genuinely matches", async () => {
    // The ordinary case: no org change, so the tab keeps its own workspace
    // rather than resetting to the default on every load.
    h.auth.user.organization = { id: ORG_A };
    const { result } = renderHook(() => useWorkspace(), {
      wrapper: WorkspaceProvider,
    });

    await waitFor(() => expect(result.current.isReady).toBe(true));
    expect(result.current.currentWorkspaceId).toBe("ws-a");
  });
});
