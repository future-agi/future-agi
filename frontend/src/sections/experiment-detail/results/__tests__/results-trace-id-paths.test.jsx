import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import axios from "src/utils/axios";
import { CellTraceChip } from "../cells/OutputCell";
import TrajectoryTab from "../drawer/TrajectoryTab";
import EvalStepsList from "../drawer/EvalStepsList";
import ReviewTab from "../drawer/ReviewTab";

const OPENTELEMETRY = "4bf92f3577b34da0a3ce929d0e0e4736";
const DASHED = "4bf92f35-77b3-4da0-a3ce-929d0e0e4736";
const OPENTELEMETRY_PATH = `/tracer/trace/${OPENTELEMETRY}/`;
const DASHED_PATH = `/tracer/trace/${DASHED}/`;
const RUN = {
  run_id: "run-candidate",
  label: "Candidate",
  name: "Answer",
  target_type: "prompt",
  config: {},
};
const BASELINE = {
  run_id: "run-baseline",
  label: "Baseline",
  target_type: "prompt",
};

const seen = vi.hoisted(() => ({
  drawers: [],
  trees: [],
  comments: [],
  commentSteps: [],
}));

// Keep the real endpoint builder and query hook. Only the transport returns
// a fixture, so a wrong id or URL cannot be hidden by a mocked trace hook.
vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    ...actual,
    default: { ...actual.default, get: vi.fn() },
  };
});

// These observers forward unchanged to the real implementations. In
// particular, the drawer itself must normalize the id before its GET.
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", async () => {
  const actual = await vi.importActual(
    "src/components/traceDetail/TraceDetailDrawerV2",
  );
  const Drawer = actual.default;
  return {
    ...actual,
    default: function ObservedTraceDrawer(props) {
      seen.drawers.push({ traceId: props.traceId, open: props.open });
      return <Drawer {...props} />;
    },
  };
});
vi.mock("src/sections/common/Trajectory/useTraceTree", async () => {
  const actual = await vi.importActual(
    "src/sections/common/Trajectory/useTraceTree",
  );
  const useRealTraceTree = actual.default;
  return {
    ...actual,
    default: function useObservedTraceTree(traceId) {
      seen.trees.push(traceId);
      return useRealTraceTree(traceId);
    },
  };
});
vi.mock("src/sections/common/Collab/RunCommentsSection", async () => {
  const actual = await vi.importActual(
    "src/sections/common/Collab/RunCommentsSection",
  );
  const Comments = actual.default;
  return {
    ...actual,
    default: function ObservedRunComments(props) {
      seen.comments.push({
        datasetId: props.datasetId,
        rowId: props.rowId,
        runId: props.runId,
        traceId: props.traceId,
      });
      return <Comments {...props} />;
    },
  };
});

// Unrelated run metadata and review reads stay local. Neither the result
// payload nor any trace-id selector is replaced by these mocks.
vi.mock("src/api/develop/r2", async () => ({
  ...(await vi.importActual("src/api/develop/r2")),
  useRunConfigs: () => ({ data: { results: [] } }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", async () => ({
  ...(await vi.importActual("src/sections/common/Datasets/datasetKind")),
  useDatasetKind: () => ({ kind: "rows", isLoading: false }),
}));
vi.mock("src/sections/common/Trajectory/useRunMarkers", () => ({
  default: () => ({
    byRun: {},
    traceOf: () => null,
    marksOf: () => null,
    isLoading: false,
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "reviewer" }, role: "VIEWER" }),
}));
vi.mock("src/api/annotation-labels/annotation-labels", async () => ({
  ...(await vi.importActual("src/api/annotation-labels/annotation-labels")),
  useAnnotationLabelsList: () => ({
    data: { results: [] },
    isLoading: false,
  }),
}));
vi.mock("src/api/develop/review", async () => ({
  ...(await vi.importActual("src/api/develop/review")),
  useReviewQueueFor: () => ({ queue: null }),
}));
vi.mock("src/api/develop/row-collab", async () => ({
  ...(await vi.importActual("src/api/develop/row-collab")),
  useRowComments: () => ({ data: { threads: [] } }),
}));

