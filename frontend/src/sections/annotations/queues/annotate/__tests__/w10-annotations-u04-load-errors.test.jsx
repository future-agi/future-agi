// W10-ANNOTATIONS-U04: an annotator opens the workspace from a link
// (?itemId=...). When the queue or the item can't load, the page says what
// happened and offers a way on, instead of an endless loading skeleton or an
// empty item with a disabled Submit.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import AnnotateWorkspaceView from "../annotate-workspace-view";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  snackbar: vi.fn(),
  user: { id: "annotator-1" },
}));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: mocks.get, post: mocks.post },
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: mocks.snackbar,
  closeSnackbar: vi.fn(),
  useSnackbar: () => ({ enqueueSnackbar: mocks.snackbar }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: mocks.user, role: "workspace_member" }),
}));
vi.mock("src/hooks/use-socket", () => ({ useSocket: () => ({}) }));
vi.mock("src/sections/common/ux/useShortcuts", () => ({ default: () => {} }));

// Leave the workspace, its queue API hooks and its error/empty screens real.
// Only the item editors inside a loaded workspace are replaced.
/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../annotate-header", () => ({ default: () => null }));
vi.mock("../annotate-footer", () => ({ default: () => null }));
vi.mock("../content-panel", () => ({ default: () => null }));
vi.mock("../label-panel", () => ({ default: () => null }));
vi.mock("../resizable-split", () => ({ default: () => null }));
vi.mock("../annotation-comparison-panel", () => ({ default: () => null }));
vi.mock("../discussion-panel", () => ({ CollaborationDrawer: () => null }));
vi.mock("../item-assignment-panel", () => ({ default: () => null }));
vi.mock("../ReviewSummaryDrawer", () => ({ default: () => null }));
vi.mock("src/sections/common/RowPanel/ShortcutSheet", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Review/ExperimentReviewSummary", () => ({
  default: () => null,
}));
/* eslint-enable react/prop-types */

const QUEUE_ID = "queue-Q";
const QUEUE_API = `/model-hub/annotation-queues/${QUEUE_ID}/`;
const QUEUES_PATH = "/dashboard/annotations/queues";
const QUEUE_PATH = `${QUEUES_PATH}/${QUEUE_ID}`;
const WORKSPACE_PATH = `${QUEUE_PATH}/annotate`;
const LINKED_ITEM_URL = `${WORKSPACE_PATH}?itemId=item-1&mode=annotate`;
const NEXT_ITEM_API = `${QUEUE_API}items/next-item/`;
const detailApi = (itemId) => `${QUEUE_API}items/${itemId}/annotate-detail/`;

const response = (result) => ({ data: { result } });
// Shape the app's axios interceptor rejects with ({ ...body, statusCode }).
const apiError = (statusCode, result) => ({
  status: false,
  result,
  statusCode,
});

const ACTIVE_QUEUE = {
  id: QUEUE_ID,
  name: "Support replies",
  status: "active",
  viewer_role: "annotator",
  viewer_roles: ["annotator"],
  annotators: [{ user_id: mocks.user.id, role: "annotator" }],
};
const itemDetail = (itemId) => ({
  item: { id: itemId, status: "pending", source_type: "trace" },
  labels: [{ id: "label-1", name: "Quality", type: "categorical" }],
  annotations: [],
  queue: { id: QUEUE_ID, name: "Support replies", status: "active" },
  progress: { total: 3, completed: 0, current_position: 1 },
});

let client;
let queueResponses;
let detailResponses;
let nextItemResponse;

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname + location.search}
    </output>
  );
}

const reply = (value) =>
  value && Object.prototype.hasOwnProperty.call(value, "statusCode")
    ? Promise.reject(value)
    : Promise.resolve(response(value));

beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  queueResponses = [ACTIVE_QUEUE];
  detailResponses = {};
  nextItemResponse = { item: null };
  mocks.get.mockImplementation(async (url) => {
    if (url === QUEUE_API) {
      const next =
        queueResponses.length > 1 ? queueResponses.shift() : queueResponses[0];
      return reply(next);
    }
    if (url === `${QUEUE_API}progress/`) {
      return response({ total: 3, completed: 0, skipped: 0 });
    }
    if (url === NEXT_ITEM_API) return reply(nextItemResponse);
    const detailMatch = Object.keys(detailResponses).find(
      (itemId) => url === detailApi(itemId),
    );
    if (detailMatch) {
      const queue = detailResponses[detailMatch];
      const next = queue.length > 1 ? queue.shift() : queue[0];
      return reply(next);
    }
    if (url.startsWith(`${QUEUE_API}items/`) && url.endsWith("/discussion/")) {
      return response({ review_comments: [], review_threads: [] });
    }
    throw new Error(`Unexpected GET ${url}`);
  });
  mocks.post.mockImplementation(async (url) => {
    throw new Error(`Unexpected POST ${url}`);
  });
});

afterEach(() => {
  cleanup();
  client.clear();
});

function renderWorkspace(initialUrl = LINKED_ITEM_URL) {
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialUrl]}>
        <Routes>
          <Route
            path="/dashboard/annotations/queues/:queueId/annotate"
            element={<AnnotateWorkspaceView />}
          />
          <Route path="*" element={null} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

const expectNotLoading = () =>
  expect(
    screen.queryByRole("status", { name: "Loading" }),
  ).not.toBeInTheDocument();

const requestedDetail = (itemId) =>
  mocks.get.mock.calls.some(([url]) => url === detailApi(itemId));

