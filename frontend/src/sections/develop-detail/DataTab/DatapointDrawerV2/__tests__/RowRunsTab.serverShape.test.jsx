// The Row view's Runs tab on the shape the server sends for a row's runs
// across experiments (GET datasets/{id}/rows/{row_id}/runs/): it lists each
// run with its experiment, output and verdict, says plainly when the row
// has no recorded run, and says how many of the dataset's experiments have
// no recorded runs and so are not listed.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  within,
} from "src/utils/test-utils";
import { setCollabOfferForTest } from "src/sections/common/Collab/collabOffer";
import { useExperimentRunHistory } from "src/sections/experiment-detail/ExperimentRunHistory";
import RowRunsTab from "../RowRunsTab";
import { runResultHref } from "../runsModel";

const hooks = vi.hoisted(() => ({
  runs: vi.fn(),
  catalogs: {},
  catalogFailed: {},
  catalogFetching: {},
  catalogReads: [],
  catalogRefetches: [],
  datasetCatalog: undefined,
  datasetFailed: false,
  datasetReads: [],
  datasetRefetches: [],
}));
vi.mock("src/api/develop/row-view", async (importOriginal) => ({
  ...(await importOriginal()),
  useRowRuns: hooks.runs,
}));
// An experiment's run catalog: which comparison group each run is in. With
// no catalog given the read has not answered yet; `catalogFailed` fails it;
// `catalogFetching` reads it again while its last answer stands.
vi.mock("src/api/develop/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useExperimentDetail: (experimentId, options = {}) => {
    const on = Boolean(experimentId) && options.enabled !== false;
    if (on) hooks.catalogReads.push(experimentId);
    const failed = on && Boolean(hooks.catalogFailed[experimentId]);
    const data = on && !failed ? hooks.catalogs[experimentId] : undefined;
    return {
      data,
      isError: failed,
      isFetching:
        on &&
        !failed &&
        (data === undefined || Boolean(hooks.catalogFetching[experimentId])),
      refetch: () => {
        hooks.catalogRefetches.push(experimentId);
        return Promise.resolve();
      },
    };
  },
  // The dataset's run catalog (runs of every experiment, or of none), read
  // the same way: no `datasetCatalog` means it has not answered yet.
  useDatasetRuns: (datasetId, options = {}) => {
    const on = Boolean(datasetId) && options.enabled !== false;
    if (on) hooks.datasetReads.push(datasetId);
    const failed = on && hooks.datasetFailed;
    const data = on && !failed ? hooks.datasetCatalog : undefined;
    return {
      data,
      isError: failed,
      isFetching: on && !failed && data === undefined,
      refetch: () => {
        hooks.datasetRefetches.push(datasetId);
        return Promise.resolve();
      },
    };
  },
}));

const DATASET = "11111111-1111-4111-8111-111111111111";
const ROW = "22222222-2222-4222-8222-222222222222";
const METRIC = "33333333-3333-4333-8333-333333333333";
const RUN_A = "44444444-4444-4444-8444-444444444444";
const RUN_B = "55555555-5555-4555-8555-555555555555";
const EXAMPLE = "77777777-7777-4777-8777-777777777777";

// One run as the server's serializer sends it.
const run = (runId, label, experiment, value, output) => ({
  run_id: runId,
  example_id: EXAMPLE,
  label,
  name: null,
  experiment_id: `${runId.slice(0, 8)}-0000-4000-8000-000000000000`,
  experiment_name: experiment,
  status: "completed",
  output: { text: output },
  error: null,
  trials: 1,
  evals: {
    [METRIC]: { value, n: 1, status: "assessed", std: 0, flaky: false },
  },
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
  trace_id: null,
  trials_detail: [{ trial_number: 1, status: "completed", trace_id: null }],
});

const LISTED = {
  summary: {
    runs: 2,
    experiments: 2,
    primary_metric_name: "Correct",
    primary_scored: 2,
    primary_pass: 1,
    classic_experiments: 0,
    flags: {
      hard_case: false,
      fails_of_runs: null,
      flaky: false,
      expected_changed_after_runs: 0,
    },
  },
  metrics: [
    {
      key: METRIC,
      name: "Correct",
      output_type: "pass_fail",
      higher_is_better: true,
      pass_threshold: null,
    },
  ],
  runs: [
    run(RUN_B, "#2", "Prompt B", 0, "Lyon"),
    run(RUN_A, "#1", "Prompt A", 1, "Paris"),
  ],
};

const EMPTY_READ = {
  summary: {
    ...LISTED.summary,
    runs: 0,
    experiments: 0,
    primary_metric_name: null,
    primary_scored: 0,
    primary_pass: 0,
  },
  metrics: [],
  runs: [],
};

const answer = (data) => ({
  data,
  isLoading: false,
  isError: false,
  error: null,
  refetch: vi.fn(),
});

