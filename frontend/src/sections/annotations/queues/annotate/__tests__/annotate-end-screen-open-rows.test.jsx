// The end screen says "All done" only when no
// row is open anywhere in the queue. Before it shows, it checks the whole
// queue from the top (the work cursor never wraps, so a walk that started
// partway down, or on a rework row, leaves open rows above it) and waits for
// fresh counts. Open rows get a button labelled by what it opens; a
// completed queue never opens a done row; Skip never lands on a done row;
// rows added while the end screen is open show up when the window regains
// focus; and the toast no longer says the queue has no more items.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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

// Same harness as annotate-end-screen.test.jsx: the workspace, its queue API
// hooks, its end screen and the design-system empty state stay real; the item
// editors are stubs that call the same Submit / Skip callbacks.
/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../annotate-header", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ onSkip }) =>
      createElement("button", { type: "button", onClick: onSkip }, "Skip"),
  };
});
vi.mock("../annotate-footer", () => ({ default: () => null }));
vi.mock("../content-panel", () => ({ default: () => null }));
vi.mock("../resizable-split", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ left, right }) => createElement("div", null, left, right),
  };
});
vi.mock("../label-panel", async () => {
  const { createElement, forwardRef } = await import("react");
  const LabelPanelStub = forwardRef(function LabelPanelStub(props, _ref) {
    return createElement(
      "button",
      {
        type: "button",
        onClick: () =>
          props.onSubmit({
            annotations: [{ label_id: "label-1", value: "Good" }],
            notes: "",
            itemNotes: "",
          }),
      },
      `${props.submitLabel} ${props.detailItemId}`,
    );
  });
  return { default: LabelPanelStub };
});
vi.mock("../annotation-comparison-panel", () => ({ default: () => null }));
vi.mock("../discussion-panel", () => ({ CollaborationDrawer: () => null }));
vi.mock("../item-assignment-panel", () => ({ default: () => null }));
vi.mock("../ReviewSummaryDrawer", () => ({ default: () => null }));
vi.mock("../../view/export-to-dataset-dialog", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/RowPanel/ShortcutSheet", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Review/ExperimentReviewSummary", () => ({
  default: () => null,
}));
/* eslint-enable react/prop-types */

const QUEUE_ID = "queue-Q";
const QUEUE_API = `/model-hub/annotation-queues/${QUEUE_ID}/`;
const WORKSPACE_PATH = `/dashboard/annotations/queues/${QUEUE_ID}/annotate`;
const NEXT_ITEM_API = `${QUEUE_API}items/next-item/`;
const PROGRESS_API = `${QUEUE_API}progress/`;
const itemApi = (itemId, action) => `${QUEUE_API}items/${itemId}/${action}/`;
const OPEN_STATUSES = ["pending", "in_progress", "skipped"];
const OLD_LAST_TOAST = "Saved. No more items in this queue.";
const ANY_OPEN_ROWS_BUTTON = /^Annotate (remaining|skipped) rows$/;

const response = (result) => ({ data: { result } });

// An in-memory queue that answers like the annotation queue views. next-item,
// complete and skip return the next row AFTER the cursor (the server cursor
// never wraps). Without a cursor, next-item starts from the top and returns a
// row sent back for rework first (_get_next_pending_item). Done rows count
// only with include_completed, which next-item forces for a completed queue.
// A queue that needs review moves a submitted row to in_progress +
// pending_review; otherwise it is completed, and the queue completes once
// every row is completed. Progress counts mirror the progress view.
let server;

function seedQueue(
  statuses,
  { queueStatus = "active", requiresReview = false, review = {} } = {},
) {
  server = {
    order: Object.keys(statuses),
    status: { ...statuses },
    review: { ...review },
    queueStatus,
    requiresReview,
    progressGate: null,
  };
}

const countStatus = (status) =>
  server.order.filter((itemId) => server.status[itemId] === status).length;

const itemBody = (itemId) => ({
  id: itemId,
  status: server.status[itemId],
  review_status: server.review[itemId] || null,
});

