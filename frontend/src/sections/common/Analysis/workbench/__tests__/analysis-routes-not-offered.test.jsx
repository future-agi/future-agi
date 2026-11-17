// The Analysis workbench asks nothing of a route this build does not offer:
// run metrics, classification, best of N and compare by key. Each part
// stays where it is, turned off, with the shared line. A build that shows
// every unreleased part still asks none of them: the route decides, not
// the switch. Not a launch build (the prototype, unit tests) keeps all of
// it working.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  renderWithRouter,
  screen,
} from "src/utils/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import {
  useDerivedMetrics,
  useFormulaPreview,
  useSaveDerivedMetrics,
} from "src/sections/common/DerivedMetrics/useDerivedMetrics";
import ClassificationView from "src/sections/common/DerivedMetrics/ClassificationView";
import ClassificationSetupDialog from "src/sections/common/DerivedMetrics/ClassificationSetupDialog";
import BestOfView from "src/sections/common/BestOf/BestOfView";
import BestOfCard from "src/sections/common/BestOf/BestOfCard";
import MatchByKeyDialog from "src/sections/common/CompareRuns/MatchByKeyDialog";
import ExploreViewBar from "../ExploreViewBar";
import ByDatasetTable from "../ByDatasetTable";
import RunMetricPanel from "../RunMetricPanel";

const h = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: h.get, put: h.put, post: h.post },
  };
});
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock(
  "src/sections/experiment-detail/ExperimentBarRightSection/ExperimentBarSummaryRightSection",
  () => ({ SEGMENTED_SX: {} }),
);

const LINE = "Not available in this release";
const RUNS = [
  { run_id: "r1", label: "#1A", name: "Reply v1" },
  { run_id: "r2", label: "#2A", name: "Reply v2" },
];
const RUN_IDS = RUNS.map((r) => r.run_id);

// A launch build whose own switch is off, a launch build as shipped (every
// unreleased part shown), and not a launch build.
const notOffered = () =>
  setLaunchOverrideForTest({ launch: true, showUnreleased: false });
const switchOn = () => setLaunchOverrideForTest({ launch: true });
const prototype = () => setLaunchOverrideForTest(null);

let client;
const withClient = (ui) => (
  <QueryClientProvider client={client}>{ui}</QueryClientProvider>
);
const wrapper = ({ children }) => withClient(children);
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
const nothingSent = () => {
  expect(h.get).not.toHaveBeenCalled();
  expect(h.put).not.toHaveBeenCalled();
  expect(h.post).not.toHaveBeenCalled();
};

beforeEach(() => {
  for (const fn of [h.get, h.put, h.post])
    fn.mockReset().mockImplementation(() => new Promise(() => {}));
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  setLaunchOverrideForTest(null);
  window.history.pushState({}, "", "/");
});

describe("run metrics (derived-metrics) where the route is not offered", () => {
  it("the runs' values read sends nothing", async () => {
    notOffered();
    renderHook(() => useDerivedMetrics("ds-1", RUN_IDS), { wrapper });
    await settle();
    nothingSent();
  });

  it("a draft formula's preview sends nothing", async () => {
    notOffered();
    renderHook(() => useFormulaPreview("ds-1", RUN_IDS, "pass_rate"), {
      wrapper,
    });
    await settle();
    nothingSent();
  });

  it("the save refuses with the line and sends nothing", async () => {
    notOffered();
    h.put.mockResolvedValue({ data: { result: {} } });
    const { result } = renderHook(() => useSaveDerivedMetrics("ds-1"), {
      wrapper,
    });
    await act(async () => {
      await expect(result.current.mutateAsync([])).rejects.toThrow(LINE);
    });
    nothingSent();
  });
});

describe("classification where the route is not offered", () => {
  it("the Classification view's read sends nothing", async () => {
    notOffered();
    render(
      withClient(
        <ClassificationView
          datasetId="ds-1"
          runs={RUNS}
          onOpenRows={vi.fn()}
        />,
      ),
    );
    await settle();
    nothingSent();
  });

  it("the setup's preview sends nothing and Save setup is turned off", async () => {
    notOffered();
    render(
      withClient(
        <ClassificationSetupDialog
          open
          onClose={vi.fn()}
          datasetId="ds-1"
          runIds={RUN_IDS}
          columns={[{ id: "label", name: "label" }]}
          setup={{ truth_column: "label", predicted: "output" }}
        />,
      ),
    );
    await settle();
    const save = screen.getByRole("button", { name: "Save setup" });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    await settle();
    nothingSent();
  });
});

describe("best of N where the route is not offered", () => {
  it("the options read sends nothing and Run stays off", async () => {
    notOffered();
    render(
      withClient(
        <BestOfView datasetId="ds-1" runs={RUNS} onOpenRows={vi.fn()} />,
      ),
    );
    await settle();
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
    nothingSent();
  });

  it("a linked result's read sends nothing", async () => {
    notOffered();
    renderWithRouter(
      withClient(
        <BestOfView datasetId="ds-1" runs={RUNS} onOpenRows={vi.fn()} />,
      ),
      { route: "/dashboard/develop/ds-1?bestof=bo-1" },
    );
    await settle();
    nothingSent();
  });

  it("the result drawer's card sends nothing", async () => {
    notOffered();
    render(
      withClient(<BestOfCard datasetId="ds-1" exampleId="ex-1" runs={RUNS} />),
    );
    await settle();
    nothingSent();
  });
});

