/* eslint-disable react/prop-types */
// Settings > Members: a row's form is held by the page, so it stays open
// across a table reload. It belongs to the workspace it opened in: if the
// page's workspace changes while it is open (WorkspaceContext seeds it again
// when the user arrives again), the form closes and nothing is sent, so an
// edit or a removal never goes to the other workspace.
//
// The grid stand-in renders the action column's cells; the form stand-in
// shows what it was opened with and records what its confirm would send.
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import UserManagementV2 from "../UserManagementV2";
import { LEVELS } from "../constant";

const h = vi.hoisted(() => ({
  workspaceId: "workspace-1",
  rows: [],
  sent: [],
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    role: "Owner",
    user: {
      id: "owner-1",
      email: "owner@example.test",
      default_workspace_id: "workspace-1",
    },
    orgLevel: 15,
    effectiveLevel: 15,
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: h.workspaceId }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false, isSuccess: true }),
}));
// Renders the action column's cells for h.rows. Its api's refresh drops and
// builds every cell again, as the server-side row model does on a purge (the
// page purges the table when its workspace changes).
vi.mock("../GridTable", () => ({
  default: React.forwardRef(function GridTable({ columnDefs }, ref) {
    const [generation, setGeneration] = React.useState(0);
    const api = React.useMemo(
      () => ({
        collapseAll: () => {},
        setGridOption: () => {},
        refreshServerSide: () => setGeneration((n) => n + 1),
      }),
      [],
    );
    React.useImperativeHandle(ref, () => ({ api }), [api]);
    const action = columnDefs.find((column) => column.field === "action");
    const Cell = action.cellRenderer;
    return (
      <div key={generation}>
        {h.rows.map((row) => (
          <div key={row.id}>
            <Cell {...action.cellRendererParams} data={row} api={api} />
          </div>
        ))}
      </div>
    );
  }),
}));
vi.mock("../AllActionForm", () => ({
  default: ({ openActionForm, userData, workspaceId }) =>
    openActionForm && openActionForm.action !== "invite-user" ? (
      <div data-testid="member-action-form">
        {`${openActionForm.action}:${userData?.email}:${workspaceId}`}
        <button
          type="button"
          onClick={() =>
            h.sent.push({ action: openActionForm.action, workspaceId })
          }
        >
          Confirm
        </button>
      </div>
    ) : null,
}));
vi.mock("../WorkspaceDetailPanel", () => ({ default: () => null }));
vi.mock("../UserHeaders", () => ({ default: () => null }));
vi.mock("src/sections/develop-detail/Common/BackButton", () => ({
  default: () => null,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({
  default: React.forwardRef(function ActionIcon(_props, ref) {
    return <span ref={ref} data-testid="member-actions-trigger" />;
  }),
}));

const MEMBER = {
  id: "member-1",
  name: "Mia",
  email: "mia@example.test",
  status: "Active",
  org_level: LEVELS.MEMBER,
  ws_level: LEVELS.WORKSPACE_MEMBER,
  workspaces: [],
};

function membersPage(queryClient, workspaceScope) {
  return (
    <HelmetProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/dashboard/settings/user-management"]}>
          <UserManagementV2 workspaceScope={workspaceScope} />
        </MemoryRouter>
      </QueryClientProvider>
    </HelmetProvider>
  );
}

beforeEach(() => {
  h.workspaceId = "workspace-1";
  h.rows = [MEMBER];
  h.sent = [];
});
afterEach(cleanup);

describe("Members: an open row form and a workspace change", () => {
  it.each([
    ["Edit user info", "edit-ws-role"],
    ["Remove from workspace", "remove-ws-member"],
  ])(
    "workspace members: %s opened in one workspace closes unsent when the workspace changes",
    (item, action) => {
      const queryClient = new QueryClient();
      const page = render(membersPage(queryClient, true));

      fireEvent.click(screen.getByTestId("member-actions-trigger"));
      fireEvent.click(screen.getByText(item));
      expect(screen.getByTestId("member-action-form")).toHaveTextContent(
        `${action}:mia@example.test:workspace-1`,
      );

      h.workspaceId = "workspace-2";
      act(() => {
        page.rerender(membersPage(queryClient, true));
      });

      // Whatever form is still on screen, the person confirms it.
      const confirms = screen.queryAllByRole("button", { name: "Confirm" });
      confirms.forEach((button) => fireEvent.click(button));
      expect(h.sent).toEqual([]);
      expect(screen.queryByTestId("member-action-form")).toBeNull();
    },
  );

  it("organization members: Edit user info stays open and sends to its workspace while the workspace does not change", () => {
    const queryClient = new QueryClient();
    const page = render(membersPage(queryClient, false));

    fireEvent.click(screen.getByTestId("member-actions-trigger"));
    fireEvent.click(screen.getByText("Edit user info"));
    act(() => {
      page.rerender(membersPage(queryClient, false));
    });

    expect(screen.getByTestId("member-action-form")).toHaveTextContent(
      "edit-role:mia@example.test:workspace-1",
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(h.sent).toEqual([
      { action: "edit-role", workspaceId: "workspace-1" },
    ]);
  });
});
