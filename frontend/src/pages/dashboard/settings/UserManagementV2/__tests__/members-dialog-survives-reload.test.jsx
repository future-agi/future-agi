/* eslint-disable react/prop-types */
// Settings > Members: a dialog the person had open closed by itself when the
// members table reloaded. A row's dialog lived inside the
// table's action cell, and the table builds its cells again when it reloads
// (a server-side refresh with purge drops every row). The dialog stays open
// until the person closes or submits it.
//
// The grid stand-in models that reload as a remount of every cell.
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

const grid = vi.hoisted(() => ({ api: null, rows: [] }));

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
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false, isSuccess: true }),
}));
// Renders the action column's cells for grid.rows. Its api's refresh drops
// and builds every cell again, as the server-side row model does on a purge.
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
    grid.api = api;
    const action = columnDefs.find((column) => column.field === "action");
    const Cell = action.cellRenderer;
    return (
      <div key={generation}>
        {grid.rows.map((row) => (
          <div key={row.id}>
            <Cell {...action.cellRendererParams} data={row} api={api} />
          </div>
        ))}
      </div>
    );
  }),
}));
vi.mock("../AllActionForm", () => ({
  default: ({ openActionForm, userData, workspaceId, onClose }) =>
    openActionForm && openActionForm.action !== "invite-user" ? (
      <div data-testid="member-action-form">
        {`${openActionForm.action}:${userData?.email}:${workspaceId}`}
        <button type="button" onClick={onClose}>
          Close form
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

function renderMembers({ workspaceScope }) {
  render(
    <HelmetProvider>
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/dashboard/settings/user-management"]}>
          <UserManagementV2 workspaceScope={workspaceScope} />
        </MemoryRouter>
      </QueryClientProvider>
    </HelmetProvider>,
  );
}

const reloadTable = () =>
  act(() => {
    grid.api.refreshServerSide({ purge: true });
  });

beforeEach(() => {
  grid.api = null;
  grid.rows = [MEMBER];
});
afterEach(cleanup);

describe("Members: an open row dialog survives a table reload", () => {
  it.each([
    ["organization", "Edit user info", false, "edit-role"],
    ["workspace", "Edit user info", true, "edit-ws-role"],
    ["workspace", "Remove from workspace", true, "remove-ws-member"],
  ])(
    "%s members: %s stays open until it is closed",
    (_scope, item, workspaceScope, action) => {
      renderMembers({ workspaceScope });
      const opened = `${action}:${MEMBER.email}:workspace-1`;

      fireEvent.click(screen.getByTestId("member-actions-trigger"));
      fireEvent.click(screen.getByText(item));
      expect(screen.getByTestId("member-action-form")).toHaveTextContent(
        opened,
      );

      reloadTable();
      expect(screen.getByTestId("member-action-form")).toHaveTextContent(
        opened,
      );

      reloadTable();
      expect(screen.getAllByTestId("member-action-form")).toHaveLength(1);

      fireEvent.click(screen.getByRole("button", { name: "Close form" }));
      expect(screen.queryByTestId("member-action-form")).toBeNull();
    },
  );
});