describe("compare by key (results/match) where the route is not offered", () => {
  it("the dialog sends nothing and says the line", async () => {
    notOffered();
    const onClose = vi.fn();
    render(
      withClient(
        <MatchByKeyDialog
          open
          onClose={onClose}
          runs={RUNS}
          defaultRunId="r1"
        />,
      ),
    );
    await settle();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Compare rows by key");
    expect(dialog).toHaveTextContent(LINE);
    expect(screen.queryByRole("combobox", { name: "Run" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    nothingSent();
  });
});

describe("a launch build that shows every unreleased part", () => {
  it("still sends none of these requests", async () => {
    switchOn();
    expect(isSurfaceShown("exp.results.summary")).toBe(true);
    renderHook(() => useDerivedMetrics("ds-1", RUN_IDS), { wrapper });
    render(
      withClient(
        <>
          <ClassificationView
            datasetId="ds-1"
            runs={RUNS}
            onOpenRows={vi.fn()}
          />
          <BestOfView datasetId="ds-1" runs={RUNS} onOpenRows={vi.fn()} />
          <BestOfCard datasetId="ds-1" exampleId="ex-1" runs={RUNS} />
          <MatchByKeyDialog
            open
            onClose={vi.fn()}
            runs={RUNS}
            defaultRunId="r1"
          />
        </>,
      ),
    );
    await settle();
    nothingSent();
  });
});

describe("the workbench's parts where their routes are not offered", () => {
  const bar = (onTab = vi.fn(), onMatch = vi.fn()) =>
    render(
      <ExploreViewBar
        tab="pivot"
        onTab={onTab}
        onPrint={vi.fn()}
        onMatch={onMatch}
      />,
    );

  it("keeps Run metrics, Classification and Best of N in the bar, turned off with the line", () => {
    notOffered();
    const onTab = vi.fn();
    bar(onTab);

    for (const label of ["Run metrics", "Classification", "Best of N"]) {
      const tab = screen.getByRole("button", { name: `${label} ${LINE}` });
      expect(tab).toBeVisible();
      expect(tab).toBeDisabled();
      fireEvent.click(tab);
    }
    expect(onTab).not.toHaveBeenCalled();
    for (const label of ["Pivot & charts", "Charts", "Config"])
      expect(screen.getByRole("button", { name: label })).toBeEnabled();
  });

  it("keeps Compare with a run on another dataset… in the menu, turned off with the line", () => {
    notOffered();
    const onMatch = vi.fn();
    bar(vi.fn(), onMatch);

    fireEvent.click(
      screen.getByRole("button", { name: "More Explore actions" }),
    );
    const item = screen.getByRole("menuitem", {
      name: `Compare with a run on another dataset… ${LINE}`,
    });
    expect(item).toBeInTheDocument();
    expect(item).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(item);
    expect(onMatch).not.toHaveBeenCalled();
  });

  it("keeps each By dataset cell's Compare rows by key… button, turned off and described by the line", () => {
    notOffered();
    const onMatchCell = vi.fn();
    const onOpenRow = vi.fn();
    render(
      <ByDatasetTable
        model={{
          columns: [{ key: "c1", label: "Config A" }],
          rows: [
            {
              dataset: { datasetId: "ds-2", name: "Refunds" },
              cells: {
                c1: {
                  run: { run_id: "r2", label: "#2A", name: "Reply v2" },
                  n: 10,
                  value: 0.5,
                  sum: 5,
                  trials: 1,
                },
              },
            },
          ],
        }}
        measureNames={{}}
        sentence="Each dataset on its own headline measure."
        onOpenRow={onOpenRow}
        onPickRun={vi.fn()}
        onMatchCell={onMatchCell}
        focusRunId="r1"
      />,
    );

    const button = screen.getByRole("button", {
      name: "Compare rows by key… Refunds #2A",
    });
    expect(button).toBeVisible();
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(LINE);
    fireEvent.click(button);
    expect(onMatchCell).not.toHaveBeenCalled();
    expect(onOpenRow).not.toHaveBeenCalled();
  });

  it("a linked run metric's panel says the line, not that the metric left the dataset", async () => {
    notOffered();
    render(
      withClient(
        <RunMetricPanel datasetId="ds-1" runs={RUNS} metricKey="rm:m1" />,
      ),
    );
    await settle();
    expect(screen.getByTestId("run-metric-panel")).toHaveTextContent(LINE);
    expect(
      screen.queryByText("This run metric is no longer on the dataset."),
    ).toBeNull();
    nothingSent();
  });
});

// Guards (green before and after): not a launch build keeps every part.
describe("not a launch build", () => {
  it("asks for the runs' values and the classification", async () => {
    prototype();
    renderHook(() => useDerivedMetrics("ds-1", RUN_IDS), { wrapper });
    render(
      withClient(
        <ClassificationView
          datasetId="ds-1"
          runs={RUNS}
          onOpenRows={vi.fn()}
        />,
      ),
    );
    await settle();
    const paths = h.get.mock.calls.map(([path]) => path);
    expect(paths).toContain("/model-hub/datasets/ds-1/derived-metrics/");
    expect(paths).toContain("/model-hub/datasets/ds-1/classification/");
  });

  it("opens the match dialog's read", async () => {
    prototype();
    render(
      withClient(
        <MatchByKeyDialog
          open
          onClose={vi.fn()}
          runs={RUNS}
          defaultRunId="r1"
        />,
      ),
    );
    await settle();
    expect(h.get.mock.calls.map(([path]) => path)).toContain(
      "/model-hub/results/match/",
    );
  });

  it("keeps the three tabs working", () => {
    prototype();
    const onTab = vi.fn();
    render(
      <ExploreViewBar
        tab="pivot"
        onTab={onTab}
        onPrint={vi.fn()}
        onMatch={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Run metrics" }));
    expect(onTab).toHaveBeenCalledWith("run_metrics");
    expect(screen.queryByText(LINE)).toBeNull();
  });
});
