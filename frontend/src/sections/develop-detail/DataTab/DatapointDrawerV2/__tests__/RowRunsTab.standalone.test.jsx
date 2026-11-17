/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  within,
} from "src/utils/test-utils";
import { readResultsState } from "src/sections/experiment-detail/results/useResultsUrlState";
import RowActivityTab from "../RowActivityTab";
import RowRunsTab from "../RowRunsTab";
import RunLinks from "../RunLinks";
import { runResultHref } from "../runsModel";

const hooks = vi.hoisted(() => ({ runs: vi.fn(), activity: vi.fn() }));
vi.mock("src/api/develop/row-view", async (importOriginal) => ({
  ...(await importOriginal()),
  useRowRuns: hooks.runs,
  useRowActivity: hooks.activity,
}));
vi.mock("src/api/develop/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useExperimentDetail: () => ({ data: undefined, isFetching: false }),
}));
vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: () => ({ data: null, isLoading: false }),
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: ({ traceId, open }) =>
    open ? <div data-testid="opened-full-trace">{traceId}</div> : null,
}));
vi.mock("src/sections/common/Collab/RunCommentsSection", () => ({
  default: () => null,
}));
vi.mock("src/api/develop/row-collab", async (importOriginal) => ({
  ...(await importOriginal()),
  useRowComments: () => ({ data: { me: { perms: { view: false } } } }),
  usePostComment: () => ({}),
}));
vi.mock("src/sections/common/RowPanel/ActivityList", () => ({
  default: ({ events, onOpenRun }) => (
    <button onClick={() => onOpenRun(events[0])}>Open activity run</button>
  ),
}));

const DATASET = "11111111-1111-4111-8111-111111111111";
const ROW = "22222222-2222-4222-8222-222222222222";
const RUN = "44444444-4444-4444-8444-444444444444";
const EXAMPLE = "77777777-7777-4777-8777-777777777777";
const EXPERIMENT = "88888888-8888-4888-8888-888888888888";
const TRACE = "0123456789abcdef0123456789abcdef";

const standalone = {
  run_id: RUN,
  example_id: EXAMPLE,
  label: "#1",
  name: null,
  experiment_id: null,
  experiment_name: null,
  status: "completed",
  output: { text: "Paris" },
  error: null,
  trials: 1,
  evals: {},
  latency_ms: 120,
  cost: 0.01,
  ran_at: "2026-01-02T03:04:05Z",
  dataset_version: {
    id: "66666666-6666-4666-8666-666666666666",
    content_hash: "",
  },
  is_dataset_baseline: false,
  row_changed_after: false,
  target_type: "external",
  trace_id: TRACE,
  trials_detail: [{ trial_number: 1, status: "completed", trace_id: TRACE }],
};
const attached = {
  ...standalone,
  experiment_id: EXPERIMENT,
  experiment_name: "Prompt A",
};
const graph = {
  ...attached,
  experiment_name: "Graph A",
  target_type: "graph",
  trials_detail: [
    {
      trial_number: 1,
      status: "completed",
      trace_id: TRACE,
      output: standalone.output,
      error: null,
      scores: {},
    },
  ],
};

const answer = (data) => ({
  data,
  isLoading: false,
  isError: false,
  refetch: vi.fn(),
});
const read = (run) => ({
  summary: {
    runs: 1,
    experiments: run.experiment_id ? 1 : 0,
    primary_metric_name: null,
    primary_scored: 0,
    primary_pass: 0,
    classic_experiments: 0,
    flags: {
      hard_case: false,
      fails_of_runs: null,
      flaky: false,
      expected_changed_after_runs: 0,
    },
  },
  metrics: [],
  runs: [run],
});

let client;
beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  client.clear();
});

const mount = (run = standalone) => {
  hooks.runs.mockReturnValue(answer(read(run)));
  return renderWithRouter(
    <QueryClientProvider client={client}>
      <RowRunsTab datasetId={DATASET} rowId={ROW} />
    </QueryClientProvider>,
    { route: `/dashboard/develop/${DATASET}?row=${ROW}` },
  );
};
const row = () => screen.getByTestId("row-run-#1");
const expectStandaloneResult = (href, exampleId = EXAMPLE) => {
  const opened = new URL(href, "http://app.test");
  expect(opened.pathname).toBe(`/dashboard/develop/${DATASET}/compare/data`);
  expect(readResultsState(opened.searchParams)).toMatchObject({
    runIds: [RUN],
    baseline: RUN,
    row: exampleId,
    run: RUN,
  });
  expect(opened.searchParams.has("history")).toBe(false);
  expect(opened.pathname).not.toContain("/experiment/null/");
  return opened;
};

const attachedHref =
  `/dashboard/develop/experiment/${EXPERIMENT}/data` +
  `?row=${EXAMPLE}&run=${RUN}&history=${RUN}`;

