import React from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  WorkspaceProvider,
  useWorkspace,
  writeSessionWorkspace,
} from "../WorkspaceContext";

const h = vi.hoisted(() => ({
  auth: { authenticated: true, loading: false, user: null },
  org: { currentOrganizationId: "org" },
  axios: { defaults: { headers: { common: {} } }, get: vi.fn(), post: vi.fn() },
  resetAll: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: h.axios,
  endpoints: {
    auth: { me: "/accounts/user-info/" },
    workspaces: { switch: "/accounts/workspace/switch/" },
  },
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => h.auth }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => h.org,
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/logger", () => ({ default: { error: vi.fn() } }));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({
  default: { getState: () => ({ resetAll: h.resetAll }) },
}));

const landing = (id) => ({
  id: "member",
  organization: { id: "org" },
  default_workspace_id: id,
  default_workspace_name: id,
  default_workspace_role: "Member",
  ws_level: 3,
});
let finish;
beforeEach(() => {
  sessionStorage.clear();
  sessionStorage.setItem("organizationId", "org");
  writeSessionWorkspace({
    id: "a",
    orgId: "org",
    role: "Workspace Admin",
    wsLevel: 15,
  });
  h.axios.defaults.headers.common = { "X-Organization-Id": "org" };
  h.auth = { authenticated: true, loading: false, user: landing("b") };
  h.org = { currentOrganizationId: "org" };
  h.axios.post.mockReset();
  h.axios.get.mockReset().mockImplementation(
    () =>
      new Promise((resolve, reject) => {
        finish = { resolve, reject };
      }),
  );
});
afterEach(cleanup);

describe("J-AUTH-WSL-001 restored workspace revalidation", () => {
  it("does not mark a restored selector ready before authentication resolves", () => {
    h.auth = { authenticated: false, loading: true, user: null };
    const { result } = renderHook(useWorkspace, { wrapper: WorkspaceProvider });
    expect(result.current.isReady).toBe(false);
  });

  it("revalidates the tab selector and reseeds after membership removal", async () => {
    const { result } = renderHook(useWorkspace, { wrapper: WorkspaceProvider });
    await waitFor(() =>
      expect(h.axios.get).toHaveBeenCalledWith("/accounts/user-info/", {
        headers: { "X-Workspace-Id": "a", "X-Organization-Id": "org" },
      }),
    );
    expect(result.current.isReady).toBe(false);
    await act(async () => finish.resolve({ data: landing("b") }));
    expect(result.current.currentWorkspaceId).toBe("b");
    expect(result.current.currentWorkspaceRole).toBe("Member");
    expect(result.current.wsLevel).toBe(3);
    expect(result.current.isReady).toBe(true);
    expect(sessionStorage.getItem("workspaceId")).toBe("b");
    expect(h.axios.defaults.headers.common["X-Workspace-Id"]).toBe("b");
    expect(h.axios.post).not.toHaveBeenCalled();
  });

  it("preserves an independent valid tab selection even when another tab changed the default", async () => {
    const { result } = renderHook(useWorkspace, { wrapper: WorkspaceProvider });
    await waitFor(() => expect(h.axios.get).toHaveBeenCalledTimes(1));
    await act(async () => finish.resolve({ data: landing("a") }));
    expect(result.current.currentWorkspaceId).toBe("a");
    expect(result.current.isReady).toBe(true);
    expect(result.current.wsLevel).toBe(3);
  });

  it("accepts an access-checked empty landing without a write", async () => {
    const { result } = renderHook(useWorkspace, { wrapper: WorkspaceProvider });
    await waitFor(() => expect(h.axios.get).toHaveBeenCalledTimes(1));
    await act(async () => finish.resolve({ data: landing(null) }));
    expect(result.current.currentWorkspaceId).toBeNull();
    expect(result.current.isReady).toBe(true);
    expect(sessionStorage.getItem("workspaceId")).toBeNull();
    expect(h.axios.post).not.toHaveBeenCalled();
  });

  it("does not apply a late revalidation over a newer selection", async () => {
    const { result, rerender } = renderHook(useWorkspace, {
      wrapper: WorkspaceProvider,
    });
    await waitFor(() => expect(h.axios.get).toHaveBeenCalledTimes(1));
    const previous = finish;
    writeSessionWorkspace({ id: "c", orgId: "org" });
    h.auth.user = landing("c");
    rerender();
    await act(async () => previous.resolve({ data: landing("b") }));
    expect(result.current.currentWorkspaceId).toBe("c");
    expect(h.axios.defaults.headers.common["X-Workspace-Id"]).toBe("c");
  });

  it("leaves navigation available without trusting a selector when revalidation fails", async () => {
    const { result } = renderHook(useWorkspace, { wrapper: WorkspaceProvider });
    await waitFor(() => expect(h.axios.get).toHaveBeenCalledTimes(1));
    await act(async () => finish.reject(new Error("offline")));
    expect(result.current.currentWorkspaceId).toBeNull();
    expect(result.current.isReady).toBe(true);
    expect(h.axios.post).not.toHaveBeenCalled();
  });
});
