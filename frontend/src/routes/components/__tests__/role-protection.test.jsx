import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RoleProtection from "../role-protection";
import WorkspaceRoleProtection from "../workspace-role-protection";
import { describeNeededRoles } from "../no-access-roles";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="iconify" data-icon={icon} />,
}));

const auth = { user: null, role: null };
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => auth }));

const wsList = {
  workspace: null,
  isLoading: false,
  isError: false,
  refetch: vi.fn(),
};
vi.mock("src/api/workspaces/list", () => ({
  useWorkspaceFromList: () => wsList,
}));

function Location() {
  return <p data-testid="location">{useLocation().pathname}</p>;
}

function renderAt(path, element, routePath) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={routePath || path} element={element} />
        <Route path="*" element={<Location />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RoleProtection on a settings URL", () => {
  beforeEach(() => {
    auth.user = null;
    auth.role = null;
  });

  it("shows a no-access state naming the needed role, not the Datasets page", () => {
    auth.user = { organization_role: "Member" };
    auth.role = "workspace_member";
    renderAt(
      "/dashboard/settings/mcp-server",
      <RoleProtection allowedRoles={["Owner", "Admin"]}>
        <p>MCP page</p>
      </RoleProtection>,
    );
    expect(screen.queryByText("MCP page")).toBeNull();
    expect(screen.queryByTestId("location")).toBeNull();
    expect(screen.queryByText(/Add your first dataset/)).toBeNull();
    const state = screen.getByTestId("settings-no-access");
    expect(state).toHaveTextContent("You don't have access to this page");
    expect(state).toHaveTextContent(
      "This page needs the organization role Owner or Admin.",
    );
    expect(state).toHaveTextContent(
      "You are Member in this organization and Member in this workspace.",
    );
    expect(screen.getByRole("link", { name: "Go to Profile" })).toHaveAttribute(
      "href",
      "/dashboard/settings/profile-settings",
    );
  });

  it("names org and workspace roles for a page open to both", () => {
    auth.user = { organization_role: "Viewer" };
    auth.role = "workspace_viewer";
    renderAt(
      "/dashboard/settings/ai-providers",
      <RoleProtection
        allowedRoles={[
          "Owner",
          "Admin",
          "Member",
          "workspace_admin",
          "workspace_member",
        ]}
      >
        <p>Providers</p>
      </RoleProtection>,
    );
    expect(screen.getByTestId("settings-no-access")).toHaveTextContent(
      "This page needs the organization role Owner, Admin or Member, or the workspace role Admin or Member.",
    );
  });

  it("renders the page for an allowed role", () => {
    auth.user = { organization_role: "Owner" };
    renderAt(
      "/dashboard/settings/api_keys",
      <RoleProtection allowedRoles={["Owner"]}>
        <p>Keys page</p>
      </RoleProtection>,
    );
    expect(screen.getByText("Keys page")).toBeInTheDocument();
    expect(screen.queryByTestId("settings-no-access")).toBeNull();
  });

  it("lets a workspace role in when the org role is not listed", () => {
    auth.user = { organization_role: "Viewer" };
    auth.role = "workspace_admin";
    renderAt(
      "/dashboard/settings/integrations",
      <RoleProtection allowedRoles={["Owner", "workspace_admin"]}>
        <p>Integrations</p>
      </RoleProtection>,
    );
    expect(screen.getByText("Integrations")).toBeInTheDocument();
  });
});

describe("WorkspaceRoleProtection", () => {
  const path = "/dashboard/settings/workspace/ws-1/general";
  const routePath = "/dashboard/settings/workspace/:workspaceId/general";

  beforeEach(() => {
    auth.user = { organization_role: "Member" };
    auth.role = "Member";
    wsList.workspace = null;
    wsList.isError = false;
    wsList.refetch.mockReset();
  });

  it("shows the no-access state for a workspace role below the page's", () => {
    wsList.workspace = { id: "ws-1", user_ws_level: 1 };
    renderAt(
      path,
      <WorkspaceRoleProtection allowedRoles={["workspace_admin"]}>
        <p>General</p>
      </WorkspaceRoleProtection>,
      routePath,
    );
    expect(screen.queryByText("General")).toBeNull();
    expect(screen.getByTestId("settings-no-access")).toHaveTextContent(
      "This page needs the workspace role Admin. You are Viewer in this workspace.",
    );
  });

  it("treats a failed workspace load as a load failure with Retry, not a denial", () => {
    wsList.isError = true;
    renderAt(
      path,
      <WorkspaceRoleProtection allowedRoles={["workspace_admin"]}>
        <p>General</p>
      </WorkspaceRoleProtection>,
      routePath,
    );
    expect(screen.queryByTestId("settings-no-access")).toBeNull();
    expect(
      screen.getByTestId("workspace-access-load-failed"),
    ).toBeInTheDocument();
    screen.getByRole("button", { name: "Retry" }).click();
    expect(wsList.refetch).toHaveBeenCalledTimes(1);
  });

  it("renders the page for an allowed workspace role", () => {
    wsList.workspace = { id: "ws-1", user_ws_level: 8 };
    renderAt(
      path,
      <WorkspaceRoleProtection allowedRoles={["workspace_admin"]}>
        <p>General</p>
      </WorkspaceRoleProtection>,
      routePath,
    );
    expect(screen.getByText("General")).toBeInTheDocument();
  });
});

describe("describeNeededRoles", () => {
  it("joins roles in plain English", () => {
    expect(describeNeededRoles(["Owner"])).toBe("the organization role Owner");
    expect(describeNeededRoles(["Owner", "Admin", "workspace_admin"])).toBe(
      "the organization role Owner or Admin, or the workspace role Admin",
    );
  });
});
