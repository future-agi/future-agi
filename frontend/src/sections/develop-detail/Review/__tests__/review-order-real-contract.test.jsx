import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import StartReviewDialog from "../StartReviewDialog";
import { orderSort, seededShuffle } from "../reviewModel";

// G2A-collab-02: RED specification written before the fix; not executed in
// the static-only packet. Keep fetchResultExampleIds real so assertions see
// the serialized Results request rather than a mocked resolver's arguments.
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  summary: vi.fn(),
  createQueue: vi.fn(),
  addItems: vi.fn(),
  setStatus: vi.fn(),
  refresh: vi.fn(),
  notify: vi.fn(),
  sendInvites: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    develop: {
      datasetRuns: {
        results: (id) => `/model-hub/datasets/${id}/results/`,
      },
    },
  },
}));
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  annotationQueueEndpoints: {},
  annotationQueueKeys: { all: ["annotation-queues"] },
}));
vi.mock("src/api/develop/review", async (importOriginal) => ({
  ...(await importOriginal()),
  useExperimentReviewQueues: () => ({ data: [] }),
  useResultExampleIds: () => ({ data: { total: 3 } }),
  useResultSummary: (...args) => mocks.summary(...args),
}));
// Partial: fetchResultExampleIds reads its pages through the real runs
// helpers (fetchReadPages, readEnvelope).
vi.mock("src/api/develop/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetRuns: () => ({ data: { results: RUNS } }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "reviewer" } }),
}));
vi.mock("src/api/annotation-labels/annotation-labels", () => ({
  annotationLabelEndpoints: { create: "/unused-label-create/" },
  useAnnotationLabelsList: () => ({
    data: {
      results: [
        { id: "preference", settings: { kind: "pairwise" } },
        { id: "correct", name: "Correct?" },
        { id: "quality", name: "Quality" },
      ],
    },
  }),
}));

