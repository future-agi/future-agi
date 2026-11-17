import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { acceptedFields } from "src/api/contracts/strictBody";
import StartReviewDialog from "../StartReviewDialog";
import StartReviewButton from "../StartReviewButton";
import { shortDate } from "../reviewModel";

// W53-PAIRWISE-REVIEW (walk flow #53, P2). Written before the fix, static
// only (not executed in this packet). The dataset runs read (R1) sends the
// canonical lowercase run status ("completed"); the dialog kept only the
// legacy "Completed", so Run A and Run B were empty and the queue was named
// "Review ·  vs  · Nov 10". A refused Start reviewing wrote its message at
// the bottom of the scrolled form, out of sight, without the server reason.
// Head-75 cannot store a review at all, pairwise or rubric (no `metadata`
// on the queue or on an added row): whether the server stores one is each
// test's input.

const mocks = vi.hoisted(() => ({
  reviewStored: true,
  // Fields per contract definition for the contract case; null reads the
  // generated contract.
  contract: null,
  navigate: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  exampleIds: vi.fn(),
  nextItem: vi.fn(),
  createQueue: vi.fn(),
  addItems: vi.fn(),
  setStatus: vi.fn(),
  refresh: vi.fn(),
  notify: vi.fn(),
  sendInvites: vi.fn(),
}));

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => mocks.navigate,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: { develop: { datasetRuns: {} } },
}));
vi.mock("src/api/contracts/strictBody", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    acceptedFields: (definition) =>
      mocks.contract?.[definition] ?? real.acceptedFields(definition),
  };
});
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  annotationQueueEndpoints: {},
  annotationQueueKeys: { all: ["annotation-queues"] },
  postAddQueueItems: vi.fn(),
}));
vi.mock("src/api/annotation-labels/annotation-labels", () => ({
  annotationLabelEndpoints: { create: "/model-hub/annotations-labels/" },
  useAnnotationLabelsList: () => ({
    isLoading: false,
    data: { results: LABELS },
  }),
}));
vi.mock("src/api/develop/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetRuns: () => ({ data: { results: RUNS } }),
}));
vi.mock("src/api/develop/review", async (importOriginal) => ({
  ...(await importOriginal()),
  useExperimentReviewQueues: () => ({ data: [] }),
  useResultExampleIds: () => ({ data: { total: ROWS.length } }),
  useResultSummary: () => ({ data: null }),
  fetchResultExampleIds: mocks.exampleIds,
  fetchNextReviewItemId: mocks.nextItem,
}));
vi.mock("../reviewQueueApi", async (importOriginal) => ({
  ...(await importOriginal()),
  createQueueQuiet: mocks.createQueue,
  addQueueItemsQuiet: mocks.addItems,
  setQueueStatusQuiet: mocks.setStatus,
  refreshQueues: mocks.refresh,
  experimentReviewSupported: () => mocks.reviewStored,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "reviewer" } }),
}));
vi.mock("src/sections/common/ux/toast", () => ({ default: mocks.notify }));
vi.mock("../outsideInvites", () => ({ sendOutsideInvites: mocks.sendInvites }));
vi.mock("../inviteModel", () => ({ outsideProblem: () => null }));

/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/annotations/queues/components/label-picker", () => ({
  default: () => null,
}));
vi.mock("src/sections/annotations/queues/components/annotator-picker", () => ({
  default: () => null,
}));
vi.mock("../CorrectionsOption", () => ({ default: () => null }));
vi.mock("../OutsideReviewersSection", () => ({
  OutsideReviewersStart: () => null,
}));
/* eslint-enable react/prop-types */

