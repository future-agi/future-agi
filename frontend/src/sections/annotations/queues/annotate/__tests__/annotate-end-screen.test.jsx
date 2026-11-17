// A finished annotation queue ends on a clear end
// screen in the workspace itself — rows done, rows skipped with a way back,
// where the export is (or who can export) and a way back to the queue —
// instead of a toast and a jump to the queue page. With Show completed on,
// finishing the last skipped row never lands on an already-done row.
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
  auth: { role: "workspace_member" },
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
  useAuthContext: () => ({ user: mocks.user, role: mocks.auth.role }),
}));
vi.mock("src/hooks/use-socket", () => ({ useSocket: () => ({}) }));
vi.mock("src/sections/common/ux/useShortcuts", () => ({ default: () => {} }));

// Leave the workspace, its queue API hooks, its end screen and the design
// system empty state real. The item editors are replaced by stubs that expose
// the same Submit / Skip callbacks the real header and label panel call.
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
vi.mock("../../view/export-to-dataset-dialog", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ open }) =>
      open
        ? createElement("div", { "data-testid": "export-to-dataset-drawer" })
        : null,
  };
});
vi.mock("src/sections/common/RowPanel/ShortcutSheet", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Review/ExperimentReviewSummary", () => ({
  default: () => null,
}));
/* eslint-enable react/prop-types */

const QUEUE_ID = "queue-Q";
const QUEUE_API = `/model-hub/annotation-queues/${QUEUE_ID}/`;
const QUEUE_PATH = `/dashboard/annotations/queues/${QUEUE_ID}`;
const WORKSPACE_PATH = `${QUEUE_PATH}/annotate`;
const NEXT_ITEM_API = `${QUEUE_API}items/next-item/`;
const PROGRESS_API = `${QUEUE_API}progress/`;
const itemApi = (itemId, action) => `${QUEUE_API}items/${itemId}/${action}/`;
const OPEN_STATUSES = ["pending", "in_progress", "skipped"];
// The toast matches the end screen: it says the row was saved and leaves
// "what is left" to the screen (which may still list open rows).
const SUBMIT_SAVED_LAST = "Saved.";
const SUBMIT_SAVED_NEXT = "Saved. Moved to next item.";
const NO_EXPORT_SENTENCE =
  "Workspace members and admins can add corrected items to a dataset from Export on the queue page.";

const response = (result) => ({ data: { result } });

// A small in-memory queue that answers like the annotation queue views:
// next-item / complete / skip return the next row AFTER the cursor (no wrap),
// counting completed rows only when include_completed is sent, and the queue
// turns completed once every row is completed.
let server;

function seedQueue(statuses, { queueStatus = "active", roles } = {}) {
  server = {
    order: Object.keys(statuses),
    status: { ...statuses },
    queueStatus,
    roles: roles || ["annotator"],
    requiresReview: false,
  };
}

const countStatus = (status) =>
  server.order.filter((itemId) => server.status[itemId] === status).length;

function nextAfter(cursorId, includeCompleted) {
  const allowed = includeCompleted
    ? [...OPEN_STATUSES, "completed"]
    : OPEN_STATUSES;
  const start = cursorId ? server.order.indexOf(cursorId) + 1 : 0;
  const itemId = server.order
    .slice(start)
    .find((id) => allowed.includes(server.status[id]));
  return itemId ? { id: itemId, status: server.status[itemId] } : null;
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
    viewer_role: server.roles[0],
    viewer_roles: server.roles,
    annotators: [
      { user_id: mocks.user.id, role: server.roles[0], roles: server.roles },
    ],
  };
}

function progressBody() {
  const counts = {
    total: server.order.length,
    completed: countStatus("completed"),
    pending: countStatus("pending"),
    in_progress: countStatus("in_progress"),
    skipped: countStatus("skipped"),
  };
  return { ...counts, user_progress: { ...counts } };
}