// The queue boundary is deliberately stubbed: accepting this payload does
// NOT certify or repair the separate backend queue metadata contract. The
// stub stores reviews so these ordering cases can submit (W53: the dialog
// starts a review only where the server stores one).
vi.mock("../reviewQueueApi", () => ({
  createQueueQuiet: mocks.createQueue,
  addQueueItemsQuiet: mocks.addItems,
  setQueueStatusQuiet: mocks.setStatus,
  refreshQueues: mocks.refresh,
  experimentReviewSupported: () => true,
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

const DATASET = "00000000-0000-4000-8000-000000000010";
const A = "00000000-0000-4000-8000-000000000011";
const B = "00000000-0000-4000-8000-000000000012";
const METRIC = "00000000-0000-4000-8000-000000000013";
const IDS = [
  "00000000-0000-4000-8000-000000000021",
  "00000000-0000-4000-8000-000000000022",
  "00000000-0000-4000-8000-000000000023",
];
const REGRESSED_IDS = [IDS[2], IDS[0], IDS[1]];
const RUNS = [
  { run_id: A, label: "#1A", status: "Completed" },
  { run_id: B, label: "#1B", status: "Completed" },
];
// Pinned from DatasetResultsQuerySerializer.sort at c982cf7f:
// futureagi/model_hub/serializers/dataset_results.py. The baseline/candidate
// precondition below is from services/results_selection.py:regressed_first.
// This is a transport contract stub, not an executed Django API test.
const RESULTS_SORTS = new Set([
  "order", "-order", "input", "-input", "regressed",
]);
const REGRESSION_HELP =
  "Regressions across metrics, cost and latency; dataset order within each group.";
const DATASET_HELP = "Dataset order; scores do not affect the order";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.summary.mockReturnValue({
    data: { metrics: [{ key: METRIC, name: "Quality" }] },
  });
  mocks.get.mockImplementation(async (url, { params }) => {
    if (url !== `/model-hub/datasets/${DATASET}/results/`)
      throw new Error("Unexpected request");
    if (!RESULTS_SORTS.has(params.sort))
      throw new Error("Unsupported Results sort");
    const runs = params.runs.split(",");
    if (
      params.sort === "regressed" &&
      (!params.baseline || !runs.some((id) => id !== params.baseline))
    )
      throw new Error("Regressed sort requires a baseline and a candidate");
    const ids = params.sort === "regressed" ? REGRESSED_IDS : IDS;
    // One complete ids_only page in the Results envelope fetchReadPages reads.
    return {
      data: {
        status: true,
        result: {
          total: ids.length,
          example_ids: [...ids],
          pagination: { page: 0, has_more: false, next: null },
          scope: { complete: true },
        },
      },
    };
  });
  mocks.createQueue.mockResolvedValue({ id: "queue-stub", name: "Review queue" });
  mocks.addItems.mockImplementation(async (_queue, items) => ({
    added: items.length,
  }));
  mocks.setStatus.mockResolvedValue({});
  mocks.sendInvites.mockResolvedValue([]);
});

function mountDialog(props = {}) {
  const onClose = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const dialog = (next = {}) => (
    <QueryClientProvider client={client}>
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
  return {
    onClose,
    rerenderDialog: (next) => view.rerender(dialog(next)),
  };
}

async function submitToStub(onClose, expectedIds) {
  fireEvent.click(screen.getByRole("button", { name: "Add to queue" }));
  await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  expect(mocks.get).toHaveBeenCalledTimes(1);
  expect(mocks.createQueue).toHaveBeenCalledTimes(1);
  expect(mocks.addItems).toHaveBeenCalledTimes(1);
  const [queueId, items] = mocks.addItems.mock.calls[0];
  expect(queueId).toBe("queue-stub");
  expect(items.map((item) => item.source_id)).toEqual(expectedIds);
  const params = mocks.get.mock.calls[0][1].params;
  expect(params.ids_only).toBe(true);
  expect(RESULTS_SORTS.has(params.sort)).toBe(true);
  expect(params.sort).not.toMatch(/^(delta|score):/);
  return params;
}

// The Order select's combobox (its label also names MUI's hidden input).
const orderPicker = () => screen.getByRole("combobox", { name: "Order" });

async function chooseOrder(label) {
  fireEvent.mouseDown(orderPicker());
  fireEvent.click(await screen.findByRole("option", { name: label }));
}

describe("review ordering uses the real Results vocabulary", () => {
  it.each([
    ["regressions", { runA: A, runB: B }, "regressed"],
    ["regressions", { runA: A, runB: B, metric: METRIC }, "regressed"],
    ["regressions", { runA: A, runB: B, mode: "rubric", metric: METRIC }, "order"],
    ["regressions", { runB: B, metric: METRIC }, "order"],
    ["regressions", { runA: A }, "order"],
    ["regressions", { runA: A, runB: A, metric: METRIC }, "order"],
    ["dataset", { runA: A, runB: B }, "order"],
    ["random", { runA: A, runB: B }, "order"],
  ])("maps %s with %j to %s", (order, context, expected) => {
    expect(orderSort(order, context)).toBe(expected);
  });

  it.each([true, false])(
    "submits a default pair as regressed with summary metrics available=%s",
    async (hasMetrics) => {
      if (!hasMetrics) mocks.summary.mockReturnValue({ data: null });
      const { onClose } = mountDialog();
      expect(orderPicker()).toHaveTextContent("Regressions first");
      expect(screen.getByText(REGRESSION_HELP)).toBeInTheDocument();
      expect(screen.queryByText(/Lowest first:|By Quality/)).not.toBeInTheDocument();
      const params = await submitToStub(onClose, REGRESSED_IDS);
      expect(params).toMatchObject({
        runs: `${A},${B}`,
        baseline: A,
        sort: "regressed",
      });
    },
  );

  it("uses dataset order for a scored single-run rubric without a regression option", async () => {
    const { onClose } = mountDialog({ mode: "rubric", runA: undefined });
    expect(orderPicker()).toHaveTextContent("Dataset order");
    expect(screen.getByText(DATASET_HELP)).toBeInTheDocument();
    expect(screen.queryByText(/Lowest first:|Regressions first/)).not.toBeInTheDocument();
    fireEvent.mouseDown(orderPicker());
    expect(screen.queryByRole("option", { name: "Regressions first" })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("option", { name: "Dataset order" }));
    const params = await submitToStub(onClose, IDS);
    expect(params).toMatchObject({ runs: B, sort: "order" });
  });

  it("resets the order and helper when changing review type or reopening", async () => {
    const { onClose, rerenderDialog } = mountDialog();
    fireEvent.click(screen.getByRole("button", { name: "Rubric" }));
    expect(orderPicker()).toHaveTextContent("Dataset order");
    expect(screen.getByText(DATASET_HELP)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Pairwise" }));
    expect(orderPicker()).toHaveTextContent("Regressions first");
    rerenderDialog({ open: false, mode: "rubric", runA: undefined });
    rerenderDialog({ open: true, mode: "rubric", runA: undefined });
    expect(orderPicker()).toHaveTextContent("Dataset order");
    expect(screen.getByText(DATASET_HELP)).toBeInTheDocument();
    const params = await submitToStub(onClose, IDS);
    expect(params).toMatchObject({ runs: B, sort: "order" });
  });

  it("keeps selected IDs in the server's regression order at the queue boundary", async () => {
    const { onClose } = mountDialog({ exampleIds: [IDS[1], IDS[2]] });
    const params = await submitToStub(onClose, [IDS[2], IDS[1]]);
    expect(params.sort).toBe("regressed");
  });

  it.each(["Dataset order", "Random"])(
    "resolves %s with sort=order and preserves the requested queue ordering",
    async (choice) => {
      const { onClose } = mountDialog();
      fireEvent.change(screen.getByLabelText("Queue Name"), {
        target: { value: "Fixed shuffle seed" },
      });
      await chooseOrder(choice);
      const expected = choice === "Random"
        ? seededShuffle(IDS, "Fixed shuffle seed")
        : IDS;
      const params = await submitToStub(onClose, expected);
      expect(params.sort).toBe("order");
    },
  );

  it("does not resolve or create a queue for identical pairwise runs", async () => {
    mountDialog({ runB: A });
    expect(screen.getByText("Pick two different runs to compare regressions")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add to queue" }));
    expect(await screen.findByText("Pick two different runs")).toBeInTheDocument();
    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.createQueue).not.toHaveBeenCalled();
  });
});
