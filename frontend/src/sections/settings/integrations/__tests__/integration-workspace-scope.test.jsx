import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, renderHook, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  useCreateConnection,
  useDeleteConnection,
  useIntegrationConnection,
  useIntegrationConnections,
  usePauseConnection,
  useResumeConnection,
  useStartSlackInstall,
  useSyncLogs,
  useSyncNow,
  useUpdateConnection,
  useValidateCredentials,
} from "src/api/integrations";
import IntegrationsWorkspaceScope from "../IntegrationsWorkspaceScope";
import { IntegrationsScopeContext } from "../scope";
import { useCanManageIntegrations } from "../access";

// A workspace's own Integrations pages stand for the workspace in their URL,
// whichever workspace is current: every integrations request they send names
// it (X-Workspace-Id), so they list, add and change that workspace's
// connections; and its integrations are managed by an organization Owner or
// Admin or an Admin of that workspace. Elsewhere requests name no workspace
// of their own (the current one's header applies).

const h = vi.hoisted(() => ({
  orgRole: "Member",
  role: "workspace_member",
  wsLevel: 3,
}));
const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockDelete = vi.fn();

vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    ...actual,
    default: {
      ...actual.default,
      get: (...args) => mockGet(...args),
      post: (...args) => mockPost(...args),
      patch: (...args) => mockPatch(...args),
      delete: (...args) => mockDelete(...args),
    },
  };
});
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { organization_role: h.orgRole },
    role: h.role,
  }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspaceFromList: (id) => ({
    workspace: id ? { id, user_ws_level: h.wsLevel } : null,
  }),
}));

const WS = "c0a80101-0000-4000-8000-0000000000b0";
const ID = "c0a80101-0000-4000-8000-000000000006";
const ok = { data: { result: { connections: [], id: ID } } };

function wrap(scope) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function ScopedProviders({ children }) {
    return (
      <QueryClientProvider client={client}>
        <IntegrationsScopeContext.Provider value={scope}>
          {children}
        </IntegrationsScopeContext.Provider>
      </QueryClientProvider>
    );
  }
  ScopedProviders.propTypes = { children: PropTypes.node };
  return ScopedProviders;
}

const headerOf = (config) => config?.headers?.["X-Workspace-Id"];

beforeEach(() => {
  for (const m of [mockGet, mockPost, mockPatch, mockDelete]) {
    m.mockReset();
    m.mockResolvedValue(ok);
  }
});

describe("A workspace's own Integrations pages", () => {
  it.each([
    ["the list", () => useIntegrationConnections(), 1],
    ["a connection", () => useIntegrationConnection(ID), 1],
    ["its sync history", () => useSyncLogs(ID), 1],
  ])("read %s for the workspace in the URL", async (_n, useHook, arg) => {
    renderHook(useHook, {
      wrapper: wrap({ workspaceId: WS, canManage: true }),
    });
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(headerOf(mockGet.mock.calls[0][arg])).toBe(WS);
  });

  it.each([
    [
      "Validate & Continue",
      useValidateCredentials,
      { platform: "datadog" },
      mockPost,
      2,
    ],
    [
      "Connect Integration",
      useCreateConnection,
      { platform: "datadog" },
      mockPost,
      2,
    ],
    ["Save Changes", useUpdateConnection, { id: ID, data: {} }, mockPatch, 2],
    ["Delete", useDeleteConnection, ID, mockDelete, 1],
    ["Sync Now", useSyncNow, ID, mockPost, 2],
    ["Pause", usePauseConnection, ID, mockPost, 2],
    ["Resume", useResumeConnection, ID, mockPost, 2],
    ["Connect Slack", useStartSlackInstall, undefined, mockPost, 2],
  ])(
    "send %s for that workspace",
    async (_n, useHook, variables, mock, arg) => {
      const { result } = renderHook(() => useHook(), {
        wrapper: wrap({ workspaceId: WS, canManage: true }),
      });
      act(() => result.current.mutate(variables));
      await waitFor(() => expect(mock).toHaveBeenCalled());
      expect(headerOf(mock.mock.calls[0][arg])).toBe(WS);
    },
  );

  it("keep their list apart from the current workspace's", async () => {
    const client = new QueryClient();
    const current = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const scoped = ({ children }) => (
      <QueryClientProvider client={client}>
        <IntegrationsScopeContext.Provider value={{ workspaceId: WS }}>
          {children}
        </IntegrationsScopeContext.Provider>
      </QueryClientProvider>
    );
    renderHook(() => useIntegrationConnections(), { wrapper: current });
    renderHook(() => useIntegrationConnections(), { wrapper: scoped });
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
    expect(
      mockGet.mock.calls.map(([, config]) => headerOf(config)).sort(),
    ).toEqual([WS, undefined].sort());
    const keys = client
      .getQueryCache()
      .findAll({ queryKey: ["integrations", "connections", "list"] })
      .map((q) => q.queryKey.at(-1))
      .sort();
    expect(keys).toEqual([WS, "current"].sort());
  });
});

describe("Who manages a workspace's integrations", () => {
  const canManageAt = () => {
    let value;
    const Probe = () => {
      value = useCanManageIntegrations();
      return null;
    };
    render(
      <MemoryRouter
        initialEntries={[`/dashboard/settings/workspace/${WS}/integrations`]}
      >
        <Routes>
          <Route
            path="/dashboard/settings/workspace/:workspaceId/integrations"
            element={
              <IntegrationsWorkspaceScope>
                <Probe />
              </IntegrationsWorkspaceScope>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    return value;
  };

  it.each([
    ["an organization Owner", "Owner", "Owner", 0, true],
    ["an Admin of that workspace", "Member", "workspace_member", 8, true],
    ["a Member of that workspace", "Member", "workspace_admin", 3, false],
    ["a Viewer of that workspace", "Viewer", "workspace_viewer", 1, false],
  ])("%s: %s", (_who, orgRole, role, wsLevel, manages) => {
    // The current role (here another workspace's) does not decide.
    Object.assign(h, { orgRole, role, wsLevel });
    expect(canManageAt()).toBe(manages);
  });
});
