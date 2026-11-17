import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import AnnotateWorkspaceView from "../annotate/annotate-workspace-view";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  navigate: vi.fn(),
  detail: vi.fn(),
  submitStandard: vi.fn(),
  complete: vi.fn(),
  review: vi.fn(),
  snackbar: vi.fn(),
  queue: {
    id: "queue-1", name: "Compare responses", mode: "pairwise",
    status: "active", annotations_required: 1, auto_assign: true,
    viewer_roles: ["annotator"], annotators: [],
  },
}));

vi.mock("react-router-dom", async () => ({
  ...await vi.importActual("react-router-dom"),
  useParams: () => ({ queueId: "queue-1" }),
  useNavigate: () => mocks.navigate,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "user-1" } }),
}));
vi.mock("src/hooks/use-socket", () => ({ useSocket: () => ({}) }));
vi.mock("notistack", () => ({
  enqueueSnackbar: mocks.snackbar,
  useSnackbar: () => ({ enqueueSnackbar: mocks.snackbar }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
}));
vi.mock("src/api/annotation-queues/annotation-queues", async () => ({
  ...await vi.importActual("src/api/annotation-queues/annotation-queues"),
  useAnnotationQueueDetail: () => ({ data: mocks.queue }),
  useAnnotateDetail: (...args) => mocks.detail(...args),
  useNextItem: () => ({ isFetchedAfterMount: true }),
  useQueueProgress: () => ({ data: { total: 2, completed: 0 } }),
  useSubmitAnnotations: () => ({ mutate: mocks.submitStandard }),
  useCompleteItem: () => ({ mutate: mocks.complete }),
  useSkipItem: () => ({ mutate: vi.fn() }),
  useReviewItem: () => ({ mutate: mocks.review }),
  useAssignQueueItems: () => ({ mutate: vi.fn() }),
  useItemDiscussion: () => ({ data: {} }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../annotate/annotate-header", () => ({ default: () => null }));
vi.mock("../annotate/annotate-footer", () => ({ default: () => null }));
vi.mock("../annotate/resizable-split", () => ({
  default: ({ left, right }) => <div>{left}{right}</div>,
}));
// ContentPanel has its own pairwise preview coverage; keep the workspace test
// focused on the item passed to it, the voting payload and navigation.
vi.mock("../annotate/content-panel", () => ({
  default: ({ item }) => <div data-testid="content-preview">{item?.id}</div>,
}));
vi.mock("../annotate/label-panel", () => ({ default: () => null }));
vi.mock("../annotate/annotation-comparison-panel", () => ({ default: () => null }));
vi.mock("../annotate/discussion-panel", () => ({ CollaborationDrawer: () => null }));
vi.mock("../annotate/item-assignment-panel", () => ({ default: () => null }));

function pairDetail(id) {
  return {
    item: {
      id, status: "pending", source_type: "pairwise", assigned_users: [],
      source_a: { source_type: "dataset_row", source_id: "row-a" },
      source_b: { source_type: "dataset_row", source_id: "row-b" },
      source_content: {
        type: "pairwise",
        source_a: { type: "dataset_row", cells: [] },
        source_b: { type: "dataset_row", cells: [] },
      },
    },
    queue: mocks.queue,
    annotations: [], labels: [], pairwise_annotation: null,
    progress: { total: 2, current_position: 1 },
  };
}

function renderWorkspace() {
  const client = new QueryClient({ defaultOptions: {
    queries: { retry: false }, mutations: { retry: false },
  } });
  return render(<QueryClientProvider client={client}>
    <AnnotateWorkspaceView />
  </QueryClientProvider>);
}

describe("Pairwise annotation workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/?itemId=pair-1");
    mocks.queue.auto_assign = true;
    mocks.queue.mode = "pairwise";
    mocks.queue.viewer_roles = ["annotator"];
    mocks.queue.requires_review = false;
    const details = { "pair-1": pairDetail("pair-1"), "pair-2": pairDetail("pair-2") };
    mocks.detail.mockImplementation((_queueId, itemId) => ({ data: details[itemId] }));
    mocks.post.mockResolvedValue({ data: { result: { choice: "B", status: "completed" } } });
    mocks.get.mockResolvedValue({ data: { result: { item: { id: "pair-2" } } } });
  });

  it("submits B with a comment through pairwise submit and advances without completing separately", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.type(screen.getByRole("textbox", { name: /comment/i }), "B is more precise");
    await user.click(screen.getByRole("button", { name: "B is better" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith(
      "/model-hub/annotation-queues/queue-1/items/pair-1/pairwise/submit/",
      { choice: "B", comment: "B is more precise" },
    ));
    await waitFor(() => expect(screen.getByTestId("content-preview")).toHaveTextContent("pair-2"));
    expect(mocks.get).toHaveBeenCalledWith(
      "/model-hub/annotation-queues/queue-1/items/next-item/",
      expect.objectContaining({ params: expect.objectContaining({ exclude: "pair-1" }) }),
    );
    expect(mocks.detail).toHaveBeenCalledWith("queue-1", "pair-1", expect.objectContaining({ reserve: true }));
    expect(screen.getByRole("textbox", { name: /comment/i })).toHaveValue("");
    expect(mocks.submitStandard).not.toHaveBeenCalled();
    expect(mocks.complete).not.toHaveBeenCalled();
  });

  it("offers A and Tie and returns to the queue after the final vote", async () => {
    mocks.get.mockResolvedValue({ data: { result: { item: null } } });
    const user = userEvent.setup();
    renderWorkspace();
    expect(screen.getByRole("button", { name: "A is better" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Tie" }));
    await waitFor(() => expect(mocks.navigate).toHaveBeenCalledWith("/dashboard/annotations/queues/queue-1"));
    expect(mocks.post).toHaveBeenCalledWith(expect.stringContaining("/pairwise/submit/"), { choice: "tie", comment: "" });
  });

  it("preserves the comment and item when voting fails", async () => {
    mocks.post.mockRejectedValue({ response: { status: 409, data: { detail: "Item reserved" } } });
    const user = userEvent.setup();
    renderWorkspace();
    await user.type(screen.getByRole("textbox", { name: /comment/i }), "Keep this explanation");
    await user.click(screen.getByRole("button", { name: "A is better" }));
    await waitFor(() => expect(mocks.snackbar).toHaveBeenCalledWith("Item reserved", { variant: "error" }));
    expect(screen.getByRole("textbox", { name: /comment/i })).toHaveValue("Keep this explanation");
    expect(screen.getByTestId("content-preview")).toHaveTextContent("pair-1");
    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  it("does not allow voting while the next item's detail is still stale", () => {
    mocks.detail.mockReturnValue({ data: pairDetail("previous-pair"), isFetching: true });
    renderWorkspace();
    expect(screen.getByRole("button", { name: "B is better" })).toBeDisabled();
  });
});


describe("Pairwise review and reviewer voting", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/?itemId=pair-1&mode=review");
    mocks.queue.mode = "pairwise";
    mocks.queue.viewer_roles = ["reviewer"];
    mocks.queue.requires_review = true;
    mocks.queue.auto_assign = true;
    mocks.post.mockResolvedValue({ data: { result: { choice: "A" } } });
    mocks.get.mockResolvedValue({ data: { result: { item: null } } });
    const detail = pairDetail("pair-1");
    detail.item.review_status = "pending_review";
    detail.pairwise_annotations = [
      { annotator: "other-user", annotator_name: "Other reviewer", choice: "B", comment: "More precise" },
      { annotator: "third-user", annotator_name: "Third reviewer", choice: "tie", comment: "Both correct" },
    ];
    mocks.detail.mockReturnValue({ data: detail });
  });

  it("shows pairwise submissions and lets an independent reviewer approve", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    expect(screen.getByText("More precise")).toBeVisible();
    expect(screen.getByText("Both correct")).toBeVisible();
    expect(screen.getByText("B is better")).toBeVisible();
    expect(screen.getByText("Tie")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /^approve$/i }));
    expect(mocks.review).toHaveBeenCalledWith(expect.objectContaining({
      queueId: "queue-1", itemId: "pair-1", action: "approve",
    }), expect.any(Object));
  });

  it("requires feedback when requesting changes to a pairwise vote", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    expect(screen.getByRole("button", { name: /request changes/i })).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: /review feedback/i }), "Recheck A");
    await user.click(screen.getByRole("button", { name: /request changes/i }));
    expect(mocks.review).toHaveBeenCalledWith(expect.objectContaining({
      action: "request_changes", notes: "Recheck A",
    }), expect.any(Object));
  });

  it("shows the reviewer's own saved vote and prevents self-review even with filtered submissions", () => {
    const detail = mocks.detail().data;
    detail.pairwise_annotation = { choice: "A", comment: "My answer" };
    renderWorkspace();
    expect(screen.getByText(/your saved choice: A/i)).toBeVisible();
    expect(screen.queryByRole("button", { name: /^approve$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /request changes/i })).not.toBeInTheDocument();
  });

  it("allows a reviewer-only member to enter annotate mode and cast a pairwise vote", async () => {
    window.history.replaceState({}, "", "/?itemId=pair-1&mode=annotate");
    mocks.detail.mockReturnValue({ data: pairDetail("pair-1") });
    const user = userEvent.setup();
    renderWorkspace();
    await user.click(screen.getByRole("button", { name: "A is better" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith(
      expect.stringContaining("/pairwise/submit/"), { choice: "A", comment: "" },
    ));
  });
});
