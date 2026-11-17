// WorkspaceRoleProtection against the REAL workspace-list hook (no mocked
// hook shape): the load-failed Retry must send the workspace-list request
// again, and an organization that could not be established gets its own copy
// with no Retry, since no retry of the list can help there.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import WorkspaceRoleProtection from "../workspace-role-protection";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  org: { currentOrganizationId: "org-1", isReady: true },
}));

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => h.get(...args) },
  endpoints: { workspaces: { list: "/accounts/workspaces/list/" } },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => h.org,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { organization_role: "Member" },
    role: "Member",
  }),
}));
vi.mock("src/components/loading-screen", () => ({
  LoadingScreen: () => <p>Loading…</p>,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const LIST = "/accounts/workspaces/list/";
const listCalls = () => h.get.mock.calls.filter(([url]) => url === LIST);

function renderGuard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={["/dashboard/settings/workspace/ws-1/general"]}
      >
        <Routes>
          <Route
            path="/dashboard/settings/workspace/:workspaceId/general"
            element={
              <WorkspaceRoleProtection allowedRoles={["workspace_admin"]}>
                <p>General</p>
              </WorkspaceRoleProtection>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("WorkspaceRoleProtection with the real workspace-list hook", () => {
  beforeEach(() => {
    h.get.mockReset();
    h.org = { currentOrganizationId: "org-1", isReady: true };
  });

  it("Retry on a failed workspace-list load sends the request again", async () => {
    h.get.mockRejectedValue(new Error("workspace list down"));
    renderGuard();

    await screen.findByTestId("workspace-access-load-failed");
    const before = listCalls().length;
    expect(before).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(listCalls().length).toBe(before + 1));
  });

  it("shows the page once a Retry succeeds", async () => {
    h.get.mockRejectedValueOnce(new Error("workspace list down"));
    h.get.mockResolvedValue({
      data: { results: [{ id: "ws-1", user_ws_level: 8 }], next: null },
    });
    renderGuard();

    await screen.findByTestId("workspace-access-load-failed");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("General")).toBeInTheDocument();
    expect(screen.queryByTestId("workspace-access-load-failed")).toBeNull();
  });

  it("gives an organization that could not be established its own copy, with no Retry", async () => {
    h.org = { currentOrganizationId: null, isReady: true };
    renderGuard();

    const state = await screen.findByTestId("workspace-access-no-organization");
    expect(state).toHaveTextContent("No organization for this session");
    expect(state).toHaveTextContent(
      "Ask an organization Owner or Admin to add you, then sign in again.",
    );
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.queryByTestId("workspace-access-load-failed")).toBeNull();
    expect(screen.queryByText("General")).toBeNull();
    // The list is never requested without an organization to scope it to.
    expect(listCalls()).toHaveLength(0);
  });
});
