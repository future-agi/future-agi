import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "src/utils/test-utils";
import DashboardsListView from "./DashboardsListView";

// ─── Test data ────────────────────────────────────────────────────────────────

const OWNER_EMAIL = "owner@test.com";
const OTHER_EMAIL = "other@test.com";

function makeDashboard(id, ownerEmail, name) {
  return {
    id,
    name,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    widget_count: 2,
    created_by: ownerEmail ? { email: ownerEmail, name: ownerEmail } : null,
    updated_by: null,
    shared_with: [],
  };
}

// ─── Module mocks ─────────────────────────────────────────────────────────────

const mockMutate = vi.fn();
let mockDashboards = [];
let mockAuthUser = { email: OWNER_EMAIL };
// Role defaults to one with dashboard-delete permission (see
// rolePermissionMapping.js) so these tests keep isolating the ownership
// gate; the one role-gate test below overrides this to Viewer.
let mockRole = "Owner";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: mockAuthUser, role: mockRole }),
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardList: () => ({ data: mockDashboards, isLoading: false }),
  useCreateDashboard: () => ({ mutate: mockMutate, isPending: false }),
  useDeleteDashboard: () => ({ mutate: mockMutate, isPending: false }),
}));

vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));

vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: () => null,
}));

vi.mock("src/components/svg-color", () => ({
  default: () => null,
}));

vi.mock("src/components/EmptyLayout/EmptyLayout", () => ({
  default: () => null,
}));

vi.mock("src/components/custom-dialog", () => ({
  ConfirmDialog: () => null,
}));

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getDeleteButtons(container) {
  return container.querySelectorAll(".row-actions");
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("DashboardsListView — delete icon ownership gate", () => {
  beforeEach(() => {
    mockMutate.mockReset();
    mockAuthUser = { email: OWNER_EMAIL };
    mockRole = "Owner";
    mockDashboards = [
      makeDashboard("id-1", OWNER_EMAIL, "My Dashboard"),
      makeDashboard("id-2", OTHER_EMAIL, "Their Dashboard"),
    ];
  });

  it("renders delete button only for dashboards owned by the current user", () => {
    // scenario: new — pins the ownership conditional added in this fix
    // red if fix reverted: querySelectorAll returns 2 (both rows get the button)
    const { container } = render(<DashboardsListView />);
    expect(getDeleteButtons(container)).toHaveLength(1);
  });

  it("hides delete button on every dashboard owned by a different user", () => {
    // scenario: boundary — all dashboards owned by someone else
    // red if fix reverted: returns N buttons instead of 0
    mockDashboards = [
      makeDashboard("id-1", OTHER_EMAIL, "Their Dashboard A"),
      makeDashboard("id-2", OTHER_EMAIL, "Their Dashboard B"),
    ];
    const { container } = render(<DashboardsListView />);
    expect(getDeleteButtons(container)).toHaveLength(0);
  });

  it("shows delete button on every dashboard owned by the current user", () => {
    // scenario: happy path — all dashboards are the user's own
    // red if fix reverted: still returns 2 (no regression)
    mockDashboards = [
      makeDashboard("id-1", OWNER_EMAIL, "My Dashboard A"),
      makeDashboard("id-2", OWNER_EMAIL, "My Dashboard B"),
    ];
    const { container } = render(<DashboardsListView />);
    expect(getDeleteButtons(container)).toHaveLength(2);
  });

  it("treats a null created_by as not-owned — no delete button", () => {
    // scenario: boundary — system/legacy dashboard with no creator recorded
    // red if fix reverted: returns 1 button (null?.email === undefined but
    // the unguarded button always renders)
    mockDashboards = [makeDashboard("id-1", null, "Legacy Dashboard")];
    const { container } = render(<DashboardsListView />);
    expect(getDeleteButtons(container)).toHaveLength(0);
  });

  it("shows no delete buttons when the dashboard list is empty", () => {
    // scenario: boundary — empty list; no crash, no stray buttons
    mockDashboards = [];
    const { container } = render(<DashboardsListView />);
    expect(getDeleteButtons(container)).toHaveLength(0);
  });

  it("hides the delete button when both created_by and the auth user are undefined", () => {
    // scenario: the actual fail-open case — undefined === undefined → true
    // red if fix reverted: renders the button because both sides are undefined
    mockAuthUser = undefined;
    mockDashboards = [makeDashboard("id-1", null, "Legacy Dashboard")];
    const { container } = render(<DashboardsListView />);
    expect(getDeleteButtons(container)).toHaveLength(0);
  });

  it("hides the delete button for a role without dashboard-delete permission, even on an owned dashboard", () => {
    // scenario: RBAC gate (TH-6927) stacks with the ownership gate — a
    // Viewer role never sees the button, regardless of ownership
    // red if the role gate is dropped: renders 1 button for the owned row
    mockRole = "Viewer";
    const { container } = render(<DashboardsListView />);
    expect(getDeleteButtons(container)).toHaveLength(0);
  });
});