function nextAfter(cursorId, { includeCompleted, excludeReviewStatus } = {}) {
  const allowed = includeCompleted
    ? [...OPEN_STATUSES, "completed"]
    : OPEN_STATUSES;
  const candidates = server.order.filter(
    (itemId) =>
      allowed.includes(server.status[itemId]) &&
      !(excludeReviewStatus && server.review[itemId] === excludeReviewStatus),
  );
  let itemId;
  if (cursorId) {
    const cursorIndex = server.order.indexOf(cursorId);
    itemId = candidates.find((id) => server.order.indexOf(id) > cursorIndex);
  } else {
    itemId =
      candidates.find((id) => server.review[id] === "rejected") ||
      candidates[0];
  }
  return itemId ? itemBody(itemId) : null;
}

function cursorOf(exclude) {
  const ids = Array.isArray(exclude)
    ? exclude
    : String(exclude || "")
        .split(",")
        .filter(Boolean);
  return ids[ids.length - 1] || null;
}

function queueBody() {
  return {
    id: QUEUE_ID,
    name: "Support replies",
    status: server.queueStatus,
    requires_review: server.requiresReview,
    viewer_role: "annotator",
    viewer_roles: ["annotator"],
    annotators: [
      { user_id: mocks.user.id, role: "annotator", roles: ["annotator"] },
    ],
  };
}

function progressBody() {
  const inReview = server.order.filter(
    (itemId) => server.review[itemId] === "pending_review",
  ).length;
  const counts = {
    total: server.order.length,
    completed: countStatus("completed"),
    pending: countStatus("pending"),
    in_progress: Math.max(countStatus("in_progress") - inReview, 0),
    in_review: inReview,
    skipped: countStatus("skipped"),
  };
  return {
    ...counts,
    user_progress: { ...counts, completed: counts.completed + inReview },
  };
}

function detailBody(itemId) {
  const status = server.status[itemId];
  return {
    item: {
      id: itemId,
      status,
      review_status: server.review[itemId] || null,
      source_type: "trace",
    },
    labels: [{ id: "label-1", name: "Quality", type: "categorical" }],
    annotations:
      status === "completed"
        ? [{ annotator: mocks.user.id, label_id: "label-1", value: "Good" }]
        : [],
    queue: {
      id: QUEUE_ID,
      name: "Support replies",
      status: server.queueStatus,
    },
    progress: {
      total: server.order.length,
      completed: countStatus("completed"),
      current_position: server.order.indexOf(itemId) + 1,
    },
    next_item_id: null,
    prev_item_id: null,
  };
}

function nextFromPayload(itemId, payload) {
  return nextAfter(itemId, {
    includeCompleted: Boolean(payload?.include_completed),
    excludeReviewStatus: payload?.exclude_review_status,
  });
}

function completeItem(itemId, payload) {
  if (server.requiresReview) {
    server.status[itemId] = "in_progress";
    server.review[itemId] = "pending_review";
  } else {
    server.status[itemId] = "completed";
  }
  if (server.order.every((id) => server.status[id] === "completed")) {
    server.queueStatus = "completed";
  }
  return nextFromPayload(itemId, payload);
}

let client;

beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  mocks.get.mockImplementation(async (url, options) => {
    const params = options?.params || {};
    if (url === QUEUE_API) return response(queueBody());
    if (url === PROGRESS_API) {
      if (server.progressGate) await server.progressGate;
      return response(progressBody());
    }
    if (url === NEXT_ITEM_API) {
      if (params.view_mode === "review") return response({ item: null });
      return response({
        item: nextAfter(cursorOf(params.exclude), {
          includeCompleted:
            Boolean(params.include_completed) ||
            server.queueStatus === "completed",
          excludeReviewStatus: params.exclude_review_status,
        }),
      });
    }
    const detailId = server.order.find(
      (itemId) => url === itemApi(itemId, "annotate-detail"),
    );
    if (detailId) return response(detailBody(detailId));
    if (url.startsWith(`${QUEUE_API}items/`) && url.endsWith("/discussion/")) {
      return response({ review_comments: [], review_threads: [] });
    }
    throw new Error(`Unexpected GET ${url}`);
  });
  mocks.post.mockImplementation(async (url, payload) => {
    const itemId = server.order.find((id) =>
      url.startsWith(`${QUEUE_API}items/${id}/`),
    );
    if (itemId && url === itemApi(itemId, "annotations/submit")) {
      return response({ submitted: true });
    }
    if (itemId && url === itemApi(itemId, "complete")) {
      return response({
        completed_item_id: itemId,
        next_item: completeItem(itemId, payload),
      });
    }
    if (itemId && url === itemApi(itemId, "skip")) {
      server.status[itemId] = "skipped";
      return response({
        skipped_item_id: itemId,
        next_item: nextFromPayload(itemId, payload),
      });
    }
    throw new Error(`Unexpected POST ${url}`);
  });
});

afterEach(() => {
  cleanup();
  client.clear();
});

function renderWorkspace(initialUrl) {
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
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

const requestedDetail = (itemId) =>
  mocks.get.mock.calls.some(
    ([url]) => url === itemApi(itemId, "annotate-detail"),
  );
const toastTexts = () => mocks.snackbar.mock.calls.map(([message]) => message);
const postPayload = (itemId, action) =>
  mocks.post.mock.calls.find(([url]) => url === itemApi(itemId, action))?.[1];

async function submitItem(user, itemId, label = "Submit & Next") {
  await user.click(
    await screen.findByRole("button", { name: `${label} ${itemId}` }),
  );
}

describe("Open rows above the work cursor", () => {
  it("a walk started from a row link ends on 'End of the queue' with the rows above still open, and opens them", async () => {
    seedQueue({
      "item-1": "pending",
      "item-2": "pending",
      "item-3": "pending",
    });
    // The queue page links a row as ?itemId=…, so the walk starts at item-2.
    const user = renderWorkspace(
      `${WORKSPACE_PATH}?itemId=item-2&mode=annotate`,
    );

    await submitItem(user, "item-2");
    await submitItem(user, "item-3");

    const heading = await screen.findByRole("heading", {
      name: "End of the queue",
    });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(screen.queryByRole("heading", { name: "All done" })).toBeNull();
    expect(screen.getByText("2 of 3 rows done.")).toBeVisible();
    expect(screen.getByText("1 row still open.")).toBeVisible();
    // The toast agrees with the screen: it no longer says nothing is left.
    expect(toastTexts()).toContain("Saved.");
    expect(toastTexts()).not.toContain(OLD_LAST_TOAST);

    await user.click(
      screen.getByRole("button", { name: "Annotate remaining rows" }),
    );
    await submitItem(user, "item-1");

    expect(
      await screen.findByRole("heading", { name: "All done" }),
    ).toBeVisible();
    expect(screen.getByText("3 of 3 rows done.")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: ANY_OPEN_ROWS_BUTTON }),
    ).toBeNull();
  });

  it("a walk that opened on a rework row first still reaches the pending row above the cursor", async () => {
    seedQueue(
      { "item-1": "pending", "item-2": "pending" },
      { requiresReview: true, review: { "item-2": "rejected" } },
    );
    const user = renderWorkspace(`${WORKSPACE_PATH}?mode=annotate`);

    // The server hands out the row sent back for rework before item-1.
    await submitItem(user, "item-2", "Submit for Review");

    const heading = await screen.findByRole("heading", {
      name: "End of the queue",
    });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(screen.getByText("1 row still open.")).toBeVisible();
    expect(toastTexts()).toContain("Submitted for review.");

    await user.click(
      screen.getByRole("button", { name: "Annotate remaining rows" }),
    );
    expect(
      await screen.findByRole("button", { name: "Submit for Review item-1" }),
    ).toBeVisible();
  });
});