// Presentation and mutation controls are not part of choosing or reading
// the trace. Keep the actual chip menu, trajectory opener and eval rows.
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/Runs/RunProvenance", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Review/CorrectedOutputChip", () => ({
  default: () => null,
}));
vi.mock("../drawer/PairwiseCard", () => ({ default: () => null }));
vi.mock("../drawer/RubricCard", () => ({ default: () => null }));
vi.mock("../drawer/CorrectAnswerCard", () => ({ default: () => null }));
vi.mock("src/sections/common/Collab/CommentThread", () => ({
  default: ({ stepOptions }) => {
    seen.commentSteps.push(stepOptions);
    return (
      <div data-testid="comment-step-options">
        {stepOptions.map((option) => (
          <span key={option.key}>{option.label}</span>
        ))}
      </div>
    );
  },
}));
vi.mock("src/components/share-dialog", () => ({
  ShareDialog: () => null,
}));
vi.mock(
  "src/sections/annotations/queues/components/add-to-queue-dialog",
  () => ({
    default: () => null,
  }),
);
vi.mock("src/components/traceDetail/AddTagsPopover", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/TraceDetailPanel", () => ({
  CaptureCoverageNotice: () => null,
}));
vi.mock("src/components/traceDetail/TraceLeftPanel", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/TraceTreeV2", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/SpanTreeTimeline", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/SpanDetailPane", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Trajectory/DiffGraph", () => ({
  default: () => null,
  SingleGraph: () => null,
}));
vi.mock("src/sections/common/Trajectory/NodeSpansSplit", () => ({
  default: () => null,
}));

const resultFor = (traceId) => ({
  status: "completed",
  output: "The answer",
  trials: 2,
  trace_ids: [null, traceId],
  trace_summary: [null, { status: "ok", steps: 1, llm_calls: 1 }],
});
const rowFor = (result) => ({
  example_id: "row-answer",
  row_order: 1,
  results: { [RUN.run_id]: result },
});

const traceDetail = (evalTraceId) => ({
  trace: { id: DASHED, name: "Answer trace", tags: [] },
  summary: {},
  observation_spans: [
    {
      observation_span: {
        id: "root-span",
        name: "answer",
        observation_type: "chain",
        status: "OK",
      },
      children: [
        {
          observation_span: {
            id: "reply-span",
            parent_span_id: "root-span",
            name: "respond",
            observation_type: "llm",
            status: "OK",
          },
          eval_scores: [
            {
              eval_name: "Correctness",
              score: 0,
              result: false,
              run_id: RUN.run_id,
              explanation: "The response did not match the expected answer.",
              eval_trace_id: evalTraceId,
            },
          ],
          children: [],
        },
      ],
    },
  ],
});

const clients = [];
const mount = (ui, route = "/") => {
  window.history.replaceState({}, "", route);
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
    },
  });
  clients.push(client);
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
};
const traceGets = () =>
  axios.get.mock.calls.filter(([url]) => url.startsWith("/tracer/trace/"));
const expectTraceGets = async (paths) => {
  await waitFor(() => {
    expect(clients.at(-1).isFetching()).toBe(0);
    expect(traceGets()).toEqual(
      paths.map((path) => [path, { params: undefined }]),
    );
  });
};
const expectTreeId = (traceId) => {
  const ids = seen.trees.filter(Boolean);
  expect(ids).toContain(traceId);
  expect([...new Set(ids)]).toEqual([traceId]);
};

