import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";

const h = vi.hoisted(() => ({
  listResult: { workspace: null, isLoading: false, isError: false },
}));

vi.mock("src/api/workspaces/list", () => ({
  useWorkspaceFromList: () => h.listResult,
}));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ workspaceId: "ws-1" }),
}));

// An org Owner: `canEdit` is `isOrgAdminPlus || wsLevel >= 8`, so the edit
// affordances do not depend on the workspace ever loading.
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceId: "ws-1",
    updateWorkspaceName: vi.fn(),
  }),
}));

vi.mock("src/utils/axios", () => ({
  default: { put: vi.fn() },
  endpoints: { workspace: { workspaceUpdate: (id) => `/workspaces/${id}/` } },
}));

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import WorkspaceGeneral from "../WorkspaceGeneral";

// The component's rename useMutation needs a client even on the paths that
// never reach it.
const renderPage = () =>
  render(
    <HelmetProvider>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <WorkspaceGeneral />
      </QueryClientProvider>
    </HelmetProvider>,
  );

describe("WorkspaceGeneral without a workspace", () => {
  beforeEach(() => {
    h.listResult = { workspace: null, isLoading: false, isError: false };
  });

  it("offers no editable name field when the list errored", () => {
    // The org list can settle without an id, which useWorkspacesList now
    // reports as an error. Rendering the form here hands an Owner a blank
    // name field with a live Save.
    h.listResult = { workspace: null, isLoading: false, isError: true };
    renderPage();

    expect(screen.getByText(/could not be loaded/i)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /save/i }),
    ).not.toBeInTheDocument();
  });

  it("offers no editable name field when the workspace is simply absent", () => {
    renderPage();

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /save/i }),
    ).not.toBeInTheDocument();
  });

  it("still renders the form once the workspace arrives", () => {
    h.listResult = {
      workspace: { id: "ws-1", name: "Design", user_ws_level: 9 },
      isLoading: false,
      isError: false,
    };
    renderPage();

    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });
});
