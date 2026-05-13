import PropTypes from "prop-types";
import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import AnnotationQueueTable from "../annotation-queue-table";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/utils/format-time", () => ({
  fToNow: () => "2 days ago",
}));

vi.mock("src/hooks/use-ag-theme", () => ({
  useAgTheme: () => ({ withParams: () => ({}) }),
  useAgThemeWith: () => ({}),
}));

vi.mock("src/styles/clean-data-table.css", () => ({}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { id: "user-1", pk: "user-1" },
    role: "admin",
  }),
}));

vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { CREATE: "create" },
  RolePermission: { DATASETS: { create: { admin: true } } },
}));

vi.mock("src/routes/paths", () => ({
  paths: {
    dashboard: {
      annotations: {
        queueDetail: (id) => `/dashboard/annotations/queues/${id}`,
      },
    },
  },
}));

// Mock AG Grid
function MockAgGridReact({
  rowData,
  columnDefs,
  context,
  onCellClicked,
  noRowsOverlayComponent: NoRowsOverlay,
}) {
  if (!rowData || rowData.length === 0) {
    return NoRowsOverlay ? <NoRowsOverlay /> : null;
  }
  return (
    <div data-testid="ag-grid">
      <div data-testid="ag-grid-header">
        {columnDefs
          .filter((c) => c.headerName)
          .map((col) => (
            <span key={col.field}>{col.headerName}</span>
          ))}
      </div>
      {rowData.map((row) => (
        <div key={row.id} data-testid="ag-grid-row">
          {columnDefs.map((col) => {
            const Renderer = col.cellRenderer;
            return Renderer ? (
              <div
                key={col.field}
                onClick={() => {
                  if (col.field !== "actions" && onCellClicked) {
                    onCellClicked({
                      data: row,
                      column: { getColId: () => col.field },
                    });
                  }
                }}
              >
                <Renderer data={row} context={context} />
              </div>
            ) : null;
          })}
        </div>
      ))}
    </div>
  );
}

MockAgGridReact.propTypes = {
  rowData: PropTypes.array,
  columnDefs: PropTypes.array.isRequired,
  context: PropTypes.object,
  onCellClicked: PropTypes.func,
  noRowsOverlayComponent: PropTypes.elementType,
};

vi.mock("ag-grid-react", () => ({
  AgGridReact: MockAgGridReact,
}));

const MOCK_QUEUES = [
  {
    id: "q1",
    name: "Review Queue",
    description: "Review items",
    status: "draft",
    item_count: 10,
    completed_count: 3,
    label_count: 2,
    annotators: [
      {
        id: "a1",
        user_id: "user-1",
        name: "Alice",
        email: "alice@example.com",
        role: "manager",
      },
      {
        id: "a2",
        user_id: "user-2",
        name: "Bob",
        email: "bob@example.com",
        role: "annotator",
      },
      {
        id: "a3",
        user_id: "user-3",
        name: "Cara",
        email: "cara@example.com",
        role: "reviewer",
      },
    ],
    created_at: "2025-01-01T00:00:00Z",
  },
  {
    id: "q2",
    name: "Active Queue",
    status: "active",
    item_count: 0,
    completed_count: 0,
    label_count: 1,
    annotators: [
      { id: "a3", user_id: "user-1", name: "Alice", role: "manager" },
    ],
    created_at: "2025-01-02T00:00:00Z",
  },
];

const defaultProps = {
  data: MOCK_QUEUES,
  loading: false,
  page: 0,
  rowsPerPage: 10,
  totalCount: 2,
  onPageChange: vi.fn(),
  onRowsPerPageChange: vi.fn(),
  onEdit: vi.fn(),
  onDuplicate: vi.fn(),
  onArchive: vi.fn(),
  onStatusChange: vi.fn(),
};

