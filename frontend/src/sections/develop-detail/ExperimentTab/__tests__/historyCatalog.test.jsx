import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { useLeaderboardView } from "../useLeaderboard";
import { VersionSelect } from "../LeaderboardToolbarMenus";
import LeaderboardFilters from "../LeaderboardFilters";
import EvalHoverCard from "../EvalHoverCard";
import {
  aggValueOf,
  compareSelection,
  compareUrl,
  filterRuns,
} from "../leaderboardModel";
import { columnCatalog } from "../leaderboardColumns";
import { leaderboardFilterDefinition } from "../leaderboardFilterDefinition";
import { lowSampleNote, normalizedScore, scoreDefs } from "../metricStripModel";
import RunPickerPopover from "src/sections/common/Runs/RunPickerPopover";

vi.mock("../useLeaderboardActions", () => ({
  usePendingDeletes: (select) => select({ byDataset: {} }),
}));
vi.mock("src/sections/common/Analysis/workbench/JudgeTrust", () => ({
  TrustLine: () => null,
  useJudgeTrust: () => null,
}));

const DATASET = "10000000-0000-4000-8000-000000000001";
const VERSIONS = [
  "20000000-0000-4000-8000-000000000001",
  "20000000-0000-4000-8000-000000000002",
];
const IDS = [
  "30000000-0000-4000-8000-000000000001",
  "30000000-0000-4000-8000-000000000002",
  "30000000-0000-4000-8000-000000000003",
];
const KEY = "40000000-0000-4000-8000-000000000001";
const metric = {
  key: KEY,
  name: "Correctness",
  output_type: "score",
  higher_is_better: true,
  runs: IDS,
  versions: {},
};
const run = (i, extra = {}) => ({
  run_id: IDS[i],
  ordinal: i + 1,
  label: `#${i + 1}`,
  name: `Candidate ${i + 1}`,
  status: "completed",
  listed: true,
  final: true,
  task_kind: "prompt",
  target_type: "prompt",
  target: { prompt_id: `prompt-${i}` },
  dataset_version_id: VERSIONS[i === 2 ? 1 : 0],
  dataset_version: {
    id: VERSIONS[i === 2 ? 1 : 0],
    content_hash: i === 2 ? "bbbbbbbb" : "aaaaaaaa",
  },
  comparison_group: i === 2 ? "other" : "shared",
  progress: { done: 10, scored: 10, total: 10, errors: i === 0 ? 2 : 0 },
  metrics: { [KEY]: { value: 0.8, n: 10, rule: "assessed_trial_mean" } },
  created_at: "2026-01-01T00:00:00Z",
  ...extra,
});
const filter = (column_id, filter_value, filter_op = "equals") => ({
  column_id,
  filter_config: { filter_type: "option", filter_op, filter_value },
});
let client;
let get;
let runs;
let view;
let pin;

function serve(url, { params = {} } = {}) {
  let results = runs;
  if (params.same_target_as) {
    const reference = runs.find((r) => r.run_id === params.same_target_as);
    if (!reference?.target?.prompt_id) {
      return Promise.reject({ response: { status: 409 } });
    }
    results = runs.filter(
      (r) => r.target?.prompt_id === reference.target.prompt_id,
    );
  }
  if (params.dataset_version_id) {
    if (!VERSIONS.includes(params.dataset_version_id)) {
      return Promise.reject({ response: { status: 400 } });
    }
    results = results.filter(
      (r) => r.dataset_version_id === params.dataset_version_id,
    );
  }
  const baseline = params.baseline === "none" ? null : params.baseline || pin;
  return Promise.resolve({
    data: {
      status: true,
      result: {
        results,
        count: results.length,
        offset: 0,
        has_more: false,
        next: null,
        pinned_run_id: pin,
        baseline_run_id: baseline,
        baseline: { run_id: baseline },
        baseline_run: runs.find((r) => r.run_id === baseline) ?? null,
        metric_defs: [metric],
      },
    },
  });
}

function History() {
  view = useLeaderboardView();
  return (
    <>
      <VersionSelect
        versions={view.versions}
        value={view.version}
        onChange={view.setVersion}
      />
      <button onClick={() => view.setBaseline(IDS[1])}>Choose baseline</button>
      {view.query.isError && <div role="alert">{view.query.error.message}</div>}
      <output data-testid="visible-runs">
        {view.runs.map((r) => r.run_id).join(",")}
      </output>
    </>
  );
}
const mount = (element = <History />, search = "") => {
  window.history.replaceState({}, "", `/history/${DATASET}${search}`);
  return render(
    <QueryClientProvider client={client}>
      <Routes>
        <Route path="/history/:dataset" element={element} />
      </Routes>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  runs = [run(0), run(1), run(2)];
  pin = IDS[0];
  get = vi.spyOn(axios, "get").mockImplementation(serve);
});
afterEach(() => {
  cleanup();
  client.clear();
  vi.restoreAllMocks();
});