let client;
beforeEach(() => {
  vi.clearAllMocks();
  hooks.catalogs = {};
  hooks.catalogFailed = {};
  hooks.catalogFetching = {};
  hooks.catalogReads = [];
  hooks.catalogRefetches = [];
  hooks.datasetCatalog = undefined;
  hooks.datasetFailed = false;
  hooks.datasetReads = [];
  hooks.datasetRefetches = [];
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => {
  cleanup();
  client.clear();
});

const mount = () =>
  renderWithRouter(
    <QueryClientProvider client={client}>
      <RowRunsTab datasetId={DATASET} rowId={ROW} />
    </QueryClientProvider>,
    { route: `/dashboard/develop/${DATASET}?row=${ROW}` },
  );

describe("the Runs tab on the server's shape", () => {
  it("says where the row ran and how often it passes", () => {
    hooks.runs.mockReturnValue(answer(LISTED));
    mount();
    expect(screen.getByTestId("row-runs-summary")).toHaveTextContent(
      "Ran in 2 runs · 2 experiments · Passes Correct in 1/2 · 1 failing",
    );
  });

  it("lists each run with its experiment and its output", () => {
    hooks.runs.mockReturnValue(answer(LISTED));
    mount();
    const a = document.querySelector(`[data-run-row="${RUN_A}"]`);
    const b = document.querySelector(`[data-run-row="${RUN_B}"]`);
    expect(a).not.toBeNull();
    expect(b).not.toBeNull();
    // The output as stored; a JSON value is shown as its text.
    expect(a).toHaveTextContent("Paris");
    expect(b).toHaveTextContent("Lyon");
    expect(a).toHaveTextContent("Prompt A");
    expect(b).toHaveTextContent("Prompt B");
  });

  it("shows no bare version letter for a version with no number", () => {
    // The server's dataset versions carry no number.
    hooks.runs.mockReturnValue(answer(LISTED));
    mount();
    const a = document.querySelector(`[data-run-row="${RUN_A}"]`);
    const cells = within(a)
      .getAllByRole("cell")
      .map((c) => c.textContent);
    expect(cells).not.toContain("v");
    expect(a.textContent).not.toMatch(/\bv(?![\w-])/);
  });

  it("reads the row's runs for this dataset and row", () => {
    hooks.runs.mockReturnValue(answer(LISTED));
    mount();
    expect(hooks.runs).toHaveBeenCalledWith(DATASET, ROW, {
      scope: "all",
      sort: "newest",
    });
  });

  it("says the row has no recorded run when the server lists none", () => {
    // Green at the base: with no unrecorded experiment, today's words stay.
    hooks.runs.mockReturnValue(answer(EMPTY_READ));
    mount();
    expect(
      screen.getByText("This row hasn't been run in any experiment yet"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Run an experiment on the dataset to see how each agent answers this row.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("row-runs-summary")).toBeNull();
    expect(screen.queryByText(/no recorded runs/)).toBeNull();
  });
});

const withUnrecorded = (read, n) => ({
  ...read,
  summary: { ...read.summary, classic_experiments: n },
});

describe("experiments on the dataset with no recorded runs", () => {
  it("are counted under the summary", () => {
    hooks.runs.mockReturnValue(answer(withUnrecorded(LISTED, 3)));
    mount();
    expect(screen.getByTestId("row-runs-unrecorded")).toHaveTextContent(
      /^3 experiments on this dataset have no recorded runs, so their results aren't listed here\.$/,
    );
  });

  it("are counted under the summary, one", () => {
    hooks.runs.mockReturnValue(answer(withUnrecorded(LISTED, 1)));
    mount();
    expect(screen.getByTestId("row-runs-unrecorded")).toHaveTextContent(
      /^1 experiment on this dataset has no recorded runs, so its results aren't listed here\.$/,
    );
  });

  it("are not mentioned when there are none", () => {
    // Green at the base.
    hooks.runs.mockReturnValue(answer(LISTED));
    mount();
    expect(screen.queryByTestId("row-runs-unrecorded")).toBeNull();
    expect(screen.queryByText(/no recorded runs/)).toBeNull();
  });

  it("change the empty state so it never says the row did not run", () => {
    hooks.runs.mockReturnValue(answer(withUnrecorded(EMPTY_READ, 3)));
    mount();
    expect(
      screen.getByText("No recorded run includes this row."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "3 experiments on this dataset have no recorded runs and aren't listed here.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("This row hasn't been run in any experiment yet"),
    ).toBeNull();
  });

  it("change the empty state, one", () => {
    hooks.runs.mockReturnValue(answer(withUnrecorded(EMPTY_READ, 1)));
    mount();
    expect(
      screen.getByText("No recorded run includes this row."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "1 experiment on this dataset has no recorded runs and isn't listed here.",
      ),
    ).toBeInTheDocument();
  });
});

describe("opening a run's result on the experiment's Data page", () => {
  const listed = run(RUN_A, "#1", "Prompt A", 1, "Paris");
  const params = (href) => new URL(href, "http://app.test").searchParams;

  it("opens the row by its example id in that run", () => {
    const href = runResultHref(listed, ROW);
    expect(new URL(href, "http://app.test").pathname).toBe(
      `/dashboard/develop/experiment/${listed.experiment_id}/data`,
    );
    expect(params(href).get("row")).toBe(EXAMPLE);
    expect(params(href).get("run")).toBe(RUN_A);
  });

  it("opens the trajectory by the example id too", () => {
    const href = runResultHref(listed, ROW, { trajectory: true });
    expect(params(href).get("row")).toBe(EXAMPLE);
    expect(params(href).get("dtab")).toBe("trajectory");
  });

  it("keeps the row id for a run that names no example", () => {
    // Green at the base: the Activity tab's events send none.
    const { example_id: _, ...activity } = listed;
    expect(params(runResultHref(activity, ROW)).get("row")).toBe(ROW);
  });

  it("names the run's Run history group", () => {
    expect(params(runResultHref(listed, ROW)).get("history")).toBe(RUN_A);
    expect(
      params(runResultHref(listed, ROW, { trajectory: true })).get("history"),
    ).toBe(RUN_A);
  });

  it("opens with the run in view when it is outside the newest group", () => {
    // The experiment's runs, newest first: a newer run on another dataset
    // version is the newest group, and the run the row ran in is older.
    const catalog = [
      { run_id: RUN_B, label: "#2", comparison_group: "newer-version" },
      { run_id: RUN_A, label: "#1", comparison_group: "older-version" },
    ];
    const href = runResultHref(listed, ROW);
    /* eslint-disable react/prop-types */
    const wrapper = ({ children }) => (
      <MemoryRouter initialEntries={[href]}>{children}</MemoryRouter>
    );
    /* eslint-enable react/prop-types */
    const { result } = renderHook(() => useExperimentRunHistory(catalog), {
      wrapper,
    });
    expect(result.current.groups).toHaveLength(2);
    expect(result.current.runs.map((r) => r.run_id)).toEqual([RUN_A]);
  });

  it("opens the trajectory from the trace chip at the row's example", () => {
    hooks.runs.mockReturnValue(answer(LISTED));
    mount();
    const row = document.querySelector(`[data-run-row="${RUN_A}"]`);
    fireEvent.click(within(row).getByRole("button", { name: "No trace" }));
    const opened = new URL(window.location.href);
    expect(opened.pathname).toBe(
      `/dashboard/develop/experiment/${listed.experiment_id}/data`,
    );
    expect(opened.searchParams.get("row")).toBe(EXAMPLE);
    expect(opened.searchParams.get("run")).toBe(RUN_A);
    expect(opened.searchParams.get("history")).toBe(RUN_A);
    expect(opened.searchParams.get("dtab")).toBe("trajectory");
  });
});

describe("side by side from the Runs tab", () => {
  const EXPERIMENT = LISTED.runs[0].experiment_id;
  const ON_RESULTS = `/dashboard/develop/experiment/${EXPERIMENT}/data`;
  const UNAVAILABLE =
    "Side-by-side compare needs a server read this release doesn't have yet.";
  const UP_TO_FOUR =
    "Side by side shows up to 4 runs. Untick one to pick another.";
  const CHECKING = "Checking whether these runs can be compared…";
  const CHECK_FAILED = "Couldn't check whether these runs can be compared.";
  const openMenu = () => fireEvent.click(screen.getByTestId("row-run-menu-#1"));
  const catalogOf = (groups) => ({
    runs: Object.entries(groups).map(([runId, group]) => ({
      run_id: runId,
      comparison_group: group,
    })),
  });
  // The experiment's run catalog answers with every run of `data` in one
  // comparison group.
  const inOneGroup = (data) => {
    hooks.catalogs[EXPERIMENT] = catalogOf(
      Object.fromEntries(data.runs.map((r) => [r.run_id, "full"])),
    );
    return data;
  };
  const RUN_C = "88888888-8888-4888-8888-888888888888";
  const RUN_D = "99999999-9999-4999-8999-999999999999";
  const RUN_E = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const sameExperiment = (runs) => ({
    ...LISTED,
    summary: { ...LISTED.summary, runs: runs.length, experiments: 1 },
    runs: runs.map((r) => ({ ...r, experiment_id: EXPERIMENT })),
  });
  const FIVE = sameExperiment([
    run(RUN_E, "#5", "Prompt E", 1, "Tours"),
    run(RUN_D, "#4", "Prompt D", 1, "Nice"),
    run(RUN_C, "#3", "Prompt C", 0, "Lille"),
    ...LISTED.runs,
  ]);
  const checkbox = (label) =>
    screen.getByRole("checkbox", {
      name: `Select ${label} for side by side`,
    });
  // Runs that are not all in one experiment open in the dataset's Compare
  // Data, checked against the dataset's run catalog.
  const ON_COMPARE = `/dashboard/develop/${DATASET}/compare/data`;
  const datasetCatalogOf = (groups) => ({ results: catalogOf(groups).runs });
  beforeEach(() => setCollabOfferForTest({ all: false, madeReal: [] }));
  afterEach(() => setCollabOfferForTest(null));

  it("opens one experiment's Results with the chosen runs and first baseline", () => {
    hooks.runs.mockReturnValue(answer(inOneGroup(FIVE)));
    mount();
    fireEvent.click(checkbox("#1"));
    fireEvent.click(checkbox("#2"));
    const compare = screen.getByRole("button", {
      name: "Compare side by side (2)",
    });
    expect(compare).toBeEnabled();
    fireEvent.click(compare);
    const opened = new URL(window.location.href);
    expect(opened.pathname).toBe(ON_RESULTS);
    expect(opened.searchParams.get("runs")).toBe(`${RUN_A},${RUN_B}`);
    expect(opened.searchParams.get("baseline")).toBe(RUN_A);
    expect(screen.queryByText(UNAVAILABLE)).toBeNull();
  });

  it("opens the default comparison in the experiment too", () => {
    hooks.runs.mockReturnValue(answer(inOneGroup(sameExperiment(LISTED.runs))));
    mount();
    fireEvent.click(
      screen.getByRole("button", { name: /Compare side by side/ }),
    );
    const opened = new URL(window.location.href);
    expect(opened.pathname).toBe(ON_RESULTS);
    expect(opened.searchParams.get("runs")).toBe(`${RUN_B},${RUN_A}`);
    expect(opened.searchParams.get("baseline")).toBe(RUN_B);
  });

  it("keeps runs of two experiments off while the dataset's runs are read", () => {
    hooks.runs.mockReturnValue(answer(LISTED));
    mount();
    expect(checkbox("#1")).toBeEnabled();
    expect(checkbox("#2")).toBeEnabled();
    fireEvent.click(checkbox("#1"));
    fireEvent.click(checkbox("#2"));
    const compare = screen.getByRole("button", {
      name: "Compare side by side (2)",
    });
    expect(compare).toBeVisible();
    expect(compare).toBeDisabled();
    expect(screen.getAllByText(CHECKING)).toHaveLength(1);
    expect(compare).toHaveAccessibleDescription(CHECKING);
    expect(screen.queryByText(UNAVAILABLE)).toBeNull();
    fireEvent.click(compare);
    expect(window.location.pathname).toBe(`/dashboard/develop/${DATASET}`);
    expect(hooks.datasetReads).toContain(DATASET);
  });

  it.each([null, undefined, ""])(
    "opens a run with no experiment (%s) beside another in the dataset's Compare Data",
    (experimentId) => {
      const data = sameExperiment(LISTED.runs);
      data.runs[1].experiment_id = experimentId;
      hooks.runs.mockReturnValue(answer(data));
      hooks.datasetCatalog = datasetCatalogOf({
        [RUN_A]: "full",
        [RUN_B]: "full",
      });
      mount();
      expect(screen.getAllByRole("checkbox")).toHaveLength(2);
      const compare = screen.getByRole("button", {
        name: /Compare side by side/,
      });
      expect(compare).toBeEnabled();
      expect(screen.queryByText(UNAVAILABLE)).toBeNull();
      fireEvent.click(compare);
      const opened = new URL(window.location.href);
      expect(opened.pathname).toBe(ON_COMPARE);
      expect(opened.searchParams.get("runs")).toBe(`${RUN_B},${RUN_A}`);
      expect(opened.searchParams.get("baseline")).toBe(RUN_B);
      expect(opened.searchParams.get("row")).toBe(EXAMPLE);
      expect(hooks.catalogReads).toEqual([]);
    },
  );

  it("enables a same-experiment pair within a mixed list", () => {
    hooks.runs.mockReturnValue(
      answer(
        inOneGroup({
          ...FIVE,
          runs: [
            { ...FIVE.runs[0], experiment_id: "other-experiment" },
            ...FIVE.runs.slice(1),
          ],
        }),
      ),
    );
    mount();
    expect(
      screen.getByRole("button", { name: /Compare side by side/ }),
    ).toBeDisabled();
    fireEvent.click(checkbox("#1"));
    fireEvent.click(checkbox("#2"));
    expect(
      screen.getByRole("button", { name: /Compare side by side/ }),
    ).toBeEnabled();
    expect(screen.queryByText(UNAVAILABLE)).toBeNull();
  });

  it("keeps a single run selectable without opening a comparison", () => {
    hooks.runs.mockReturnValue(answer(sameExperiment([LISTED.runs[0]])));
    mount();
    expect(checkbox("#2")).toBeEnabled();
    expect(
      screen.getByRole("button", { name: /Compare side by side/ }),
    ).toBeDisabled();
    expect(screen.queryByText(UNAVAILABLE)).toBeNull();
    expect(screen.queryByText(CHECKING)).toBeNull();
  });

  it("the run menu opens the same experiment's Results", () => {
    hooks.runs.mockReturnValue(answer(inOneGroup(sameExperiment(LISTED.runs))));
    mount();
    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /^Side by side/ }));
    const opened = new URL(window.location.href);
    expect(opened.pathname).toBe(ON_RESULTS);
    expect(opened.searchParams.get("runs")).toBe(`${RUN_A},${RUN_B}`);
    expect(opened.searchParams.get("baseline")).toBe(RUN_A);
  });

  it("disables the unavailable menu comparison without removing other actions", () => {
    hooks.runs.mockReturnValue(answer(LISTED));
    mount();
    openMenu();
    const compare = screen.getByRole("menuitem", { name: /^Side by side/ });
    expect(compare).toHaveAttribute("aria-disabled", "true");
    expect(
      screen.getByRole("menuitem", {
        name: "Open this result in the experiment",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: "Copy run id" }),
    ).toBeInTheDocument();
    fireEvent.click(compare);
    expect(window.location.pathname).toBe(`/dashboard/develop/${DATASET}`);
  });

  it("says why a run menu's comparison across experiments is off while the ticked runs open", () => {
    const [b, a] = inOneGroup(sameExperiment(LISTED.runs)).runs;
    hooks.runs.mockReturnValue(
      answer({
        ...LISTED,
        summary: { ...LISTED.summary, runs: 3, experiments: 2 },
        runs: [
          {
            ...run(RUN_C, "#3", "Prompt C", 0, "Lille"),
            experiment_id: "other-experiment",
          },
          b,
          { ...a, is_dataset_baseline: true },
        ],
      }),
    );
    hooks.datasetCatalog = datasetCatalogOf({
      [RUN_A]: "full",
      [RUN_B]: "full",
      [RUN_C]: "other-set-up",
    });
    const differ =
      "Runs #1 and #3 can't be compared side by side because their set-ups differ.";
    mount();
    fireEvent.click(checkbox("#1"));
    fireEvent.click(checkbox("#2"));
    expect(
      screen.getByRole("button", { name: "Compare side by side (2)" }),
    ).toBeEnabled();
    expect(screen.queryByText(UNAVAILABLE)).toBeNull();
    fireEvent.click(screen.getByTestId("row-run-menu-#3"));
    const compare = screen.getByRole("menuitem", {
      name: "Side by side with baseline",
    });
    expect(compare).toHaveAttribute("aria-disabled", "true");
    expect(compare).toHaveAccessibleDescription(differ);
    expect(within(compare).getByText(differ)).toBeVisible();
    expect(screen.getAllByText(differ)).toHaveLength(1);
    expect(
      screen.getByRole("menuitem", { name: "Copy run id" }),
    ).toBeInTheDocument();
    fireEvent.click(compare);
    expect(window.location.pathname).toBe(`/dashboard/develop/${DATASET}`);
  });

  describe("runs of other experiments, or of none", () => {
    const SET_UPS_DIFFER =
      "Runs #1 and #2 can't be compared side by side because their set-ups differ.";
    const standalone = (data) => ({
      ...data,
      summary: { ...data.summary, experiments: 0 },
      runs: data.runs.map((r) => ({ ...r, experiment_id: null })),
    });
    const tickBoth = () => {
      fireEvent.click(checkbox("#1"));
      fireEvent.click(checkbox("#2"));
      return screen.getByRole("button", { name: "Compare side by side (2)" });
    };

    it("open in the dataset's Compare Data when the dataset's runs put them in one group", () => {
      hooks.runs.mockReturnValue(answer(LISTED));
      hooks.datasetCatalog = datasetCatalogOf({
        [RUN_A]: "full",
        [RUN_B]: "full",
      });
      mount();
      const compare = tickBoth();
      expect(compare).toBeEnabled();
      expect(screen.queryByText(CHECKING)).toBeNull();
      fireEvent.click(compare);
      const opened = new URL(window.location.href);
      expect(opened.pathname).toBe(ON_COMPARE);
      expect(opened.searchParams.get("runs")).toBe(`${RUN_A},${RUN_B}`);
      expect(opened.searchParams.get("baseline")).toBe(RUN_A);
      expect(opened.searchParams.get("row")).toBe(EXAMPLE);
      expect(opened.searchParams.has("history")).toBe(false);
      expect(hooks.catalogReads).toEqual([]);
    });

    it("open from a run's menu in the dataset's Compare Data", () => {
      hooks.runs.mockReturnValue(answer(LISTED));
      hooks.datasetCatalog = datasetCatalogOf({
        [RUN_A]: "full",
        [RUN_B]: "full",
      });
      mount();
      openMenu();
      fireEvent.click(screen.getByRole("menuitem", { name: /^Side by side/ }));
      const opened = new URL(window.location.href);
      expect(opened.pathname).toBe(ON_COMPARE);
      expect(opened.searchParams.get("runs")).toBe(`${RUN_A},${RUN_B}`);
      expect(opened.searchParams.get("baseline")).toBe(RUN_A);
    });

    it("open when neither run is in an experiment", () => {
      hooks.runs.mockReturnValue(answer(standalone(LISTED)));
      hooks.datasetCatalog = datasetCatalogOf({
        [RUN_A]: "full",
        [RUN_B]: "full",
      });
      mount();
      fireEvent.click(tickBoth());
      const opened = new URL(window.location.href);
      expect(opened.pathname).toBe(ON_COMPARE);
      expect(opened.searchParams.get("runs")).toBe(`${RUN_A},${RUN_B}`);
      expect(opened.pathname).not.toContain("/experiment/");
    });

    it.each([
      ["of two experiments", LISTED],
      ["of no experiment", standalone(LISTED)],
    ])("stay closed and say why when their set-ups differ (%s)", (_, data) => {
      hooks.runs.mockReturnValue(answer(data));
      hooks.datasetCatalog = datasetCatalogOf({
        [RUN_A]: "full",
        [RUN_B]: "subset-retry",
      });
      mount();
      const compare = tickBoth();
      expect(compare).toBeDisabled();
      expect(compare).toHaveAccessibleDescription(SET_UPS_DIFFER);
      expect(screen.queryByText(CHECKING)).toBeNull();
      fireEvent.click(compare);
      expect(window.location.pathname).toBe(`/dashboard/develop/${DATASET}`);
    });

    it("stay off when the dataset's runs can't be read, and try again", () => {
      hooks.runs.mockReturnValue(answer(LISTED));
      hooks.datasetFailed = true;
      mount();
      const compare = tickBoth();
      expect(compare).toBeDisabled();
      expect(compare).toHaveAccessibleDescription(CHECK_FAILED);
      expect(hooks.datasetRefetches).toEqual([]);
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(hooks.datasetRefetches).toEqual([DATASET]);
      expect(hooks.catalogRefetches).toEqual([]);
      expect(window.location.pathname).toBe(`/dashboard/develop/${DATASET}`);
    });

    it("stay off when the dataset's runs do not list one of them", () => {
      hooks.runs.mockReturnValue(answer(LISTED));
      hooks.datasetCatalog = datasetCatalogOf({ [RUN_A]: "full" });
      mount();
      const compare = tickBoth();
      expect(compare).toBeDisabled();
      expect(compare).toHaveAccessibleDescription(CHECK_FAILED);
    });

    it("stay off when Show hides a ticked run, so none is left out", () => {
      const all = {
        ...LISTED,
        summary: { ...LISTED.summary, runs: 3, experiments: 3 },
        runs: [
          run(RUN_C, "#3", "Prompt C", 1, "Lille"),
          ...LISTED.runs.slice(0, 1),
          { ...LISTED.runs[1], is_dataset_baseline: true },
        ],
      };
      const baselineAndLatest = {
        ...all,
        summary: { ...all.summary, runs: 2, experiments: 2 },
        runs: [all.runs[0], all.runs[2]],
      };
      hooks.runs.mockImplementation((_dataset, _row, { scope } = {}) =>
        answer(scope === "baseline_latest" ? baselineAndLatest : all),
      );
      hooks.datasetCatalog = datasetCatalogOf({
        [RUN_A]: "full",
        [RUN_B]: "full",
        [RUN_C]: "full",
      });
      const NOT_SHOWN =
        "A ticked run isn't in this view. Show all runs to see or untick it.";
      mount();
      for (const label of ["#1", "#2", "#3"]) fireEvent.click(checkbox(label));
      expect(
        screen.getByRole("button", { name: "Compare side by side (3)" }),
      ).toBeEnabled();
      fireEvent.click(screen.getByRole("button", { name: /^Show:/ }));
      fireEvent.click(
        screen.getByRole("menuitem", { name: "Baseline + latest" }),
      );
      expect(screen.queryByTestId("row-run-#2")).toBeNull();
      const compare = screen.getByRole("button", {
        name: "Compare side by side (3)",
      });
      expect(compare).toBeDisabled();
      expect(compare).toHaveAccessibleDescription(NOT_SHOWN);
      fireEvent.click(compare);
      expect(window.location.pathname).toBe(`/dashboard/develop/${DATASET}`);
    });

    it("read no dataset runs when every run is in one experiment", () => {
      hooks.runs.mockReturnValue(
        answer(inOneGroup(sameExperiment(LISTED.runs))),
      );
      mount();
      tickBoth();
      openMenu();
      expect(hooks.datasetReads).toEqual([]);
    });
  });

  describe("runs of one experiment whose set-ups differ", () => {
    const SET_UPS_DIFFER =
      "Runs #1 and #2 can't be compared side by side because their set-ups differ.";
    const notice = () =>
      screen
        .getAllByText(SET_UPS_DIFFER)
        .filter((el) => !el.closest('[role="menu"]'));

    it("keeps the ticked pair closed and says why", () => {
      hooks.runs.mockReturnValue(answer(sameExperiment(LISTED.runs)));
      hooks.catalogs[EXPERIMENT] = catalogOf({
        [RUN_A]: "full",
        [RUN_B]: "subset-retry",
      });
      mount();
      fireEvent.click(checkbox("#1"));
      fireEvent.click(checkbox("#2"));
      const compare = screen.getByRole("button", {
        name: "Compare side by side (2)",
      });
      expect(compare).toBeVisible();
      expect(compare).toBeDisabled();
      expect(compare).toHaveAccessibleDescription(SET_UPS_DIFFER);
      expect(notice()).toHaveLength(1);
      expect(screen.queryByText(UNAVAILABLE)).toBeNull();
      fireEvent.click(compare);
      expect(window.location.pathname).toBe(`/dashboard/develop/${DATASET}`);
      expect(hooks.catalogReads).toContain(EXPERIMENT);
    });

    it("keeps the run menu's pair closed and says why", () => {
      hooks.runs.mockReturnValue(answer(sameExperiment(LISTED.runs)));
      hooks.catalogs[EXPERIMENT] = catalogOf({
        [RUN_A]: "full",
        [RUN_B]: "subset-retry",
      });
      mount();
      openMenu();
      const compare = screen.getByRole("menuitem", { name: /^Side by side/ });
      expect(compare).toHaveAttribute("aria-disabled", "true");
      expect(compare).toHaveAccessibleDescription(SET_UPS_DIFFER);
      expect(within(compare).getByText(SET_UPS_DIFFER)).toBeVisible();
      expect(within(compare).queryByText(UNAVAILABLE)).toBeNull();
      expect(
        screen.getByRole("menuitem", { name: "Copy run id" }),
      ).toBeInTheDocument();
      fireEvent.click(compare);
      expect(window.location.pathname).toBe(`/dashboard/develop/${DATASET}`);
    });

    it("opens a pair the catalog puts in one comparison group", () => {
      hooks.runs.mockReturnValue(answer(sameExperiment(LISTED.runs)));
      hooks.catalogs[EXPERIMENT] = catalogOf({
        [RUN_A]: "full",
        [RUN_B]: "full",
      });
      mount();
      fireEvent.click(checkbox("#1"));
      fireEvent.click(checkbox("#2"));
      const compare = screen.getByRole("button", {
        name: "Compare side by side (2)",
      });
      expect(compare).toBeEnabled();
      expect(screen.queryByText(/set-ups differ/)).toBeNull();
      fireEvent.click(compare);
      const opened = new URL(window.location.href);
      expect(opened.pathname).toBe(ON_RESULTS);
      expect(opened.searchParams.get("runs")).toBe(`${RUN_A},${RUN_B}`);
    });

    it("keeps the catalog's last answer while it is read again", () => {
      hooks.runs.mockReturnValue(answer(sameExperiment(LISTED.runs)));
      hooks.catalogs[EXPERIMENT] = catalogOf({
        [RUN_A]: "full",
        [RUN_B]: "full",
      });
      hooks.catalogFetching[EXPERIMENT] = true;
      mount();
      fireEvent.click(checkbox("#1"));
      fireEvent.click(checkbox("#2"));
      expect(
        screen.getByRole("button", { name: "Compare side by side (2)" }),
      ).toBeEnabled();
      expect(screen.queryByText(CHECKING)).toBeNull();
    });

    it("reads no experiment's catalog when every run is in another experiment", () => {
      hooks.runs.mockReturnValue(answer(LISTED));
      mount();
      fireEvent.click(checkbox("#1"));
      fireEvent.click(checkbox("#2"));
      openMenu();
      expect(hooks.catalogReads).toEqual([]);
      expect(hooks.datasetReads).toContain(DATASET);
    });
  });

  describe("runs of one experiment while their set-ups are unknown", () => {
    const ON_DATASET = `/dashboard/develop/${DATASET}`;
    const tickBoth = () => {
      fireEvent.click(checkbox("#1"));
      fireEvent.click(checkbox("#2"));
      return screen.getByRole("button", { name: "Compare side by side (2)" });
    };

    it("keeps the ticked pair off while the catalog is being read", () => {
      hooks.runs.mockReturnValue(answer(sameExperiment(LISTED.runs)));
      mount();
      const compare = tickBoth();
      expect(compare).toBeVisible();
      expect(compare).toBeDisabled();
      expect(compare).toHaveAccessibleDescription(CHECKING);
      expect(screen.getAllByText(CHECKING)).toHaveLength(1);
      expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
      fireEvent.click(compare);
      expect(window.location.pathname).toBe(ON_DATASET);
      expect(hooks.catalogReads).toContain(EXPERIMENT);
    });

    it("keeps the run menu's pair off while the catalog is being read", () => {
      hooks.runs.mockReturnValue(answer(sameExperiment(LISTED.runs)));
      mount();
      openMenu();
      const menu = screen.getByRole("menu");
      const compare = within(menu).getByRole("menuitem", {
        name: /^Side by side/,
      });
      expect(compare).toHaveAttribute("aria-disabled", "true");
      expect(compare).toHaveAccessibleDescription(CHECKING);
      expect(within(compare).getByText(CHECKING)).toBeVisible();
      expect(
        within(menu).queryByRole("menuitem", { name: "Try again" }),
      ).toBeNull();
      expect(
        within(menu).getByRole("menuitem", { name: "Copy run id" }),
      ).toBeInTheDocument();
      fireEvent.click(compare);
      expect(window.location.pathname).toBe(ON_DATASET);
    });

    it("keeps the ticked pair off when the catalog read fails, and tries again", () => {
      hooks.runs.mockReturnValue(answer(sameExperiment(LISTED.runs)));
      hooks.catalogFailed[EXPERIMENT] = true;
      mount();
      const compare = tickBoth();
      expect(compare).toBeVisible();
      expect(compare).toBeDisabled();
      expect(compare).toHaveAccessibleDescription(CHECK_FAILED);
      expect(screen.getAllByText(CHECK_FAILED)).toHaveLength(1);
      fireEvent.click(compare);
      expect(window.location.pathname).toBe(ON_DATASET);
      expect(hooks.catalogRefetches).toEqual([]);
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(hooks.catalogRefetches).toEqual([EXPERIMENT]);
      expect(window.location.pathname).toBe(ON_DATASET);
    });

    it("keeps the run menu's pair off when the catalog read fails, and tries again", () => {
      hooks.runs.mockReturnValue(answer(sameExperiment(LISTED.runs)));
      hooks.catalogFailed[EXPERIMENT] = true;
      mount();
      openMenu();
      const menu = screen.getByRole("menu");
      const compare = within(menu).getByRole("menuitem", {
        name: /^Side by side/,
      });
      expect(compare).toHaveAttribute("aria-disabled", "true");
      expect(compare).toHaveAccessibleDescription(CHECK_FAILED);
      expect(within(compare).getByText(CHECK_FAILED)).toBeVisible();
      fireEvent.click(compare);
      expect(window.location.pathname).toBe(ON_DATASET);
      expect(hooks.catalogRefetches).toEqual([]);
      fireEvent.click(
        within(menu).getByRole("menuitem", { name: "Try again" }),
      );
      expect(hooks.catalogRefetches).toEqual([EXPERIMENT]);
      // The menu stays open to show the answer.
      expect(screen.getByRole("menu")).toBeInTheDocument();
      expect(window.location.pathname).toBe(ON_DATASET);
    });

    it("keeps a pair off when the catalog does not list one of its runs", () => {
      hooks.runs.mockReturnValue(answer(sameExperiment(LISTED.runs)));
      hooks.catalogs[EXPERIMENT] = catalogOf({ [RUN_A]: "full" });
      mount();
      const compare = tickBoth();
      expect(compare).toBeDisabled();
      expect(compare).toHaveAccessibleDescription(CHECK_FAILED);
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(hooks.catalogRefetches).toEqual([EXPERIMENT]);
    });

    it("keeps a pair off when the catalog names no comparison group", () => {
      hooks.runs.mockReturnValue(answer(sameExperiment(LISTED.runs)));
      hooks.catalogs[EXPERIMENT] = {
        runs: [{ run_id: RUN_A }, { run_id: RUN_B }],
      };
      mount();
      const compare = tickBoth();
      expect(compare).toBeDisabled();
      expect(compare).toHaveAccessibleDescription(CHECK_FAILED);
      expect(screen.queryByText(/set-ups differ/)).toBeNull();
      fireEvent.click(compare);
      expect(window.location.pathname).toBe(ON_DATASET);
    });

    it("keeps the run menu's pair off when the catalog names no comparison group", () => {
      hooks.runs.mockReturnValue(answer(sameExperiment(LISTED.runs)));
      hooks.catalogs[EXPERIMENT] = {
        runs: [{ run_id: RUN_A }, { run_id: RUN_B }],
      };
      mount();
      openMenu();
      const compare = within(screen.getByRole("menu")).getByRole("menuitem", {
        name: /^Side by side/,
      });
      expect(compare).toHaveAttribute("aria-disabled", "true");
      expect(compare).toHaveAccessibleDescription(CHECK_FAILED);
      fireEvent.click(compare);
      expect(window.location.pathname).toBe(ON_DATASET);
    });
  });

  it("keeps the four-run limit and lets an unticked run replace a selection", () => {
    hooks.runs.mockReturnValue(answer(inOneGroup(FIVE)));
    mount();
    expect(screen.queryByText(UP_TO_FOUR)).toBeNull();
    for (const label of ["#1", "#2", "#3", "#4"])
      fireEvent.click(checkbox(label));
    expect(screen.getByText(UP_TO_FOUR)).toBeInTheDocument();
    expect(checkbox("#5")).toBeDisabled();
    fireEvent.click(checkbox("#5"));
    expect(checkbox("#5")).not.toBeChecked();
    expect(
      screen.getByRole("button", { name: "Compare side by side (4)" }),
    ).toBeEnabled();
    fireEvent.click(checkbox("#1"));
    expect(checkbox("#5")).toBeEnabled();
    fireEvent.click(checkbox("#5"));
    fireEvent.click(
      screen.getByRole("button", { name: "Compare side by side (4)" }),
    );
    const opened = new URL(window.location.href);
    expect(opened.pathname).toBe(ON_RESULTS);
    expect(opened.searchParams.get("runs")).toBe(
      [RUN_B, RUN_E, RUN_D, RUN_C].join(","),
    );
    expect(opened.searchParams.get("baseline")).toBe(RUN_B);
  });
});