beforeEach(() => {
  vi.clearAllMocks();
  Object.values(seen).forEach((calls) => calls.splice(0));
  vi.stubGlobal("localStorage", {
    getItem: vi.fn().mockReturnValue(null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe.each([
  {
    form: "an older run's OpenTelemetry id",
    traceId: OPENTELEMETRY,
    readPath: OPENTELEMETRY_PATH,
  },
  {
    form: "the API's stored dashed id",
    traceId: DASHED,
    readPath: DASHED_PATH,
  },
])("experiment result trace paths with $form", ({ traceId, readPath }) => {
  beforeEach(() => {
    const detail = traceDetail(traceId);
    axios.get.mockImplementation(async (url) => ({
      data: {
        result: url === OPENTELEMETRY_PATH || url === DASHED_PATH ? detail : [],
      },
    }));
  });

  // With a dashed payload the inline read and drawer share a fresh query.
  // A compact payload uses its own inline query and a dashed drawer query.
  const pathsAfterDrawer =
    traceId === DASHED ? [DASHED_PATH] : [readPath, DASHED_PATH];

  it("opens the output cell's selected trial through the real trace drawer", async () => {
    const result = resultFor(traceId);
    mount(
      <CellTraceChip
        row={rowFor(result)}
        runId={RUN.run_id}
        result={result}
        trial={2}
        ctx={{ runsById: { [RUN.run_id]: RUN } }}
      />,
    );
    expect(traceGets()).toEqual([]);
    expect(seen.drawers).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: /menu$/i }));
    const openTrace = await screen.findByRole("menuitem", {
      name: /Open full trace/,
    });
    expect(openTrace.getAttribute("aria-disabled")).not.toBe("true");
    fireEvent.click(openTrace);

    expect(seen.drawers.at(-1)).toEqual({ traceId, open: true });
    await expectTraceGets([DASHED_PATH]);
  });

  it("reads the trajectory's selected trial and opens it in Observe", async () => {
    // Trial detail wins over an absent trace_ids entry; traceIdFor remains real.
    const result = {
      ...resultFor(traceId),
      trace_ids: [null, null],
      trials_detail: [{ trial: 2, trace_id: traceId }],
    };
    mount(
      <TrajectoryTab
        datasetId="dataset-answers"
        row={rowFor(result)}
        runs={[RUN]}
        focusedRunId={RUN.run_id}
        trial={2}
      />,
      "/?trial=2&tview=steps&vs=none",
    );

    await screen.findByTestId("eval-step-line");
    expectTreeId(traceId);
    expect(seen.drawers).toEqual([]);
    await expectTraceGets([readPath]);

    // The tooltip's title is the button's accessible name.
    fireEvent.click(
      screen.getByRole("button", { name: "Open this trace in Observe" }),
    );
    expect(seen.drawers.at(-1)).toEqual({ traceId, open: true });
    await expectTraceGets(pathsAfterDrawer);
  });

  it.each([1, 2])("keeps evidence tied to try %i", async (trial) => {
    const result = {
      ...resultFor(traceId),
      trace_ids: [traceId, null],
      trials_detail: [
        { trial_number: 1, status: "completed", trace_id: traceId },
        { trial_number: 2, status: "not_reached", trace_id: null },
      ],
    };
    mount(
      <TrajectoryTab
        datasetId="dataset-answers"
        row={rowFor(result)}
        runs={[RUN]}
        focusedRunId={RUN.run_id}
        trial={trial}
      />,
      `/?trial=${trial}&tview=steps&vs=none`,
    );
    expect(screen.getByText(`Try ${trial} of 2`).textContent).toBe(
      `Try ${trial} of 2`,
    );

    if (trial === 2) {
      expect(screen.getByText("No trace for this try").textContent).toBe(
        "No trace for this try",
      );
      expect(
        screen.queryByRole("button", {
          name: "Open this trace in Observe",
        }),
      ).toBeNull();
      await expectTraceGets([]);
      expect(seen.trees.filter(Boolean)).toEqual([]);
      expect(seen.drawers).toEqual([]);
    } else {
      await screen.findByTestId("eval-step-line");
      expectTreeId(traceId);
      await expectTraceGets([readPath]);
      fireEvent.click(
        screen.getByRole("button", { name: "Open this trace in Observe" }),
      );
      expect(seen.drawers.at(-1)).toEqual({ traceId, open: true });
      await expectTraceGets(pathsAfterDrawer);
    }
  });

  it("reads eval steps without changing the id and opens eval details in the drawer", async () => {
    mount(
      <EvalStepsList
        traceId={traceId}
        runId={RUN.run_id}
        runLabel={RUN.label}
      />,
    );

    const step = await screen.findByTestId("eval-step-line");
    expect(step.textContent).toContain("Correctness");
    expectTreeId(traceId);
    expect(seen.drawers).toEqual([]);
    await expectTraceGets([readPath]);

    fireEvent.click(step);
    fireEvent.click(
      await screen.findByRole("button", { name: "Open eval details" }),
    );
    expect(seen.drawers.at(-1)).toEqual({ traceId, open: true });
    await expectTraceGets(pathsAfterDrawer);
  });

  it("reads the reviewed run's first trace for comment step anchors without a drawer", async () => {
    const row = rowFor({
      ...resultFor(traceId),
      trace_ids: [traceId, null],
    });
    row.results[BASELINE.run_id] = { status: "completed", trace_ids: [] };
    mount(
      <ReviewTab
        datasetId="dataset-answers"
        row={row}
        runs={[BASELINE, RUN]}
        baselineId={BASELINE.run_id}
        focusedRunId={RUN.run_id}
        blind={false}
      />,
    );

    await screen.findByText("1 respond");
    expect(seen.comments.at(-1)).toEqual({
      datasetId: "dataset-answers",
      rowId: row.example_id,
      runId: RUN.run_id,
      traceId,
    });
    expect(seen.commentSteps.at(-1)).toEqual([
      {
        key: "step:reply-span",
        label: "1 respond",
        anchor: {
          kind: "step",
          trace_id: traceId,
          span_id: "reply-span",
          step_label: "1 respond",
        },
      },
    ]);
    expect(seen.drawers).toEqual([]);
    await expectTraceGets([readPath]);
  });
});
