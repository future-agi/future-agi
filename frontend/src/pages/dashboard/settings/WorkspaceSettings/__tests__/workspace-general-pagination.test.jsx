import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const h = vi.hoisted(() => ({ get: vi.fn(), role: "Owner" }));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => h.get(...args), put: vi.fn() },
  endpoints: {
    workspaces: { list: "/accounts/workspaces/list/" },
    workspace: { workspaceUpdate: (id) => `/workspaces/${id}/` },
  },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-1", isReady: true }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: {}, role: h.role }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceId: "ws-1",
    updateWorkspaceName: vi.fn(),
  }),
}));
vi.mock("src/components/loading-screen", () => ({
  LoadingScreen: () => <p>Loading workspace</p>,
}));
vi.mock("src/routes/components/no-access-state", () => ({
  default: () => <p data-testid="no-workspace-access">No access</p>,
}));
vi.mock("src/sections/common/Access/WorkspaceSharingCard", () => ({
  default: () => null,
}));
vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("react-helmet-async", () => ({ Helmet: () => null }));

import WorkspaceRoleProtection from "src/routes/components/workspace-role-protection";
import WorkspaceGeneral from "../WorkspaceGeneral";

let client;

function renderDeepLink() {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={["/dashboard/settings/workspace/ws-101/general"]}
      >
        <Routes>
          <Route
            path="/dashboard/settings/workspace/:workspaceId/general"
            element={
              <WorkspaceRoleProtection
                allowedRoles={[
                  "workspace_admin",
                  "workspace_member",
                  "workspace_viewer",
                ]}
              >
                <WorkspaceGeneral />
              </WorkspaceRoleProtection>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("H15-C25-004 fresh-cache workspace 101 settings deep link", () => {
  beforeEach(() => {
    h.get.mockReset();
    client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
  });

  afterEach(() => {
    cleanup();
    client.clear();
  });

  it.each([
    ["Owner", 8, true],
    ["Admin", 8, true],
    ["Member", 8, true],
    ["Member", 3, false],
    ["Member", 1, false],
  ])(
    "loads General for %s with workspace level %i without scrolling",
    async (role, level, canEdit) => {
      h.role = role;
      let resolveSecondPage;
      const secondPage = new Promise((resolve) => {
        resolveSecondPage = resolve;
      });
      h.get
        .mockResolvedValueOnce({
          data: {
            current_page: 1,
            next: "?page=2&limit=100",
            results: Array.from({ length: 100 }, (_, i) => ({
              id: `ws-${i + 1}`,
              name: `Workspace ${i + 1}`,
              user_ws_level: level,
            })),
          },
        })
        .mockReturnValueOnce(secondPage);

      // No switcher is mounted, no list is preseeded, and no scroll is simulated.
      renderDeepLink();
      await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
      expect(screen.getByText("Loading workspace")).toBeInTheDocument();
      expect(screen.queryByText(/could not be loaded/i)).toBeNull();
      expect(screen.queryByTestId("no-workspace-access")).toBeNull();

      await act(async () =>
        resolveSecondPage({
          data: {
            current_page: 2,
            next: null,
            results: [
              { id: "ws-101", name: "Oldest workspace", user_ws_level: level },
            ],
          },
        }),
      );
      const input = await screen.findByRole("textbox", {
        name: "Workspace name",
      });
      await waitFor(() => expect(input).toHaveValue("Oldest workspace"));
      if (canEdit) expect(input).toBeEnabled();
      else expect(input).toBeDisabled();
      expect(screen.queryByTestId("no-workspace-access")).toBeNull();
      expect(h.get.mock.calls).toEqual([
        ["/accounts/workspaces/list/", { params: { page: 1, limit: 100 } }],
        ["/accounts/workspaces/list/", { params: { page: 2, limit: 100 } }],
      ]);
    },
  );
});