describe("W10-ANNOTATIONS-U04 annotate workspace when the queue can't load", () => {
  it("says an archived or deleted queue is no longer available and goes back to queues", async () => {
    queueResponses = [apiError(404, "Not found.")];
    const user = renderWorkspace();

    expect(
      await screen.findByRole("heading", { name: "Queue not available" }),
    ).toBeVisible();
    expect(
      screen.getByText(
        "This queue is no longer available. It may have been archived or deleted.",
      ),
    ).toBeVisible();
    expectNotLoading();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(requestedDetail("item-1")).toBe(false);

    const back = screen.getByRole("button", { name: "Back to queues" });
    expect(back).toBeEnabled();
    await user.click(back);
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        new RegExp(`^${QUEUES_PATH}$`),
      ),
    );
  });

  it("says the annotator no longer has access to the queue", async () => {
    queueResponses = [apiError(403, "Forbidden.")];
    renderWorkspace();

    expect(
      await screen.findByRole("heading", { name: "No access to this queue" }),
    ).toBeVisible();
    expect(
      screen.getByText(
        "You no longer have access to this queue. Ask a queue manager to add you.",
      ),
    ).toBeVisible();
    expectNotLoading();
    expect(screen.getByRole("button", { name: "Back to queues" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("offers Retry when the queue fails to load and opens the workspace once it loads", async () => {
    queueResponses = [apiError(500, "Internal server error"), ACTIVE_QUEUE];
    detailResponses = { "item-1": [itemDetail("item-1")] };
    const user = renderWorkspace();

    expect(
      await screen.findByRole("heading", { name: "Couldn't load this queue" }),
    ).toBeVisible();
    expectNotLoading();
    expect(screen.getByRole("button", { name: "Back to queues" })).toBeEnabled();
    // Never the raw server text.
    expect(screen.queryByText("Internal server error")).toBeNull();

    const retry = screen.getByRole("button", { name: "Retry" });
    expect(retry).toBeEnabled();
    await user.click(retry);

    expect(
      await screen.findByTestId("annotation-workspace-root"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Couldn't load this queue" }),
    ).toBeNull();
  });
});

describe("W10-ANNOTATIONS-U04 annotate workspace when the linked item can't load", () => {
  it("says a removed item is no longer in the queue and skips to the next item without a skip write", async () => {
    detailResponses = {
      "item-1": [apiError(404, "Queue item not found.")],
      "item-2": [itemDetail("item-2")],
    };
    nextItemResponse = { item: { id: "item-2" } };
    const user = renderWorkspace();

    expect(
      await screen.findByRole("heading", {
        name: "This item is no longer in the queue",
      }),
    ).toBeVisible();
    expectNotLoading();
    expect(screen.queryByTestId("annotation-workspace-root")).toBeNull();
    expect(screen.queryByText("Queue item not found.")).toBeNull();
    expect(screen.getByRole("button", { name: "Back to Queue" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();

    const skip = screen.getByRole("button", { name: "Skip to next item" });
    expect(skip).toBeEnabled();
    await user.click(skip);

    await waitFor(() =>
      expect(mocks.get).toHaveBeenCalledWith(NEXT_ITEM_API, {
        params: { exclude: "item-1" },
      }),
    );
    expect(
      await screen.findByTestId("annotation-workspace-root"),
    ).toBeInTheDocument();
    expect(requestedDetail("item-2")).toBe(true);
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("ends on All done when a removed item was the last one", async () => {
    detailResponses = { "item-1": [apiError(404, "Queue item not found.")] };
    nextItemResponse = { item: null };
    const user = renderWorkspace();

    await user.click(
      await screen.findByRole("button", { name: "Skip to next item" }),
    );

    expect(await screen.findByText("All done")).toBeVisible();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("says the annotator can't open an item they have no access to", async () => {
    detailResponses = {
      "item-1": [apiError(403, "This item was sent back to a different annotator.")],
    };
    renderWorkspace();

    expect(
      await screen.findByRole("heading", { name: "Couldn't load this item" }),
    ).toBeVisible();
    expect(
      screen.getByText(
        "You don't have access to this item. Skip to the next item to keep going.",
      ),
    ).toBeVisible();
    expectNotLoading();
    expect(screen.getByRole("button", { name: "Skip to next item" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Back to Queue" })).toBeEnabled();
  });

  it("offers Retry when the item fails to load and shows it once it loads", async () => {
    detailResponses = {
      "item-1": [apiError(502, "Bad gateway"), itemDetail("item-1")],
    };
    const user = renderWorkspace();

    expect(
      await screen.findByRole("heading", { name: "Couldn't load this item" }),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Something went wrong while loading this item. Retry, or skip to the next item.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("Bad gateway")).toBeNull();
    expect(screen.getByRole("button", { name: "Skip to next item" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Back to Queue" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(
      await screen.findByTestId("annotation-workspace-root"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Couldn't load this item" }),
    ).toBeNull();
  });

  it("keeps the reservation screen for an item another annotator holds", async () => {
    detailResponses = {
      "item-1": [
        {
          ...apiError(409, "Reserved"),
          code: "item_reserved",
          detail: "This item is reserved by another annotator.",
        },
      ],
    };
    renderWorkspace();

    expect(await screen.findByText("Item Reserved")).toBeVisible();
    expect(
      screen.getByText("This item is reserved by another annotator."),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Couldn't load this item" }),
    ).toBeNull();
  });
});