const DATASET = "00000000-0000-4000-8000-000000000530";
const A = "00000000-0000-4000-8000-000000000531";
const B = "00000000-0000-4000-8000-000000000532";
const C = "00000000-0000-4000-8000-000000000533";
const PREFERENCE = "00000000-0000-4000-8000-000000000534";
const ROWS = [
  "00000000-0000-4000-8000-000000000541",
  "00000000-0000-4000-8000-000000000542",
  "00000000-0000-4000-8000-000000000543",
  "00000000-0000-4000-8000-000000000544",
];
// R1 canonical runs as the server orders them (newest ordinal first), with
// the server's own status words (ExperimentRun.Status).
const RUNS = [
  { run_id: C, label: "#3", name: "Run C-gpt-4o-mini", status: "failed" },
  { run_id: B, label: "#2", name: "Run B-gpt-4o-mini", status: "completed" },
  { run_id: A, label: "#1", name: "Run A-gpt-4o-mini", status: "completed" },
];
const LABELS = [
  {
    id: PREFERENCE,
    name: "Overall preference",
    settings: { kind: "pairwise" },
  },
  { id: "00000000-0000-4000-8000-000000000535", name: "Correct?" },
  { id: "00000000-0000-4000-8000-000000000536", name: "Quality" },
];
const LABEL_REFUSED =
  "One or more labels are missing or not accessible from this workspace.";
const REVIEW_UNAVAILABLE =
  "Starting a review is not available on this server: it cannot keep which run each row came from.";
const MENU_REASON =
  "Not available on this server: it cannot keep which run each row came from.";
// What head-75's add-items answers for rows that carry metadata
// (AddQueueItemSerializer is strict), as the app's axios rejects with it.
const ITEMS_REFUSED = {
  status: false,
  statusCode: 400,
  code: "invalid",
  result: "items: metadata: Unknown field.",
};

const today = () => shortDate(new Date());

beforeEach(() => {
  vi.clearAllMocks();
  mocks.reviewStored = true;
  mocks.contract = null;
  mocks.exampleIds.mockResolvedValue({
    total: ROWS.length,
    example_ids: [...ROWS],
  });
  mocks.nextItem.mockResolvedValue("item-1");
  mocks.createQueue.mockImplementation(async (payload) => ({
    id: "queue-1",
    name: payload.name,
  }));
  mocks.addItems.mockImplementation(async (_queue, items) => ({
    added: items.length,
  }));
  mocks.setStatus.mockResolvedValue({});
  mocks.sendInvites.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
});

const client = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

function mountDialog(props = {}) {
  const onClose = vi.fn();
  const queryClient = client();
  const dialog = (next = {}) => (
    <QueryClientProvider client={queryClient}>
      <StartReviewDialog
        open
        datasetId={DATASET}
        runA={A}
        runB={B}
        onClose={onClose}
        {...props}
        {...next}
      />
    </QueryClientProvider>
  );
  const view = render(dialog());
  return { onClose, rerenderDialog: (next) => view.rerender(dialog(next)) };
}

function mountButton(props = {}) {
  render(
    <QueryClientProvider client={client()}>
      <StartReviewButton
        datasetId={DATASET}
        runIds={[A, B]}
        baselineId={A}
        {...props}
      />
    </QueryClientProvider>,
  );
}

const picker = (name) => screen.getByRole("combobox", { name });
const openReviewMenu = () =>
  fireEvent.click(screen.getByRole("button", { name: "Review" }));

