import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import AnnotateWorkspaceView from "../annotate/annotate-workspace-view";

const state = vi.hoisted(() => ({
  fetch: vi.fn(),
  initialError: null,
  queue: { status: "active", viewer_roles: ["annotator"], annotators: [] },
  user: { id: "user-1" },
  params: new URLSearchParams("itemId=item-1&mode=annotate"),
  listen: vi.fn(() => () => {}),
}));
vi.mock("react-router-dom", async (original) => ({
  ...(await original()),
  useParams: () => ({ queueId: "queue-1" }),
  useNavigate: () => vi.fn(),
  useSearchParams: () => [state.params, vi.fn()],
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: state.user }),
}));
vi.mock("src/hooks/use-socket", () => ({
  useSocket: () => ({ addMessageListener: state.listen }),
}));
vi.mock("src/api/annotation-queues/annotation-queues", async (original) => ({
  ...(await original()),
  useAnnotationQueueDetail: () => ({ data: state.queue }),
  useQueueProgress: () => ({}),
  useNextItem: () => ({}),
  useItemDiscussion: () => ({}),
  useAnnotateDetail: (_queue, _item, options) => {
    const query = useQuery({
      ...options,
      enabled: !state.initialError && options.enabled,
      queryKey: ["workspace-test-detail"],
      queryFn: state.fetch,
      retry: false,
    });
    return state.initialError
      ? {
          error: state.initialError,
          isLoading: false,
          isFetching: false,
          refetch: query.refetch,
        }
      : query;
  },
  useSubmitAnnotations: () => ({}),
  useCompleteItem: () => ({}),
  useSkipItem: () => ({}),
  useReviewItem: () => ({}),
  useAssignQueueItems: () => ({}),
}));
vi.mock("../annotate/annotate-header", () => ({ default: () => null }));
vi.mock("../annotate/annotate-footer", () => ({ default: () => null }));
vi.mock("../annotate/content-panel", () => ({ default: () => null }));
vi.mock("../annotate/discussion-panel", () => ({
  CollaborationDrawer: () => null,
}));
vi.mock("../annotate/annotation-history", () => ({ default: () => null }));
vi.mock("../annotate/use-keyboard-shortcuts", () => ({ default: () => {} }));
vi.mock("../annotate/resizable-split", () => ({
  default: ({ right }) => right,
}));
// Keep the real LabelPanel and its three local draft states; simplify only the input widget.
vi.mock("../annotate/label-input", () => ({
  default: ({ value, onChange, labelNotes, onLabelNotesChange }) => (
    <>
      <input
        aria-label="Score"
        value={value?.value ?? ""}
        onChange={(event) => onChange({ value: event.target.value })}
      />
      <input
        aria-label="Label notes"
        value={labelNotes}
        onChange={(event) => onLabelNotesChange(event.target.value)}
      />
    </>
  ),
}));

const detail = {
  item: { id: "item-1", status: "pending", assigned_users: [] },
  queue: { name: "Synthetic queue", status: "active" },
  labels: [
    {
      id: "queue-label",
      label_id: "label-1",
      name: "Quality",
      type: "text",
      allow_notes: true,
    },
  ],
  annotations: [
    {
      label_id: "label-1",
      value: { value: "server" },
      notes: "server label note",
    },
  ],
  existing_notes: "server item note",
  progress: {},
};

beforeEach(() => {
  state.fetch.mockReset();
  state.initialError = null;
});

function mount(initialData = detail) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  if (initialData) client.setQueryData(["workspace-test-detail"], initialData);
  render(
    <QueryClientProvider client={client}>
      <AnnotateWorkspaceView />
    </QueryClientProvider>,
  );
  return client;
}

function expectDrafts() {
  expect(screen.getByLabelText("Score")).toHaveValue("draft score");
  expect(screen.getByLabelText("Label notes")).toHaveValue("draft label note");
  expect(screen.getByPlaceholderText("Add notes for this item...")).toHaveValue(
    "draft item note",
  );
}

describe("annotation workspace refetch recovery", () => {
  it.each([503, undefined])(
    "keeps all drafts through transient %s failure and retry",
    async (statusCode) => {
      state.fetch.mockResolvedValue(detail);
      const client = mount();
      await waitFor(() => expect(state.fetch).toHaveBeenCalled());
      await waitFor(() => expect(client.isFetching()).toBe(0));
      fireEvent.change(screen.getByLabelText("Score"), {
        target: { value: "draft score" },
      });
      fireEvent.change(screen.getByLabelText("Label notes"), {
        target: { value: "draft label note" },
      });
      fireEvent.change(
        screen.getByPlaceholderText("Add notes for this item..."),
        { target: { value: "draft item note" } },
      );
      state.fetch.mockRejectedValue(
        Object.assign(new Error("Temporary failure"), { statusCode }),
      );
      await act(async () =>
        client.refetchQueries({ queryKey: ["workspace-test-detail"] }),
      );
      await screen.findByRole("alert");
      expectDrafts();
      state.fetch.mockResolvedValue(detail);
      fireEvent.click(screen.getByRole("button", { name: "Try Again" }));
      await waitFor(() =>
        expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
      );
      expectDrafts();
      client.clear();
    },
  );

  it.each([403, 404])("retains initial %s recovery", async (statusCode) => {
    // Initial recovery is a rendering control; transient cases above use real query transitions.
    state.initialError = { statusCode };
    const client = mount(null);
    expect(
      await screen.findByRole("button", { name: "Back to Queue" }),
    ).toBeVisible();
    expect(screen.queryByLabelText("Score")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Try Again" }),
    ).not.toBeInTheDocument();
    client.clear();
  });
});