describe("Canonical history", () => {
  it("uses known error counts and excludes unknown evidence from both choices", () => {
    const unknown = run(2, { progress: {}, metrics: {} });
    const list = [run(0), run(1), unknown];
    expect(
      filterRuns(list, [filter("has_errors", "yes")]).map((r) => r.run_id),
    ).toEqual([IDS[0]]);
    expect(
      filterRuns(list, [filter("has_errors", "no")]).map((r) => r.run_id),
    ).toEqual([IDS[1]]);
    expect(
      filterRuns(list, [filter("has_errors", "yes", "not_equals")]).map(
        (r) => r.run_id,
      ),
    ).toEqual([IDS[1]]);
    const legacy = run(2, { progress: {}, metrics: { error_count: 3 } });
    expect(filterRuns([legacy], [filter("has_errors", "yes")])).toEqual([
      legacy,
    ]);
    const canonical = run(1, { metrics: { error_count: 3 } });
    expect(filterRuns([canonical], [filter("has_errors", "no")])).toEqual([
      canonical,
    ]);
  });

  it("offers assessed evaluator means to columns charts and filters without invented statistics", async () => {
    mount();
    await waitFor(() => expect(view.allRuns.length).toBe(3));
    expect(view.metricDefs.map((d) => [d.key, d.kind])).toEqual([
      [KEY, "eval"],
    ]);
    expect(
      columnCatalog(view.metricDefs, view.allRuns)
        .filter((c) => c.id === KEY)
        .map((c) => c.label),
    ).toEqual(["Correctness"]);
    expect(
      leaderboardFilterDefinition(view.allRuns, view.metricDefs)
        .find((f) => f.propertyName === "Evals")
        .dependents.map((d) => d.propertyId),
    ).toEqual([`metric:${KEY}`]);
    expect(scoreDefs(view.metricDefs).map((d) => d.key)).toEqual([KEY]);
    const scored = view.allRuns[0];
    const def = view.metricDefs[0];
    expect(aggValueOf(scored, def)).toBe(0.8);
    expect(normalizedScore(scored, def)).toBe(80);
    expect(lowSampleNote(scored, def)).toBe("Low sample · n=10");
    expect(scored.metrics.aggregates[KEY]).toEqual({
      avg: 0.8,
      n: 10,
      rule: "assessed_trial_mean",
      median: null,
      ci95: null,
    });
    expect(scored.metrics[KEY]).toEqual(runs[0].metrics[KEY]);
    expect(aggValueOf(scored, def, "median")).toBeNull();
    expect(scored.metrics.deltas).toBeUndefined();
    expect(
      filterRuns(
        [scored],
        [filter(`metric:${KEY}`, [0.7], "greater_than")],
        view.defsByKey,
      ).map((r) => r.run_id),
    ).toEqual([IDS[0]]);
    expect(runs[0].metrics.aggregates).toBeUndefined();
  });

  it("renders canonical multi-trial evidence through the history hover card", async () => {
    runs = [0, 1].map((i) =>
      run(i, {
        trials: 3,
        progress: { done: 2, scored: 2, total: 2, errors: 0 },
        metrics: {
          [KEY]: { value: i / 2, n: 6, rule: "assessed_trial_mean" },
        },
      }),
    );
    get.mockImplementation(async (...args) => {
      const response = await serve(...args);
      response.data.result.metric_defs = [
        { ...metric, output_type: "pass_fail" },
      ];
      return response;
    });
    mount();
    await waitFor(() => expect(view.allRuns.length).toBe(2));
    render(
      <EvalHoverCard
        def={view.metricDefs[0]}
        run={view.allRuns[1]}
        baselineRun={view.baselineRun}
        datasetId={DATASET}
      />,
    );
    expect(screen.getByText("Pass rate 50.0% · Std — · n 6").textContent).toBe(
      "Pass rate 50.0% · Std — · n 6",
    );
    expect(screen.queryByText(/^95% interval /)).toBeNull();
    expect(screen.getByText(/^Paired statistics unavailable/).textContent).toBe(
      "Paired statistics unavailable for Correctness vs #1. Both runs have scores, but no paired comparison is available.",
    );
    expect(screen.queryByText(/did not score/)).toBeNull();
    expect(screen.queryByText("How rows are counted")).toBeNull();
  });

  it.each([true, false])(
    "uses the view baseline with a persistent pin present=%s",
    async (pinned) => {
      pin = pinned ? IDS[0] : null;
      mount();
      await waitFor(() => expect(view.allRuns.length).toBe(3));
      fireEvent.click(screen.getByText("Choose baseline"));
      await waitFor(() => expect(view.baseline.run_id).toBe(IDS[1]));
      expect(view.baselineRun.run_id).toBe(IDS[1]);
      expect(view.pinnedRunId).toBe(pin);
      expect(new URLSearchParams(window.location.search).get("baseline")).toBe(
        IDS[1],
      );
      const selection = compareSelection([runs[2]], view.baseline.run_id);
      expect(selection.runIds).toEqual([IDS[1], IDS[2]]);
      expect(selection.baselineId).toBe(IDS[1]);
      const params = new URLSearchParams(
        compareUrl(DATASET, selection).split("?")[1],
      );
      expect(params.get("runs")).toBe(`${IDS[1]},${IDS[2]}`);
      expect(params.get("baseline")).toBe(IDS[1]);
    },
  );

  it("keeps every immutable version available while requesting the selected UUID", async () => {
    mount();
    await waitFor(() =>
      expect(view.versions.map((v) => v.id)).toEqual(VERSIONS),
    );
    fireEvent.click(screen.getByRole("button", { name: /Version:/ }));
    fireEvent.click(screen.getByText(`Version ${VERSIONS[1]}`));
    await waitFor(() =>
      expect(view.runs.map((r) => r.run_id)).toEqual([IDS[2]]),
    );
    expect(view.versions.map((v) => v.id)).toEqual(VERSIONS);
    expect(new URLSearchParams(window.location.search).get("version")).toBe(
      VERSIONS[1],
    );
    expect(
      get.mock.calls.some(
        ([, options]) => options.params.dataset_version_id === VERSIONS[1],
      ),
    ).toBe(true);
    expect(screen.queryByText(/undefined rows/)).toBeNull();
  });

  it("refuses an unresolved numbered link without sending it as a version UUID", async () => {
    mount(<History />, "?version=2");
    await waitFor(() => expect(view.query.isError).toBe(true));
    expect(screen.getByRole("alert").textContent).toBe(
      "This dataset version selection is unavailable. Choose a version again.",
    );
    expect(screen.getByTestId("visible-runs").textContent).toBe("");
    expect(
      get.mock.calls
        .map(([, options]) => options.params.dataset_version_id)
        .filter(Boolean),
    ).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: /Version:/ }));
    fireEvent.click(screen.getByText("All versions"));
    await waitFor(() => expect(view.allRuns.length).toBe(3));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("opens version repair even when the normal filter panel is closed", () => {
    const onVersion = vi.fn();
    mount(
      <LeaderboardFilters
        open={false}
        onClose={vi.fn()}
        filters={[]}
        onChange={vi.fn()}
        runs={[]}
        metricDefs={[]}
        version="2"
        versions={VERSIONS.map((id) => ({ id }))}
        onVersion={onVersion}
      />,
    );
    expect(
      screen.getByText(
        "This dataset version selection is unavailable. Choose a version again.",
      ).textContent,
    ).toBe(
      "This dataset version selection is unavailable. Choose a version again.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(onVersion.mock.calls).toEqual([["all"]]);
  });

  it.each(["prompt", "http", "external"])(
    "offers other dataset targets when adding to a %s run",
    async (kind) => {
      runs = [
        run(0, {
          task_kind: kind,
          target_type: kind,
          target: kind === "prompt" ? { prompt_id: "first" } : null,
        }),
        run(1),
      ];
      const onPick = vi.fn();
      mount(
        <RunPickerPopover
          datasetId={DATASET}
          excludeIds={[IDS[0]]}
          groupBySameTarget
          onPick={onPick}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Add runs" }));
      await waitFor(() => expect(screen.getByText("#2")).toBeTruthy());
      expect(
        get.mock.calls
          .map(([, options]) => options.params.same_target_as)
          .filter(Boolean),
      ).toEqual([]);
      fireEvent.click(screen.getByText("#2"));
      fireEvent.click(screen.getByRole("button", { name: "Add run" }));
      expect(onPick.mock.calls).toEqual([[[IDS[1]]]]);
    },
  );
});
