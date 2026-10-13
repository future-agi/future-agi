import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { createOmegaSquadClient } from "src/api/omega-squad/remoteClient";
import useOmegaSquadConfig from "./useOmegaSquadConfig";

const host = vi.hoisted(() => ({ auth: {}, organization: {}, workspace: {} }));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => host.auth,
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => host.organization,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => host.workspace,
}));

beforeEach(() => {
  host.auth = { authenticated: true, user: { id: "account-a" } };
  host.organization = { currentOrganizationId: "org-a", isReady: true };
  host.workspace = { currentWorkspaceId: "ws-a", isReady: true };
  window.__OMEGA_SQUAD_CONFIG__ = {
    baseUrl: "https://omega.example.test",
    allowHostToken: true,
    organizationId: "runtime-org",
    workspaceId: "runtime-workspace",
    getToken: vi.fn(() => "host-session-secret"),
  };
});

const fetchSuccess = () =>
  vi.fn(async () => new Response(JSON.stringify({ agents: [] })));

describe("Squad host context binding", () => {
  it("sends context selectors instead of mutable storage or runtime defaults", async () => {
    sessionStorage.setItem("organizationId", "stored-org");
    sessionStorage.setItem("workspaceId", "stored-ws");
    const { result } = renderHook(() => useOmegaSquadConfig());
    const fetchImpl = fetchSuccess();
    await createOmegaSquadClient(result.current.config, {
      fetchImpl,
    }).listWorkforce();
    expect(fetchImpl.mock.calls[0][1].headers).toMatchObject({
      Authorization: "Bearer host-session-secret",
      "X-Organization-Id": "org-a",
      "X-Workspace-Id": "ws-a",
    });
    expect(result.current.scopeKey).not.toContain("host-session-secret");
  });

  it.each([
    "auth",
    "organization",
    "workspace",
    "missing-org",
    "missing-workspace",
  ])("blocks credentials until %s selection is ready", async (pending) => {
    if (pending === "auth") host.auth.authenticated = false;
    if (pending === "organization") host.organization.isReady = false;
    if (pending === "workspace") host.workspace.isReady = false;
    if (pending === "missing-org")
      host.organization.currentOrganizationId = null;
    if (pending === "missing-workspace")
      host.workspace.currentWorkspaceId = null;
    const { result } = renderHook(() => useOmegaSquadConfig());
    const fetchImpl = fetchSuccess();
    await expect(
      createOmegaSquadClient(result.current.config, {
        fetchImpl,
      }).listWorkforce(),
    ).resolves.toMatchObject({ available: false, code: "SCOPE_UNAVAILABLE" });
    expect(window.__OMEGA_SQUAD_CONFIG__.getToken).not.toHaveBeenCalled();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it.each(["organization", "workspace", "account"])(
    "retires clients on %s changes, including A to B to A",
    async (changed) => {
      const { result, rerender, unmount } = renderHook(() =>
        useOmegaSquadConfig(),
      );
      const oldKey = result.current.scopeKey;
      const fetchImpl = fetchSuccess();
      const oldClient = createOmegaSquadClient(result.current.config, {
        fetchImpl,
      });
      if (changed === "organization")
        host.organization.currentOrganizationId = "org-b";
      if (changed === "workspace") host.workspace.currentWorkspaceId = "ws-b";
      if (changed === "account") host.auth.user = { id: "account-b" };
      rerender();
      expect(result.current.scopeKey).not.toBe(oldKey);
      await expect(oldClient.listWorkforce()).resolves.toMatchObject({
        code: "SCOPE_UNAVAILABLE",
      });
      expect(fetchImpl).not.toHaveBeenCalled();
      await expect(
        createOmegaSquadClient(result.current.config, {
          fetchImpl,
        }).listWorkforce(),
      ).resolves.toMatchObject({ available: true });

      host.organization.currentOrganizationId = "org-a";
      host.workspace.currentWorkspaceId = "ws-a";
      host.auth.user = { id: "account-a" };
      rerender();
      expect(result.current.scopeKey).toBe(oldKey);
      await expect(oldClient.listWorkforce()).resolves.toMatchObject({
        code: "SCOPE_UNAVAILABLE",
      });
      const lastClient = createOmegaSquadClient(result.current.config, {
        fetchImpl,
      });
      unmount();
      await expect(lastClient.listWorkforce()).resolves.toMatchObject({
        code: "SCOPE_UNAVAILABLE",
      });
    },
  );

  it("keeps the committed scope active through StrictMode effect replay", async () => {
    const { result } = renderHook(() => useOmegaSquadConfig(), {
      wrapper: ({ children }) => <StrictMode>{children}</StrictMode>,
    });
    await expect(
      createOmegaSquadClient(result.current.config, {
        fetchImpl: fetchSuccess(),
      }).listWorkforce(),
    ).resolves.toMatchObject({ available: true });
  });

  it("preserves native injected sessions without host providers or selections", async () => {
    window.__OMEGA_SQUAD_CONFIG__.allowHostToken = false;
    host.auth = {};
    host.organization = {};
    host.workspace = {};
    const { result, rerender } = renderHook(() => useOmegaSquadConfig());
    const original = result.current.config;
    const fetchImpl = fetchSuccess();
    await expect(
      createOmegaSquadClient(original, { fetchImpl }).listWorkforce(),
    ).resolves.toMatchObject({ available: true });
    host.organization = { currentOrganizationId: "host-org", isReady: false };
    rerender();
    expect(result.current.config).toBe(original);
    expect(fetchImpl.mock.calls[0][1].headers["X-Organization-Id"]).toBe(
      "runtime-org",
    );
  });
});
