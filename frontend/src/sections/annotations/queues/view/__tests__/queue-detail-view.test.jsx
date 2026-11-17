import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import QueueDetailView from "../queue-detail-view";

const mocks = vi.hoisted(() => ({
  mode: "standard",
  role: "manager",
  addPairs: vi.fn(),
  pairwiseResults: vi.fn(),
  assignItems: vi.fn(),
  bulkRemove: vi.fn(),
  navigate: vi.fn(),
  queryClient: {
    removeQueries: vi.fn(),
    invalidateQueries: vi.fn(),
  },
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useParams: () => ({ queueId: "queue-1" }),
    useNavigate: () => mocks.navigate,
  };
});

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual("@tanstack/react-query");
  return {
    ...actual,
    useQueryClient: () => mocks.queryClient,
  };
});

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { id: "manager-1", email: "manager@example.com" },
  }),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  annotateKeys: {
    nextItem: (...args) => ["next-item", ...args],
  },
  annotationQueueKeys: {
    all: ["annotation-queues"],
    detail: (...args) => ["annotation-queue-detail", ...args],
    progress: (...args) => ["annotation-queue-progress", ...args],
  },
  queueItemKeys: {
    all: (...args) => ["queue-items", ...args],
  },
  useAddQueuePairs: () => ({ mutate: mocks.addPairs, isPending: false }),
  usePairwiseResults: (...args) => mocks.pairwiseResults(...args),
  useAnnotationQueueDetail: () => ({
    data: {
      mode: mocks.mode,
      id: "queue-1",
      name: "Bulk Queue",
      status: "active",
      viewer_role: mocks.role,
      viewer_roles: [mocks.role],
      annotators: [
        {
          user_id: "manager-1",
          role: "manager",
          roles: ["manager"],
          name: "Manager",
          email: "manager@example.com",
        },
        {
          user_id: "annotator-1",
          role: "annotator",
          roles: ["annotator"],
          name: "Alice",
          email: "alice@example.com",
        },
        {
          user_id: "annotator-2",
          role: "annotator",
          roles: ["annotator"],
          name: "Bob",
          email: "bob@example.com",
        },
      ],
    },
  }),
  useQueueItems: () => ({
    data: {
      results: [
        {
          id: "item-1",
          status: "pending",
          review_status: "pending_review",
          source_type: "trace",
          created_at: "2026-01-01T00:00:00Z",
        },
        {
          id: "item-2",
          status: "completed",
          review_status: "approved",
          source_type: "trace",
          created_at: "2026-01-02T00:00:00Z",
        },
      ],
      count: 2,
    },
    isLoading: false,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  }),
  useQueueProgress: () => ({
    data: { total: 1, skipped: 0 },
  }),
  useRemoveQueueItem: () => ({ mutate: vi.fn() }),
  useBulkRemoveQueueItems: () => ({
    mutate: mocks.bulkRemove,
    isPending: false,
  }),
  useBulkReviewItems: () => ({ mutate: vi.fn(), isPending: false }),
  useAssignQueueItems: () => ({
    mutate: mocks.assignItems,
    isPending: false,
  }),
  useDownloadAnnotationQueueExport: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useUpdateAnnotationQueueStatus: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("../../items/queue-items-table", () => ({
  default: ({ data = [], onSelectToggle, onSelectAll }) => (
    <div>
      <label htmlFor="select-all-items">Select all items</label>
      <input
        id="select-all-items"
        type="checkbox"
        onChange={() => onSelectAll()}
      />
      {data.map((item, index) => (
        <div key={item.id}>
          <label htmlFor={`select-${item.id}`}>{`Item ${index + 1}`}</label>
          <input
            id={`select-${item.id}`}
            type="checkbox"
            onChange={() => onSelectToggle(item.id)}
          />
        </div>
      ))}
    </div>
  ),
}));

vi.mock("../../items/add-items-dialog", () => ({
  default: () => null,
}));

vi.mock("../export-to-dataset-dialog", () => ({
  default: () => null,
}));

vi.mock("../queue-settings-tab", () => ({
  default: () => null,
}));

vi.mock("../queue-analytics-tab", () => ({
  default: () => null,
}));

vi.mock("../queue-agreement-tab", () => ({
  default: () => null,
}));

vi.mock("../automation-rules-tab", () => ({
  default: () => null,
}));

describe("QueueDetailView pairwise summary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.mode = "pairwise";
    mocks.role = "manager";
    mocks.pairwiseResults.mockReturnValue({
      data: { overall: {
        total_votes: 4,
        tallies: { A: 1, B: 2, tie: 1 },
        win_rates: { A: 0.25, B: 0.5, tie: 0.25 },
      } },
    });
  });

  it("shows backend tallies and win rates including ties in the denominator", () => {
    render(<QueueDetailView />);
    const table = screen.getByRole("table", { name: /pairwise results/i });
    expect(within(table).getByRole("row", { name: "A 1 25%" })).toBeVisible();
    expect(within(table).getByRole("row", { name: "B 2 50%" })).toBeVisible();
    expect(within(table).getByRole("row", { name: "Tie 1 25%" })).toBeVisible();
    expect(screen.getByText(/total votes: 4/i)).toBeVisible();
    expect(mocks.pairwiseResults).toHaveBeenCalledWith("queue-1");
  });

  it("shows zero vote shares for an empty queue", () => {
    mocks.pairwiseResults.mockReturnValue({ data: { overall: {
      total_votes: 0,
      tallies: { A: 0, B: 0, tie: 0 },
      win_rates: { A: 0, B: 0, tie: 0 },
    } } });
    render(<QueueDetailView />);
    const table = screen.getByRole("table", { name: /pairwise results/i });
    expect(within(table).getAllByText("0%")).toHaveLength(3);
  });

  it("surfaces results failures rather than claiming there are no votes", () => {
    mocks.pairwiseResults.mockReturnValue({ isError: true });
    render(<QueueDetailView />);
    expect(screen.getByRole("alert")).toHaveTextContent(/couldn't load pairwise results/i);
    expect(screen.queryByText(/total votes: 0/i)).not.toBeInTheDocument();
  });
});