describe("W53 Start a review: run pickers and the queue name", () => {
  it("fills Run A and Run B with the dataset's runs, by name", () => {
    mountDialog();
    expect(picker("Run A (baseline run)")).toHaveTextContent(
      "#1 Run A-gpt-4o-mini",
    );
    expect(picker("Run B")).toHaveTextContent("#2 Run B-gpt-4o-mini");
    fireEvent.mouseDown(picker("Run B"));
    const options = within(screen.getByRole("listbox"))
      .getAllByRole("option")
      .map((o) => o.textContent);
    // A failed run has nothing to review; the finished ones are offered.
    expect(options).toEqual(["#2 Run B-gpt-4o-mini", "#1 Run A-gpt-4o-mini"]);
  });

  it("names the new queue from the picked runs", () => {
    mountDialog();
    expect(screen.getByLabelText("Queue Name")).toHaveValue(
      `Review · #2 vs #1 · ${today()}`,
    );
  });

  it("asks for two runs when a prefilled run is not one the dialog offers", async () => {
    const { onClose } = mountDialog({ runB: C });
    expect(picker("Run B")).not.toHaveTextContent("Run C");
    fireEvent.click(screen.getByRole("button", { name: "Start reviewing" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pick two runs");
    expect(mocks.createQueue).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("W53 Start reviewing: create the queue and move on, or say why not", () => {
  it("creates the queue, adds every row and opens the review page", async () => {
    const { onClose } = mountDialog();
    fireEvent.click(screen.getByRole("button", { name: "Start reviewing" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(mocks.createQueue).toHaveBeenCalledWith(
      expect.objectContaining({
        name: `Review · #2 vs #1 · ${today()}`,
        label_ids: [PREFERENCE],
        description: "Pairwise review of #2 against #1.",
      }),
    );
    const [queueId, items] = mocks.addItems.mock.calls[0];
    expect(queueId).toBe("queue-1");
    expect(items.map((i) => i.source_id)).toEqual(ROWS);
    expect(mocks.navigate).toHaveBeenCalledTimes(1);
    expect(mocks.navigate.mock.calls[0][0]).toMatch(
      new RegExp(
        `^/dashboard/develop/${DATASET}/review/queue-1\\?itemId=item-1`,
      ),
    );
  });

  it("shows the server's reason where it can be seen and keeps the dialog open", async () => {
    // The app's axios rejects with the flattened error body.
    mocks.createQueue.mockRejectedValue({
      status: false,
      code: "invalid",
      message: LABEL_REFUSED,
      result: LABEL_REFUSED,
      statusCode: 400,
    });
    const { onClose } = mountDialog();
    fireEvent.click(screen.getByRole("button", { name: "Start reviewing" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      `Could not start the review. ${LABEL_REFUSED}`,
    );
    // Not inside the scrolling form, whose end was below the fold.
    expect(alert.closest(".MuiDialogContent-root")).toBeNull();
    expect(alert.closest(".MuiDialog-paper")).not.toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Start reviewing" }),
    ).toBeEnabled();
  });

  it("never shows a server failure's own text", async () => {
    mocks.addItems.mockRejectedValue({
      status: false,
      result: "Traceback (most recent call last)",
      statusCode: 500,
    });
    mountDialog();
    fireEvent.click(screen.getByRole("button", { name: "Start reviewing" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /^Could not start the review\. Try again\.$/,
    );
  });
});

describe("W53 A review starts only where the server can store it", () => {
  it("reads the server contract: the queue and each added row must keep their runs", async () => {
    const { experimentReviewSupported } =
      await vi.importActual("../reviewQueueApi");
    // The check reads definitions the generated contract has.
    expect(acceptedFields("AnnotationQueue")).toContain("label_ids");
    expect(acceptedFields("AddQueueItem")).toContain("source_id");
    const queue = ["name", "label_ids"];
    const item = ["source_type", "source_id"];
    mocks.contract = {
      AnnotationQueue: [...queue, "metadata"],
      AddQueueItem: item,
    };
    expect(experimentReviewSupported(false)).toBe(false);
    mocks.contract = {
      AnnotationQueue: queue,
      AddQueueItem: [...item, "metadata"],
    };
    expect(experimentReviewSupported(false)).toBe(false);
    mocks.contract = {
      AnnotationQueue: [...queue, "metadata"],
      AddQueueItem: [...item, "metadata"],
    };
    expect(experimentReviewSupported(false)).toBe(true);
    // Prototype mocks store a review.
    mocks.contract = { AnnotationQueue: queue, AddQueueItem: item };
    expect(experimentReviewSupported(true)).toBe(true);
  });
});

describe("W53 where the server stores a review", () => {
  it("offers Pairwise… and Rubric… in Review", () => {
    mountButton();
    openReviewMenu();
    expect(
      screen.getByRole("menuitem", { name: /^Pairwise…/ }),
    ).not.toHaveAttribute("aria-disabled");
    expect(
      screen.getByRole("menuitem", { name: /^Rubric…/ }),
    ).not.toHaveAttribute("aria-disabled");
    expect(screen.queryByText(MENU_REASON)).not.toBeInTheDocument();
  });

  it("offers both review types and no notice in the dialog", () => {
    mountDialog();
    expect(
      screen.getByRole("button", { name: "Pairwise" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rubric" })).toBeInTheDocument();
    expect(screen.queryByText(REVIEW_UNAVAILABLE)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Start reviewing" }),
    ).toBeEnabled();
  });

  it("opens the review a link asks for", async () => {
    mountButton({ initialMode: "rubric" });
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(picker("Run")).toHaveTextContent("#2 Run B-gpt-4o-mini");
  });

  it("keeps a closing dialog as it was while it fades out", async () => {
    mountButton();
    openReviewMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /^Rubric…/ }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(picker("Run")).toHaveTextContent("#2 Run B-gpt-4o-mini");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    // Still fading out, with the rubric form it had: no line or picker
    // comes in from the default mode Review ▾ passes once it is closed.
    expect(screen.getByText("Start a review")).toBeInTheDocument();
    expect(screen.queryAllByText("Run A (baseline run)")).toHaveLength(0);
    expect(screen.queryByText(REVIEW_UNAVAILABLE)).not.toBeInTheDocument();
  });
});

describe("W53 where the server cannot store a review (head-75)", () => {
  beforeEach(() => {
    mocks.reviewStored = false;
    // Were rows ever sent, this server would refuse them.
    mocks.addItems.mockRejectedValue(ITEMS_REFUSED);
  });

  it("shows one disabled Start a review in Review, with the reason", () => {
    mountButton();
    openReviewMenu();
    const items = screen.getAllByRole("menuitem");
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveTextContent("Start a review");
    expect(items[0]).toHaveTextContent(MENU_REASON);
    expect(items[0]).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(items[0]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens nothing from a review link", () => {
    mountButton({ initialMode: "pairwise" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review" })).toBeEnabled();
  });

  it.each(["pairwise", "rubric"])(
    "says a %s review cannot start here and sends nothing",
    async (mode) => {
      const { onClose } = mountDialog({ mode });
      const notice = screen.getByText(REVIEW_UNAVAILABLE);
      // Beside the buttons, not inside the scrolling form.
      expect(notice.closest(".MuiDialogContent-root")).toBeNull();
      const start = screen.getByRole("button", { name: "Start reviewing" });
      const add = screen.getByRole("button", { name: "Add to queue" });
      expect(start).toBeDisabled();
      expect(add).toBeDisabled();
      fireEvent.click(start);
      fireEvent.click(add);
      await act(async () => {});
      expect(mocks.exampleIds).not.toHaveBeenCalled();
      expect(mocks.post).not.toHaveBeenCalled();
      expect(mocks.createQueue).not.toHaveBeenCalled();
      expect(mocks.setStatus).not.toHaveBeenCalled();
      expect(mocks.addItems).not.toHaveBeenCalled();
      expect(mocks.navigate).not.toHaveBeenCalled();
      expect(onClose).not.toHaveBeenCalled();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    },
  );

  it("keeps the same notice while the dialog fades out", () => {
    const { rerenderDialog } = mountDialog({ mode: "rubric" });
    expect(screen.getAllByText(REVIEW_UNAVAILABLE)).toHaveLength(1);
    // Review ▾ closes its dialog with open=false and its default mode.
    rerenderDialog({ open: false, mode: "pairwise" });
    expect(screen.getAllByText(REVIEW_UNAVAILABLE)).toHaveLength(1);
    expect(screen.queryAllByText("Run A (baseline run)")).toHaveLength(0);
  });
});
