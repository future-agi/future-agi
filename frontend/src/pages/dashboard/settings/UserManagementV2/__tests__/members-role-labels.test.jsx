/* eslint-disable react/prop-types */
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import UserManagementV2 from "../UserManagementV2";

// The Members table names the role columns the way its dialogs name the role
// fields: one spelling ("Organization") and sentence case, like the page's
// other columns ("User name", "Start date", "Invite link").

const h = vi.hoisted(() => ({ isOSS: true }));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    role: "Owner",
    user: { id: "owner-1", default_workspace_id: "workspace-1" },
    orgLevel: 15,
    effectiveLevel: 15,
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: h.isOSS, isSuccess: true }),
}));
vi.mock("../GridTable", () => ({
  default: React.forwardRef(function GridTable({ columnDefs }, _ref) {
    return (
      <div role="row">
        {columnDefs
          .filter((column) => column.headerName)
          .map((column) => (
            <span role="columnheader" key={column.headerName}>
              {column.headerName}
            </span>
          ))}
      </div>
    );
  }),
}));
vi.mock("../AllActionForm", () => ({ default: () => null }));
vi.mock("../WorkspaceDetailPanel", () => ({ default: () => null }));
vi.mock("../UserHeaders", () => ({ default: () => null }));
vi.mock("src/sections/develop-detail/Common/BackButton", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

function headers({ workspaceScope = false } = {}) {
  render(
    <HelmetProvider>
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/dashboard/settings/user-management"]}>
          <UserManagementV2 workspaceScope={workspaceScope} />
        </MemoryRouter>
      </QueryClientProvider>
    </HelmetProvider>,
  );
  return screen.getAllByRole("columnheader").map((cell) => cell.textContent);
}

const isSentenceCase = (label) =>
  label.split(" ").every((word, index) => index === 0 || word === word.toLowerCase());

beforeEach(() => {
  h.isOSS = true;
});
afterEach(cleanup);

describe("Members table role columns", () => {
  it.each([true, false])(
    "spells the organization role column 'Organization role' (oss: %s)",
    (isOSS) => {
      h.isOSS = isOSS;
      const names = headers();
      expect(names).toContain("Organization role");
      expect(names.join("|")).not.toMatch(/Organisation|Organization Role/);
    },
  );

  it("names the workspace role column 'Workspace role' in a workspace's Members", () => {
    const names = headers({ workspaceScope: true });
    expect(names).toContain("Organization role");
    expect(names).toContain("Workspace role");
  });

  it.each([false, true])(
    "writes every column header in sentence case (workspace scope: %s)",
    (workspaceScope) => {
      const names = headers({ workspaceScope });
      expect(names.filter((name) => !isSentenceCase(name))).toEqual([]);
    },
  );
});