describe("standalone run result links", () => {
  it("opens one standalone run and its example in dataset results", () => {
    expectStandaloneResult(
      runResultHref(standalone, ROW, { datasetId: DATASET }),
    );
  });

  it("selects the trajectory without requiring an experiment", () => {
    const opened = expectStandaloneResult(
      runResultHref(standalone, ROW, {
        datasetId: DATASET,
        trajectory: true,
      }),
    );
    expect(opened.searchParams.get("dtab")).toBe("trajectory");
  });

  it("uses the supplied row when the standalone event has no example", () => {
    const { example_id: _, ...event } = standalone;
    expectStandaloneResult(
      runResultHref(event, ROW, { datasetId: DATASET }),
      ROW,
    );
  });

  it("returns no destination without the standalone dataset or run", () => {
    expect(runResultHref(standalone, ROW)).toBeNull();
    expect(
      runResultHref({ ...standalone, run_id: null }, ROW, {
        datasetId: DATASET,
      }),
    ).toBeNull();
  });

  it("keeps attached result and trajectory URLs byte-identical", () => {
    expect(runResultHref(attached, ROW, { datasetId: DATASET })).toBe(
      attachedHref,
    );
    expect(
      runResultHref(attached, ROW, { datasetId: DATASET, trajectory: true }),
    ).toBe(`${attachedHref}&dtab=trajectory`);
    expect(runResultHref(attached, ROW)).toBe(attachedHref);
  });

  it("opens a standalone result from the row icon", () => {
    mount();
    fireEvent.click(within(row()).getByRole("button", { name: "Open #1" }));
    expectStandaloneResult(window.location.href);
  });

  it("opens a standalone result from the row menu", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "More for #1" }));
    expect(
      screen.queryByRole("menuitem", {
        name: "Open this result in the experiment",
      }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: "Open this result" }));
    expectStandaloneResult(window.location.href);
  });

  it("opens a standalone result from the expanded row", () => {
    mount();
    fireEvent.click(row());
    const detail = row().nextElementSibling;
    fireEvent.click(within(detail).getByRole("button", { name: "Open #1" }));
    expectStandaloneResult(window.location.href);
  });

  it.each(["icon", "menu"])(
    "opens a standalone agent trajectory from the %s",
    (action) => {
      mount({ ...standalone, target_type: "agent" });
      if (action === "icon") {
        fireEvent.click(
          within(row()).getByRole("button", { name: "Trajectory of #1" }),
        );
      } else {
        fireEvent.click(screen.getByRole("button", { name: "More for #1" }));
        fireEvent.click(
          screen.getByRole("menuitem", { name: "Open the trajectory" }),
        );
      }
      const opened = expectStandaloneResult(window.location.href);
      expect(opened.searchParams.get("dtab")).toBe("trajectory");
    },
  );

  it("hides standalone icon actions when dataset context is absent", () => {
    renderWithRouter(
      <RunLinks run={{ ...standalone, target_type: "agent" }} rowId={ROW} />,
    );
    expect(screen.queryByRole("button", { name: "Open #1" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Trajectory of #1" }),
    ).toBeNull();
  });

  it("keeps the supplied full trace independently accessible", () => {
    mount();
    const initialHref = window.location.href;
    fireEvent.click(within(row()).getByRole("button", { name: / menu$/ }));
    const action = screen.getByRole("menuitem", { name: /Open full trace/ });
    expect(action).toBeVisible();
    fireEvent.click(action);
    expect(screen.getByTestId("opened-full-trace")).toHaveTextContent(TRACE);
    expect(window.location.href).toBe(initialHref);
  });

  it.each(["icon", "menu"])("keeps attached routing from the %s", (action) => {
    mount(attached);
    if (action === "icon") {
      fireEvent.click(within(row()).getByRole("button", { name: "Open #1" }));
    } else {
      fireEvent.click(screen.getByRole("button", { name: "More for #1" }));
      fireEvent.click(
        screen.getByRole("menuitem", {
          name: "Open this result in the experiment",
        }),
      );
    }
    const opened = new URL(window.location.href);
    expect(`${opened.pathname}${opened.search}`).toBe(attachedHref);
  });

  it("passes dataset context from an activity run action", () => {
    hooks.activity.mockReturnValue(
      answer([{ ...standalone, type: "run", at: standalone.ran_at }]),
    );
    client.setQueryData(["dataset-detail-average", DATASET], {
      data: { result: { column_config: [] } },
    });
    renderWithRouter(
      <QueryClientProvider client={client}>
        <RowActivityTab datasetId={DATASET} rowId={ROW} />
      </QueryClientProvider>,
      { route: `/dashboard/develop/${DATASET}?row=${ROW}` },
    );
    fireEvent.click(screen.getByRole("button", { name: "Open activity run" }));
    expectStandaloneResult(window.location.href);
  });
});

describe("graph run trajectory links", () => {
  it.each([
    ["icon", 1000],
    ["expanded row", 640],
    ["menu", 1000],
    ["menu", 640],
  ])("opens a graph trajectory from the %s at %i px", (action, width) => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback) {
          this.callback = callback;
        }
        observe() {
          this.callback([{ contentRect: { width } }]);
        }
        unobserve() {}
        disconnect() {}
      },
    );
    mount(graph);
    expect(
      screen.queryAllByRole("columnheader", { name: "Version" }),
    ).toHaveLength(width === 640 ? 0 : 1);

    let link;
    if (action === "menu") {
      fireEvent.click(screen.getByRole("button", { name: "More for #1" }));
      link = screen.getByRole("menuitem", { name: "Open the trajectory" });
    } else if (action === "expanded row") {
      expect(
        within(row()).queryByRole("button", { name: "Trajectory of #1" }),
      ).toBeNull();
      fireEvent.click(row());
      link = within(row().nextElementSibling).getByRole("button", {
        name: "Trajectory of #1",
      });
    } else {
      link = within(row()).getByRole("button", { name: "Trajectory of #1" });
    }
    expect(link).toBeVisible();
    fireEvent.click(link);
    const opened = new URL(window.location.href);
    expect(`${opened.pathname}${opened.search}`).toBe(
      `${attachedHref}&dtab=trajectory`,
    );
  });
});
