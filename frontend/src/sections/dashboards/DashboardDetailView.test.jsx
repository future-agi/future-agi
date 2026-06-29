/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent } from "src/utils/test-utils";
import DashboardDetailView from "./DashboardDetailView";

const OWNER_EMAIL = "owner@test.com";
const OTHER_EMAIL = "other@test.com";

function makeDashboard(ownerEmail) {
  return {
    id: "dash-1",
    name: "Test Dashboard",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    created_by: ownerEmail ? { email: ownerEmail, name: ownerEmail } : null,
    updated_by: null,
    widgets: [],
  };
}

const mockMutate = vi.fn();
let mockDashboard = makeDashboard(OWNER_EMAIL);
let mockAuthUser = { email: OWNER_EMAIL };

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useParams: () => ({ dashboardId: "dash-1" }),
  };
});
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: mockAuthUser }),
}));
vi.mock("src/hooks/useDashboards", () => ({
  useDashboardDetail: () => ({ data: mockDashboard, isLoading: false }),
  useUpdateDashboard: () => ({ mutate: mockMutate, isPending: false }),
  useUpdateWidget: () => ({ mutate: mockMutate, isPending: false }),
  useDeleteWidget: () => ({ mutate: mockMutate, isPending: false }),
  useDeleteDashboard: () => ({ mutate: mockMutate, isPending: false }),
  useReorderWidgets: () => ({ mutate: mockMutate, isPending: false }),
  useDuplicateWidget: () => ({ mutate: mockMutate, isPending: false }),
  useCreateWidget: () => ({ mutate: mockMutate, isPending: false }),
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));
vi.mock("src/components/custom-dialog", () => ({
  ConfirmDialog: () => null,
}));
vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));
vi.mock("./WidgetChart", () => ({ default: () => null }));
vi.mock("./dashboardDateRange", () => ({
  resolveGlobalDateRange: () => null,
}));
vi.mock("@dnd-kit/core", () => ({
  DndContext: ({ children }) => <>{children}</>,
  DragOverlay: () => null,
  PointerSensor: class {},
  closestCenter: vi.fn(),
  useSensor: vi.fn(),
  useSensors: vi.fn(() => []),
  useDraggable: () => ({
    attributes: {},
    listeners: {},
    setNodeRef: vi.fn(),
    transform: null,
  }),
  useDroppable: () => ({ setNodeRef: vi.fn(), isOver: false }),
}));

function openDashboardMenu(getByRole) {
  const btn = getByRole("button", { name: /dashboard options/i });
  fireEvent.click(btn);
}

describe("DashboardDetailView — delete dashboard ownership gate", () => {
  beforeEach(() => {
    mockMutate.mockReset();
    mockAuthUser = { email: OWNER_EMAIL };
    mockDashboard = makeDashboard(OWNER_EMAIL);
  });

  it("shows Delete Dashboard when the current user owns the dashboard", () => {
    const { getByRole, queryByText } = render(<DashboardDetailView />);
    openDashboardMenu(getByRole);
    expect(queryByText("Delete Dashboard")).not.toBeNull();
  });

  it("hides Delete Dashboard when the dashboard is owned by a different user", () => {
    // red if fix reverted: menu item renders for all users
    mockDashboard = makeDashboard(OTHER_EMAIL);
    const { getByRole, queryByText } = render(<DashboardDetailView />);
    openDashboardMenu(getByRole);
    expect(queryByText("Delete Dashboard")).toBeNull();
  });

  it("hides Delete Dashboard when created_by is null", () => {
    // red if fix reverted: null?.email === undefined === undefined → true → renders
    mockDashboard = makeDashboard(null);
    const { getByRole, queryByText } = render(<DashboardDetailView />);
    openDashboardMenu(getByRole);
    expect(queryByText("Delete Dashboard")).toBeNull();
  });
});