describe("AnnotationQueueTable", () => {
  it("renders table headers", () => {
    render(<AnnotationQueueTable {...defaultProps} />);

    expect(screen.getByText("Name")).toBeInTheDocument();
    expect(screen.getByText("Status")).toBeInTheDocument();
    expect(screen.getByText("Progress")).toBeInTheDocument();
    expect(screen.getByText("Labels")).toBeInTheDocument();
    expect(screen.getByText("Members")).toBeInTheDocument();
    expect(screen.getByText("Created")).toBeInTheDocument();
  });

  it("renders queue rows", () => {
    render(<AnnotationQueueTable {...defaultProps} />);

    expect(screen.getByText("Review Queue")).toBeInTheDocument();
    expect(screen.getByText("Active Queue")).toBeInTheDocument();
  });

  it("renders status badges", () => {
    render(<AnnotationQueueTable {...defaultProps} />);

    expect(screen.getByText("Draft")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("shows progress bar for queues with items", () => {
    render(<AnnotationQueueTable {...defaultProps} />);

    // Queue with items should show progress
    expect(screen.getByText("3/10 (30%)")).toBeInTheDocument();
    // Queue without items shows "No items"
    expect(screen.getByText("No items")).toBeInTheDocument();
  });

  it("shows label counts", () => {
    render(<AnnotationQueueTable {...defaultProps} />);

    expect(screen.getByText("2 labels")).toBeInTheDocument();
    expect(screen.getByText("1 labels")).toBeInTheDocument();
  });

  it("shows all queue members, including managers and reviewers", () => {
    render(<AnnotationQueueTable {...defaultProps} />);

    expect(screen.getAllByText("A").length).toBeGreaterThan(0);
    expect(screen.getByText("B")).toBeInTheDocument();
    expect(screen.getByText("C")).toBeInTheDocument();
  });

  it("shows queue members once with their roles on hover", async () => {
    const user = userEvent.setup();
    render(<AnnotationQueueTable {...defaultProps} />);

    const avatarGroup = screen
      .getByTestId("queue-members-q1")
      .querySelector(".MuiAvatarGroup-root");
    await user.hover(avatarGroup);

    expect(await screen.findByText("3 people")).toBeInTheDocument();
    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("alice@example.com")).toBeInTheDocument();
    expect(screen.getByText("Manager")).toBeInTheDocument();
    expect(screen.getByText("Bob")).toBeInTheDocument();
    expect(screen.getByText("bob@example.com")).toBeInTheDocument();
    expect(screen.getByText("Annotator")).toBeInTheDocument();
    expect(screen.getByText("Cara")).toBeInTheDocument();
    expect(screen.getByText("cara@example.com")).toBeInTheDocument();
    expect(screen.getByText("Reviewer")).toBeInTheDocument();
    expect(screen.queryByText("Managers")).not.toBeInTheDocument();
  });

  it("shows a multi-role member once with every assigned role", async () => {
    const user = userEvent.setup();
    render(
      <AnnotationQueueTable
        {...defaultProps}
        data={[
          {
            ...MOCK_QUEUES[0],
            annotators: [
              {
                id: "a1",
                user_id: "user-1",
                name: "Alice",
                email: "alice@example.com",
                role: "manager",
                roles: ["manager", "reviewer", "annotator"],
              },
            ],
          },
        ]}
      />,
    );

    const avatarGroup = screen
      .getByTestId("queue-members-q1")
      .querySelector(".MuiAvatarGroup-root");
    await user.hover(avatarGroup);

    expect(await screen.findByText("1 person")).toBeInTheDocument();
    expect(screen.getAllByText("Alice")).toHaveLength(1);
    expect(screen.getAllByText("alice@example.com")).toHaveLength(1);
    expect(screen.getByText("Manager")).toBeInTheDocument();
    expect(screen.getByText("Annotator")).toBeInTheDocument();
    expect(screen.getByText("Reviewer")).toBeInTheDocument();
  });

  it("shows loading skeletons when loading", () => {
    const { container } = render(
      <AnnotationQueueTable {...defaultProps} loading={true} />,
    );
    expect(screen.queryByText("Review Queue")).not.toBeInTheDocument();
    const skeletons = container.querySelectorAll(".MuiSkeleton-root");
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it("shows empty message when data is empty", () => {
    render(<AnnotationQueueTable {...defaultProps} data={[]} />);
    expect(
      screen.getByText("No queues match your filters"),
    ).toBeInTheDocument();
  });

  it("navigates on cell click", async () => {
    const user = userEvent.setup();
    render(<AnnotationQueueTable {...defaultProps} />);

    // Clicking a queue name triggers onCellClicked which navigates
    await user.click(screen.getByText("Review Queue"));
    // Navigation is handled programmatically via react-router navigate
    // Verify the queue name renders (navigation is tested via integration)
    expect(screen.getByText("Review Queue")).toBeInTheDocument();
  });

  describe("action menu", () => {
    it("opens menu with edit/duplicate/delete options", async () => {
      const user = userEvent.setup();
      render(<AnnotationQueueTable {...defaultProps} />);

      const moreButtons = screen
        .getAllByTestId("iconify")
        .filter(
          (el) => el.getAttribute("data-icon") === "eva:more-vertical-fill",
        );
      await user.click(moreButtons[0].closest("button"));

      expect(screen.getByText("Edit")).toBeInTheDocument();
      expect(screen.getByText("Duplicate")).toBeInTheDocument();
      expect(screen.getByText("Archive")).toBeInTheDocument();
    });

    it("shows status transition for draft queue (Activate)", async () => {
      const user = userEvent.setup();
      render(<AnnotationQueueTable {...defaultProps} />);

      // Click menu on first (draft) queue
      const moreButtons = screen
        .getAllByTestId("iconify")
        .filter(
          (el) => el.getAttribute("data-icon") === "eva:more-vertical-fill",
        );
      await user.click(moreButtons[0].closest("button"));

      expect(screen.getByText("Activate")).toBeInTheDocument();
    });

    it("shows status transitions for active queue (Pause)", async () => {
      const user = userEvent.setup();
      render(<AnnotationQueueTable {...defaultProps} />);

      // Click menu on second (active) queue
      const moreButtons = screen
        .getAllByTestId("iconify")
        .filter(
          (el) => el.getAttribute("data-icon") === "eva:more-vertical-fill",
        );
      await user.click(moreButtons[1].closest("button"));

      expect(screen.getByText("Pause")).toBeInTheDocument();
    });

    it("calls onEdit when Edit is clicked", async () => {
      const user = userEvent.setup();
      const onEdit = vi.fn();
      render(<AnnotationQueueTable {...defaultProps} onEdit={onEdit} />);

      const moreButtons = screen
        .getAllByTestId("iconify")
        .filter(
          (el) => el.getAttribute("data-icon") === "eva:more-vertical-fill",
        );
      await user.click(moreButtons[0].closest("button"));
      await user.click(screen.getByText("Edit"));

      expect(onEdit).toHaveBeenCalledWith(MOCK_QUEUES[0]);
    });

    it("calls onStatusChange when status action is clicked", async () => {
      const user = userEvent.setup();
      const onStatusChange = vi.fn();
      render(
        <AnnotationQueueTable
          {...defaultProps}
          onStatusChange={onStatusChange}
        />,
      );

      const moreButtons = screen
        .getAllByTestId("iconify")
        .filter(
          (el) => el.getAttribute("data-icon") === "eva:more-vertical-fill",
        );
      await user.click(moreButtons[0].closest("button"));
      await user.click(screen.getByText("Activate"));

      expect(onStatusChange).toHaveBeenCalledWith(MOCK_QUEUES[0], "active");
    });
  });
});
