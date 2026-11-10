import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import QueueDetailView from "../queue-detail-view";

const mocks = vi.hoisted(() => ({
  assignItems: vi.fn(),
  assignPending: false,
  annotators: null,
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
  useAnnotationQueueDetail: () => ({
    data: {
      id: "queue-1",
      name: "Bulk Queue",
      status: "active",
      viewer_role: "manager",
      viewer_roles: ["manager"],
      annotators: mocks.annotators ?? [
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
      ],
      count: 1,
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
  useBulkRemoveQueueItems: () => ({ mutate: vi.fn(), isPending: false }),
  useBulkReviewItems: () => ({ mutate: vi.fn(), isPending: false }),
  useAssignQueueItems: () => ({
    mutate: mocks.assignItems,
    isPending: mocks.assignPending,
  }),
  useDownloadAnnotationQueueExport: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useUpdateAnnotationQueueStatus: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("../../items/queue-items-table", () => ({
  default: ({ onSelectToggle }) => (
    <div>
      <label htmlFor="select-item-1">Item 1</label>
      <input
        id="select-item-1"
        type="checkbox"
        onChange={() => onSelectToggle("item-1")}
      />
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

describe("QueueDetailView bulk assignment", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.assignPending = false;
    mocks.annotators = null;
    mocks.assignItems.mockImplementation((_payload, options) => {
      options?.onSuccess?.();
    });
  });

  const openBulkAssignDialog = async (user) => {
    await user.click(screen.getByLabelText("Item 1"));
    await user.click(
      screen.getByRole("button", { name: /assign selected \(1\)/i }),
    );
    return screen.getByRole("dialog");
  };

  it("selects or clears every annotator from the bulk assignment dialog", async () => {
    const user = userEvent.setup();
    render(<QueueDetailView />);

    const dialog = within(await openBulkAssignDialog(user));
    const alice = dialog.getByLabelText("Alice");
    const bob = dialog.getByLabelText("Bob");
    expect(alice).not.toBeChecked();
    expect(bob).not.toBeChecked();

    await user.click(dialog.getByRole("button", { name: /^select all$/i }));
    expect(alice).toBeChecked();
    expect(bob).toBeChecked();

    await user.click(dialog.getByRole("button", { name: /^deselect all$/i }));
    expect(alice).not.toBeChecked();
    expect(bob).not.toBeChecked();

    await user.click(dialog.getByRole("button", { name: /^select all$/i }));
    await user.click(dialog.getByRole("button", { name: /^select none$/i }));
    expect(alice).not.toBeChecked();
    expect(bob).not.toBeChecked();
    expect(dialog.getByRole("button", { name: /^select all$/i })).toBeVisible();

    await user.click(bob);
    await user.click(dialog.getByRole("button", { name: /^select all$/i }));
    await user.click(dialog.getByRole("button", { name: /^assign$/i }));

    expect(mocks.assignItems).toHaveBeenCalledTimes(1);
    expect(mocks.assignItems.mock.calls[0][0].userIds).toEqual(
      expect.arrayContaining(["annotator-1", "annotator-2"]),
    );
    expect(mocks.assignItems.mock.calls[0][0].userIds).toHaveLength(2);
  });

  it("disables the select all and select none controls while assigning", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<QueueDetailView />);

    await openBulkAssignDialog(user);
    mocks.assignPending = true;
    rerender(<QueueDetailView />);

    const dialog = within(screen.getByRole("dialog"));
    expect(
      dialog.getByRole("button", { name: /^select all$/i }),
    ).toBeDisabled();
    expect(
      dialog.getByRole("button", { name: /^select none$/i }),
    ).toBeDisabled();
  });

  it("hides the select all and select none controls for a single annotator", async () => {
    mocks.annotators = [
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
    ];
    const user = userEvent.setup();
    render(<QueueDetailView />);

    const dialog = within(await openBulkAssignDialog(user));
    expect(dialog.getByLabelText("Alice")).toBeInTheDocument();
    expect(
      dialog.queryByRole("button", { name: /^select all$/i }),
    ).not.toBeInTheDocument();
    expect(
      dialog.queryByRole("button", { name: /^select none$/i }),
    ).not.toBeInTheDocument();
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