describe("The end screen shows final counts once", () => {
  it("waits for fresh progress instead of showing the stale 'End of the queue' first", async () => {
    seedQueue({ "item-1": "pending", "item-2": "pending" });
    const user = renderWorkspace(`${WORKSPACE_PATH}?mode=annotate`);

    await screen.findByRole("button", { name: "Submit & Next item-1" });
    await user.click(screen.getByRole("button", { name: "Skip" }));
    await submitItem(user, "item-2");
    await user.click(
      await screen.findByRole("button", { name: "Annotate skipped rows" }),
    );
    await screen.findByRole("button", { name: "Submit & Next item-1" });

    let releaseProgress;
    server.progressGate = new Promise((resolve) => {
      releaseProgress = resolve;
    });
    await submitItem(user, "item-1");
    await waitFor(() =>
      expect(postPayload("item-1", "complete")).toBeDefined(),
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    // Counts from before the save (1 skipped) are never shown.
    expect(
      screen.queryByRole("heading", { name: "End of the queue" }),
    ).toBeNull();
    expect(screen.queryByText("1 row skipped.")).toBeNull();

    server.progressGate = null;
    releaseProgress();

    const heading = await screen.findByRole("heading", { name: "All done" });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(screen.getByText("2 of 2 rows done.")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: ANY_OPEN_ROWS_BUTTON }),
    ).toBeNull();
  });
});

describe("Open-row navigation never lands on a done row", () => {
  it("Skip with Show completed on goes to the next open row or the end screen, not to a done row", async () => {
    seedQueue({
      "item-1": "completed",
      "item-2": "pending",
      "item-3": "completed",
    });
    const user = renderWorkspace(
      `${WORKSPACE_PATH}?mode=annotate&includeCompleted=true&itemId=item-2`,
    );

    await screen.findByRole("button", { name: "Submit & Next item-2" });
    await user.click(screen.getByRole("button", { name: "Skip" }));

    expect(
      await screen.findByRole("heading", { name: "End of the queue" }),
    ).toBeVisible();
    expect(screen.getByText("1 row skipped.")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Annotate skipped rows" }),
    ).toBeEnabled();
    expect(requestedDetail("item-3")).toBe(false);
    expect(postPayload("item-2", "skip")?.include_completed).toBeUndefined();
  });

  it("in a completed queue it never offers a button that opens a done row, and points to the queue page", async () => {
    seedQueue(
      { "item-1": "completed", "item-2": "skipped", "item-3": "skipped" },
      { queueStatus: "completed" },
    );
    const user = renderWorkspace(
      `${WORKSPACE_PATH}?mode=annotate&itemId=item-3`,
    );

    await submitItem(user, "item-3");

    expect(
      await screen.findByRole("heading", { name: "End of the queue" }),
    ).toBeVisible();
    expect(
      screen.getByText("1 row skipped. Open skipped rows from the queue page."),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: ANY_OPEN_ROWS_BUTTON }),
    ).toBeNull();
    expect(requestedDetail("item-1")).toBe(false);
  });
});

describe("Rows added while the end screen is open", () => {
  it("re-checks the queue when the window regains focus and offers the new row", async () => {
    seedQueue({ "item-1": "pending" });
    const user = renderWorkspace(`${WORKSPACE_PATH}?mode=annotate`);

    await submitItem(user, "item-1");
    expect(
      await screen.findByRole("heading", { name: "All done" }),
    ).toBeVisible();

    // A manager adds a row; adding rows reactivates a completed queue.
    server.order.unshift("item-0");
    server.status["item-0"] = "pending";
    server.queueStatus = "active";
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });

    expect(
      await screen.findByRole("heading", { name: "End of the queue" }),
    ).toBeVisible();
    expect(await screen.findByText("1 row still open.")).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: "Annotate remaining rows" }),
    );
    expect(
      await screen.findByRole("button", { name: "Submit & Next item-0" }),
    ).toBeVisible();
  });
});
