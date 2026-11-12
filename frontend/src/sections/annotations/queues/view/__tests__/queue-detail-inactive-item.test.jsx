import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import QueueDetailView from "../queue-detail-view";

const mocks = vi.hoisted(() => ({
  enqueueSnackbar: vi.fn(),
  navigate: vi.fn(),
  queryClient: {
    removeQueries: vi.fn(),
    invalidateQueries: vi.fn(),
  },
  queue: null,
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
    user: { id: "member-1", email: "member@example.com" },
  }),
}));

/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: mocks.enqueueSnackbar,
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
  useAnnotationQueueDetail: () => ({ data: mocks.queue }),
  useQueueItems: () => ({
    data: {
      results: [
        {
          id: "item-1",
          status: "pending",
          review_status: null,
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
  useAssignQueueItems: () => ({ mutate: vi.fn(), isPending: false }),
  useDownloadAnnotationQueueExport: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useUpdateAnnotationQueueStatus: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("../../items/queue-items-table", () => ({
  default: ({ data = [], onItemClick }) => (
    <div>
      {data.map((item) => (
        <button key={item.id} type="button" onClick={() => onItemClick(item)}>
          {`Open ${item.id}`}
        </button>
      ))}
    </div>
  ),
}));

vi.mock("../../items/add-items-dialog", () => ({ default: () => null }));
vi.mock("../export-to-dataset-dialog", () => ({ default: () => null }));
vi.mock("../queue-settings-tab", () => ({ default: () => null }));
vi.mock("../queue-analytics-tab", () => ({ default: () => null }));
vi.mock("../queue-agreement-tab", () => ({ default: () => null }));
vi.mock("../automation-rules-tab", () => ({ default: () => null }));
/* eslint-enable react/prop-types */

const queueWith = ({ status, roles }) => ({
  id: "queue-1",
  name: "Inactive Queue",
  status,
  viewer_role: roles[0],
  viewer_roles: roles,
  annotators: [
    {
      user_id: "member-1",
      role: roles[0],
      roles,
      name: "Member",
      email: "member@example.com",
    },
  ],
});

async function openItem() {
  const user = userEvent.setup();
  render(<QueueDetailView />);
  await user.click(screen.getByRole("button", { name: "Open item-1" }));
  expect(mocks.navigate).not.toHaveBeenCalled();
  expect(mocks.enqueueSnackbar).toHaveBeenCalledTimes(1);
  return mocks.enqueueSnackbar.mock.calls[0][0];
}

// FA-ANNOT-02: opening an item in a draft or paused queue pointed at "status in
// settings tab", while managers have an Activate button on this page and
// annotators have neither the button nor the Settings tab.
describe("FA-ANNOT-02 opening an item in an inactive queue", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it.each([
    ["draft", /this queue is a draft/i],
    ["paused", /this queue is paused/i],
  ])(
    "points a manager at the Activate button (%s)",
    async (status, stateText) => {
      mocks.queue = queueWith({ status, roles: ["manager", "annotator"] });

      const message = await openItem();

      expect(
        screen.getByRole("button", { name: /^activate$/i }),
      ).toBeInTheDocument();
      expect(message).toMatch(stateText);
      expect(message).toMatch(/select activate/i);
      expect(message).not.toMatch(/settings/i);
    },
  );

  it("tells an annotator who can activate the queue", async () => {
    mocks.queue = queueWith({ status: "draft", roles: ["annotator"] });

    const message = await openItem();

    expect(
      screen.queryByRole("button", { name: /^activate$/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /settings/i })).toBeNull();
    expect(message).toMatch(/this queue is a draft/i);
    expect(message).toMatch(/ask a queue manager to activate it/i);
    expect(message).not.toMatch(/select activate|settings/i);
  });
});
