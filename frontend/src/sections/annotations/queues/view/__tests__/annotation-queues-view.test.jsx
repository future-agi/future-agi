import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import AnnotationQueuesView from "../annotation-queues-view";

const mocks = vi.hoisted(() => ({
  useAnnotationQueuesList: vi.fn(),
  restoreQueue: vi.fn(),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

// AnnotationsTabs uses react-router navigation; not under test here.
vi.mock("../../../view/annotations-tabs", () => ({
  default: () => <div data-testid="annotations-tabs">Tabs</div>,
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useAnnotationQueuesList: mocks.useAnnotationQueuesList,
  useUpdateAnnotationQueueStatus: () => ({ mutate: vi.fn() }),
  useDeleteAnnotationQueue: () => ({ mutate: vi.fn(), isPending: false }),
  useRestoreAnnotationQueue: () => ({ mutate: mocks.restoreQueue }),
}));

// The real table renders through AG Grid; this stand-in shows each queue
// name and, in the archived view, the Restore action the row menu offers.
function MockAnnotationQueueTable({ data, archivedView, onRestore }) {
  return (
    <div data-testid="queue-table">
      {data.map((queue) => (
        <div key={queue.id}>
          <span>{queue.name}</span>
          {archivedView ? (
            <button type="button" onClick={() => onRestore(queue)}>
              {`Restore ${queue.name}`}
            </button>
          ) : null}
        </div>
      ))}
    </div>
  );
}

MockAnnotationQueueTable.propTypes = {
  data: PropTypes.array.isRequired,
  archivedView: PropTypes.bool,
  onRestore: PropTypes.func,
};

vi.mock("../../annotation-queue-table", () => ({
  default: MockAnnotationQueueTable,
}));

vi.mock("../../create-queue-drawer", () => ({
  default: () => null,
}));

const ARCHIVED_QUEUE = {
  id: "queue-archived-1",
  name: "Support triage",
  status: "active",
  item_count: 12,
  completed_count: 4,
};

describe("AnnotationQueuesView: archiving the last active queue", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // No active queues remain; the only queue sits in the archived list.
    mocks.useAnnotationQueuesList.mockImplementation((params) => ({
      data: params?.archived
        ? { results: [ARCHIVED_QUEUE], count: 1 }
        : { results: [], count: 0 },
      isLoading: false,
    }));
  });

  it("keeps the Archived toggle next to the empty state", () => {
    render(<AnnotationQueuesView />);

    expect(screen.getByText("No annotation queues yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Active" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archived" }),
    ).toBeInTheDocument();
  });

  it("opens the archived queue from the empty state and restores it", async () => {
    const user = userEvent.setup();
    render(<AnnotationQueuesView />);

    await user.click(screen.getByRole("button", { name: "Archived" }));

    expect(await screen.findByText("Support triage")).toBeInTheDocument();
    expect(mocks.useAnnotationQueuesList).toHaveBeenLastCalledWith(
      expect.objectContaining({ archived: true, page: 1 }),
    );
    expect(
      screen.queryByText("No annotation queues yet"),
    ).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Restore Support triage" }),
    );

    expect(mocks.restoreQueue).toHaveBeenCalledWith(
      "queue-archived-1",
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });
});
