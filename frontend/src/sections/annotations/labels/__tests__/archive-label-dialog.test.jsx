import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import ArchiveLabelDialog from "../archive-label-dialog";

// Mock the API hooks
const mockArchive = vi.fn();
const mockRestore = vi.fn();

vi.mock("src/api/annotation-labels/annotation-labels", () => ({
  useDeleteAnnotationLabel: () => ({
    mutate: mockArchive,
    isPending: false,
  }),
  useRestoreAnnotationLabel: () => ({
    mutate: mockRestore,
    isPending: false,
  }),
}));

// Mock notistack
vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// Mock useQueryClient (component uses it for cache invalidation)
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

// Mock axios (used in undo action)
vi.mock("src/utils/axios", () => ({ default: { post: vi.fn() } }));

describe("ArchiveLabelDialog", () => {
  const onClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns null when label is null", () => {
    const { container } = render(
      <ArchiveLabelDialog label={null} onClose={onClose} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("renders dialog with label name", () => {
    const label = { id: "1", name: "Accuracy", annotation_count: 0 };
    render(<ArchiveLabelDialog label={label} onClose={onClose} />);

    expect(screen.getByText("Archive Label")).toBeInTheDocument();
    expect(screen.getByText(/Accuracy/)).toBeInTheDocument();
  });

  it("shows not-in-use message when annotation_count is 0", () => {
    const label = { id: "1", name: "Test", annotation_count: 0 };
    render(<ArchiveLabelDialog label={label} onClose={onClose} />);

    expect(
      screen.getByText("This label is not currently in use."),
    ).toBeInTheDocument();
  });

  it("shows usage warning when annotation_count > 0", () => {
    const label = { id: "1", name: "Test", annotation_count: 5 };
    render(<ArchiveLabelDialog label={label} onClose={onClose} />);

    expect(screen.getByText(/used in 5 annotation tasks/)).toBeInTheDocument();
  });

  it("shows singular 'task' for annotation_count 1", () => {
    const label = { id: "1", name: "Test", annotation_count: 1 };
    render(<ArchiveLabelDialog label={label} onClose={onClose} />);

    expect(screen.getByText(/used in 1 annotation task\b/)).toBeInTheDocument();
  });

  it("calls onClose when Cancel is clicked", async () => {
    const user = userEvent.setup();
    const label = { id: "1", name: "Test", annotation_count: 0 };
    render(<ArchiveLabelDialog label={label} onClose={onClose} />);

    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("calls archiveLabel with label id when Archive is clicked", async () => {
    const user = userEvent.setup();
    const label = { id: "abc-123", name: "Test", annotation_count: 0 };
    render(<ArchiveLabelDialog label={label} onClose={onClose} />);

    await user.click(screen.getByRole("button", { name: /archive/i }));
    expect(mockArchive).toHaveBeenCalledWith("abc-123", expect.any(Object));
  });
});