describe("QueueDetailView bulk assignment", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.mode = "standard";
    mocks.role = "manager";
    mocks.assignItems.mockImplementation((_payload, options) => {
      options?.onSuccess?.();
    });
  });

  it("lets managers assign selected items to annotators in one request", async () => {
    const user = userEvent.setup();
    render(<QueueDetailView />);

    await user.click(screen.getByLabelText("Item 1"));
    expect(
      screen.getByRole("button", { name: /assign selected \(1\)/i }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: /remove selected \(1\)/i }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: /approve selected \(1\)/i }),
    ).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: /assign selected \(1\)/i }),
    );
    await user.click(screen.getByLabelText("Alice"));
    await user.click(screen.getByRole("button", { name: /^assign$/i }));

    expect(mocks.assignItems).toHaveBeenCalledWith(
      {
        queueId: "queue-1",
        itemIds: ["item-1"],
        userIds: ["annotator-1"],
        action: "set",
        assignees: expect.arrayContaining([
          expect.objectContaining({
            user_id: "annotator-1",
            name: "Alice",
          }),
        ]),
      },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("returns from settings to the queue item list without leaving the queue", async () => {
    const user = userEvent.setup();
    render(<QueueDetailView />);

    await user.click(screen.getByRole("tab", { name: /settings/i }));
    await user.click(
      screen.getByRole("button", { name: /back to queue items/i }),
    );

    expect(screen.getByLabelText("Item 1")).toBeVisible();
    expect(mocks.navigate).not.toHaveBeenCalled();
  });
});

describe("QueueDetailView bulk remove", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.mode = "standard";
    mocks.role = "manager";
    mocks.bulkRemove.mockImplementation((_payload, options) => {
      options?.onSuccess?.();
    });
  });

  it("asks for confirmation and states the cost before removing selected items", async () => {
    const user = userEvent.setup();
    render(<QueueDetailView />);

    await user.click(screen.getByLabelText("Select all items"));
    await user.click(
      screen.getByRole("button", { name: /remove selected \(2\)/i }),
    );

    // W10-ANNOTATIONS-U01: one click must not delete items and their annotations.
    expect(mocks.bulkRemove).not.toHaveBeenCalled();

    const dialog = screen.getByRole("dialog", {
      name: /remove 2 items from this queue\?/i,
    });
    expect(
      within(dialog).getByText(
        /their annotations, notes and review comments are deleted too\. this can't be undone\./i,
      ),
    ).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /^cancel$/i }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(mocks.bulkRemove).not.toHaveBeenCalled();

    await user.click(
      screen.getByRole("button", { name: /remove selected \(2\)/i }),
    );
    const confirmDialog = screen.getByRole("dialog", {
      name: /remove 2 items from this queue\?/i,
    });
    await user.click(
      within(confirmDialog).getByRole("button", { name: /^remove 2 items$/i }),
    );

    expect(mocks.bulkRemove).toHaveBeenCalledTimes(1);
    expect(mocks.bulkRemove).toHaveBeenCalledWith(
      { queueId: "queue-1", itemIds: ["item-1", "item-2"] },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: /remove selected/i }),
    ).not.toBeInTheDocument();
  });
});

describe("QueueDetailView pairwise entry points", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.mode = "pairwise";
    mocks.role = "manager";
    mocks.pairwiseResults.mockReturnValue({ data: { overall: {
      total_votes: 0, tallies: { A: 0, B: 0, tie: 0 }, win_rates: { A: 0, B: 0, tie: 0 },
    } } });
  });

  it("adds an explicit source pair rather than ordinary items", async () => {
    const user = userEvent.setup();
    render(<QueueDetailView />);
    await user.click(screen.getByRole("button", { name: /^add pair$/i }));
    const dialog = within(screen.getByRole("dialog", { name: /add pair/i }));
    expect(dialog.getByRole("button", { name: /^add pair$/i })).toBeDisabled();
    await user.type(dialog.getByRole("textbox", { name: "Source A ID" }), "row-a");
    await user.type(dialog.getByRole("textbox", { name: "Source B ID" }), "row-b");
    await user.click(dialog.getByRole("button", { name: /^add pair$/i }));
    expect(mocks.addPairs).toHaveBeenCalledWith({ queueId: "queue-1", pairs: [{
      source_a: { source_type: "dataset_row", source_id: "row-a" },
      source_b: { source_type: "dataset_row", source_id: "row-b" },
    }] }, expect.any(Object));
  });

  it("lets reviewer-only pairwise members start annotating without changing standard permissions", () => {
    mocks.role = "reviewer";
    const { unmount } = render(<QueueDetailView />);
    expect(screen.getByRole("button", { name: /start annotating/i })).toBeVisible();
    unmount();
    mocks.mode = "standard";
    render(<QueueDetailView />);
    expect(screen.queryByRole("button", { name: /start annotating/i })).not.toBeInTheDocument();
  });
});