function detailBody(itemId) {
  const status = server.status[itemId];
  return {
    item: { id: itemId, status, source_type: "trace" },
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

function finishItem(itemId, status, payload) {
  server.status[itemId] = status;
  if (server.order.every((id) => server.status[id] === "completed")) {
    server.queueStatus = "completed";
  }
  return nextAfter(itemId, Boolean(payload?.include_completed));
}

let client;

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname + location.search}
    </output>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.auth.role = "workspace_member";
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  mocks.get.mockImplementation(async (url, options) => {
    const params = options?.params || {};
    if (url === QUEUE_API) return response(queueBody());
    if (url === PROGRESS_API) return response(progressBody());
    if (url === NEXT_ITEM_API) {
      if (params.view_mode === "review") return response({ item: null });
      const includeCompleted =
        Boolean(params.include_completed) || server.queueStatus === "completed";
      return response({
        item: nextAfter(cursorOf(params.exclude), includeCompleted),
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
        next_item: finishItem(itemId, "completed", payload),
      });
    }
    if (itemId && url === itemApi(itemId, "skip")) {
      return response({
        skipped_item_id: itemId,
        next_item: finishItem(itemId, "skipped", payload),
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
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

const requestedDetail = (itemId) =>
  mocks.get.mock.calls.some(
    ([url]) => url === itemApi(itemId, "annotate-detail"),
  );
const queueLoads = () =>
  mocks.get.mock.calls.filter(([url]) => url === QUEUE_API).length;
const toastTexts = () => mocks.snackbar.mock.calls.map(([message]) => message);
const completePayload = (itemId) =>
  mocks.post.mock.calls.find(
    ([url]) => url === itemApi(itemId, "complete"),
  )?.[1];

async function submitItem(user, itemId, label = "Submit & Next") {
  await user.click(
    await screen.findByRole("button", { name: `${label} ${itemId}` }),
  );
}

describe("The last Submit & Next ends on the end screen in place", () => {
  it("shows rows done, rows skipped with a way back, the export and Back to Queue, and focuses the heading", async () => {
    seedQueue({
      "item-1": "pending",
      "item-2": "pending",
      "item-3": "pending",
    });
    const user = renderWorkspace(`${WORKSPACE_PATH}?mode=annotate`);

    await submitItem(user, "item-1");
    await screen.findByRole("button", { name: "Submit & Next item-2" });
    await user.click(screen.getByRole("button", { name: "Skip" }));
    await submitItem(user, "item-3");

    const heading = await screen.findByRole("heading", {
      name: "End of the queue",
    });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(await screen.findByText("2 of 3 rows done.")).toBeVisible();
    expect(screen.getByText("1 row skipped.")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Annotate skipped rows" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Add corrected items to a dataset" }),
    ).toBeEnabled();
    expect(screen.getByRole("button", { name: "Back to Queue" })).toBeEnabled();

    // In place: the workspace URL is kept, the toast still offers Undo, and
    // the first open (skipped) row does not replace the end screen.
    expect(screen.getByTestId("location")).toHaveTextContent(
      new RegExp(`^${WORKSPACE_PATH}\\?mode=annotate$`),
    );
    expect(toastTexts()).toContain(SUBMIT_SAVED_LAST);
    expect(screen.queryByTestId("annotation-workspace-root")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Submit & Next item-2" }),
    ).toBeNull();
  });

  it("takes the annotator back to the skipped row and ends on All done once it is saved, even after the queue completes", async () => {
    seedQueue({ "item-1": "pending", "item-2": "pending" });
    const user = renderWorkspace(`${WORKSPACE_PATH}?mode=annotate`);

    await screen.findByRole("button", { name: "Submit & Next item-1" });
    await user.click(screen.getByRole("button", { name: "Skip" }));
    await submitItem(user, "item-2");
    await user.click(
      await screen.findByRole("button", { name: "Annotate skipped rows" }),
    );

    await screen.findByRole("button", { name: "Submit & Next item-1" });
    const queueLoadsBeforeLastSubmit = queueLoads();
    await submitItem(user, "item-1");
    expect(server.queueStatus).toBe("completed");

    const heading = await screen.findByRole("heading", { name: "All done" });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(await screen.findByText("2 of 2 rows done.")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Annotate skipped rows" }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Add corrected items to a dataset" }),
    ).toBeEnabled();

    // The auto-completed queue still ends on the end screen, never on
    // "Queue Not Active".
    await waitFor(() =>
      expect(queueLoads()).toBeGreaterThan(queueLoadsBeforeLastSubmit),
    );
    expect(screen.queryByText("Queue Not Active")).toBeNull();
    expect(screen.getByRole("heading", { name: "All done" })).toBeVisible();
    expect(screen.getByTestId("location")).toHaveTextContent(
      new RegExp(`^${WORKSPACE_PATH}\\?mode=annotate$`),
    );
  });
});

describe("With Show completed on", () => {
  it("ends on the end screen after the last skipped row instead of an already-done row", async () => {
    seedQueue({
      "item-1": "completed",
      "item-2": "skipped",
      "item-3": "completed",
    });
    const user = renderWorkspace(
      `${WORKSPACE_PATH}?mode=annotate&includeCompleted=true&itemId=item-2`,
    );

    await submitItem(user, "item-2");

    const heading = await screen.findByRole("heading", { name: "All done" });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(await screen.findByText("3 of 3 rows done.")).toBeVisible();
    expect(toastTexts()).not.toContain(SUBMIT_SAVED_NEXT);
    expect(toastTexts()).toContain(SUBMIT_SAVED_LAST);
    expect(requestedDetail("item-3")).toBe(false);
    expect(completePayload("item-2")?.include_completed).toBeUndefined();
  });

  it("moves to the next row that still needs an answer, past already-done rows", async () => {
    seedQueue({
      "item-1": "completed",
      "item-2": "skipped",
      "item-3": "completed",
      "item-4": "pending",
    });
    const user = renderWorkspace(
      `${WORKSPACE_PATH}?mode=annotate&includeCompleted=true&itemId=item-2`,
    );

    await submitItem(user, "item-2");

    expect(
      await screen.findByRole("button", { name: "Submit & Next item-4" }),
    ).toBeVisible();
    expect(toastTexts()).toContain(SUBMIT_SAVED_NEXT);
    expect(requestedDetail("item-3")).toBe(false);
  });

  it("keeps stepping through done rows when the annotator updates a done row", async () => {
    seedQueue({
      "item-1": "completed",
      "item-2": "completed",
      "item-3": "pending",
    });
    const user = renderWorkspace(
      `${WORKSPACE_PATH}?mode=annotate&includeCompleted=true&itemId=item-1`,
    );

    await submitItem(user, "item-1", "Update & Next");

    expect(
      await screen.findByRole("button", { name: "Update & Next item-2" }),
    ).toBeVisible();
    expect(completePayload("item-1")?.include_completed).toBe(true);
  });
});

describe("Export on the end screen is role-aware", () => {
  it.each(["Viewer", "workspace_viewer"])(
    "tells a %s who can add the corrected items to a dataset and where, instead of a button",
    async (role) => {
      mocks.auth.role = role;
      seedQueue({ "item-1": "completed", "item-2": "completed" });
      renderWorkspace(`${WORKSPACE_PATH}?mode=annotate`);

      expect(
        await screen.findByRole("heading", { name: "All done" }),
      ).toBeVisible();
      expect(await screen.findByText("2 of 2 rows done.")).toBeVisible();
      expect(screen.getByText(NO_EXPORT_SENTENCE)).toBeVisible();
      expect(
        screen.queryByRole("button", {
          name: "Add corrected items to a dataset",
        }),
      ).toBeNull();
      expect(
        screen.getByRole("button", { name: "Back to Queue" }),
      ).toBeEnabled();
    },
  );

  it("opens the dataset export for a workspace member", async () => {
    seedQueue({ "item-1": "completed", "item-2": "completed" });
    const user = renderWorkspace(`${WORKSPACE_PATH}?mode=annotate`);

    await user.click(
      await screen.findByRole("button", {
        name: "Add corrected items to a dataset",
      }),
    );

    expect(screen.getByTestId("export-to-dataset-drawer")).toBeInTheDocument();
    expect(screen.queryByText(NO_EXPORT_SENTENCE)).toBeNull();
  });
});

describe("Review workspace", () => {
  it("still says nothing is waiting for review, with no annotator actions", async () => {
    seedQueue({ "item-1": "completed" }, { roles: ["reviewer"] });
    renderWorkspace(`${WORKSPACE_PATH}?mode=review`);

    expect(
      await screen.findByRole("heading", { name: "All done" }),
    ).toBeVisible();
    expect(screen.getByText("No items are waiting for review.")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Annotate skipped rows" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", {
        name: "Add corrected items to a dataset",
      }),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Back to Queue" })).toBeEnabled();
  });
});
