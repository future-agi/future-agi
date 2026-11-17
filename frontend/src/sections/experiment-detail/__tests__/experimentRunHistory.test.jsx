import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ExperimentDataView from "../ExperimentData/ExperimentDataView";
import ExperimentSummaryView from "../ExperimentSummary/ExperimentSummaryView";

// Run history does not exercise the newly visible evaluator query panel.
vi.mock("../ExperimentSummary/SummaryEvaluators", () => ({
  default: () => null,
}));

const state = vi.hoisted(() => ({ experiment: null, analysis: vi.fn() }));
vi.mock("src/api/develop/runs", () => ({
  useExperimentDetail: () => ({ data: state.experiment }),
  useDatasetRuns: () => ({ data: { pinned_run_id: "original-a" } }),
}));
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: [] }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
vi.mock("src/utils/axios", () => ({ default: {}, endpoints: {} }));
vi.mock("src/api/develop/run-results", () => ({ runResultsKey: () => [] }));
vi.mock("src/sections/common/Runs/runStatus", () => ({
  pollIntervalFor: () => false,
}));
vi.mock("src/sections/common/EvaluationDrawer/EvaluationDrawer", () => ({
  default: () => null,
}));
vi.mock("../experiment-context", () => ({
  useExperimentDetailContext: () => ({
    evaluateOpen: false,
    setEvaluateOpen: vi.fn(),
  }),
}));
vi.mock("src/api/develop/analysis", () => ({ useRunAnalysis: state.analysis }));
vi.mock("../results/LaunchScopeNotice", () => ({ default: () => null }));
/* eslint-disable react/prop-types */
vi.mock("../results/RunResultsView", () => ({
  default: ({ runIds, baselineId, initialRuns }) => (
    <div data-testid="data-selection">
      {JSON.stringify({ runIds, baselineId, initialRuns })}
    </div>
  ),
}));
/* eslint-enable react/prop-types */

const originalRuns = [
  { run_id: "original-a", ordinal: 2, comparison_group: "full-plan" },
  { run_id: "original-b", ordinal: 1, comparison_group: "full-plan" },
];
const retry = { run_id: "retry", ordinal: 3, comparison_group: "subset-plan" };

function LocationProbe() {
  return <div data-testid="location">{useLocation().search}</div>;
}

function mount(View, search = "") {
  render(
    <MemoryRouter initialEntries={[`/experiment/experiment-a/data${search}`]}>
      <LocationProbe />
      <Routes>
        <Route path="/experiment/:experimentId/:tab" element={<View />} />
      </Routes>
    </MemoryRouter>,
  );
}

function selection(tab) {
  return tab === "Data"
    ? JSON.parse(screen.getByTestId("data-selection").textContent)
    : state.analysis.mock.calls.at(-1)[1];
}

beforeEach(() => {
  state.experiment = {
    dataset_id: "dataset-a",
    runs: [retry, ...originalRuns],
  };
  state.analysis.mockReset().mockReturnValue({});
});
afterEach(cleanup);

describe("Results links with selected experiment runs", () => {
  it("opens exactly the requested historical runs in URL order", () => {
    state.experiment.runs = [
      retry,
      { run_id: "original-c", ordinal: 4, comparison_group: "full-plan" },
      ...originalRuns,
    ];
    mount(
      ExperimentDataView,
      "?runs=original-b,original-a&baseline=original-b",
    );
    expect(selection("Data").runIds).toEqual(["original-b", "original-a"]);
    expect(selection("Data").baselineId).toBe("original-b");
    expect(selection("Data").initialRuns).toEqual([
      originalRuns[1],
      originalRuns[0],
    ]);
    expect(
      screen.getByRole("combobox", { name: "Run history" }),
    ).toHaveDisplayValue("Selected runs (2)");
    expect(
      screen.getByText(
        "Showing the runs selected in the link. Choose a history group to change the comparison.",
      ),
    ).toBeInTheDocument();
  });

  it("can return from a linked subset to the experiment's only history group", () => {
    state.experiment.runs = originalRuns;
    mount(ExperimentDataView, "?runs=original-b&baseline=original-b");
    fireEvent.change(screen.getByRole("combobox", { name: "Run history" }), {
      target: { value: "original-a" },
    });
    expect(selection("Data").runIds).toEqual(["original-a", "original-b"]);
    expect(
      new URLSearchParams(screen.getByTestId("location").textContent).has(
        "runs",
      ),
    ).toBe(false);
  });

  it("never passes a run outside the readable experiment to Results", () => {
    mount(ExperimentDataView, "?runs=foreign,original-b&baseline=foreign");
    expect(selection("Data").runIds).toEqual(["original-b"]);
    expect(selection("Data").baselineId).toBe("original-b");
    expect(selection("Data").initialRuns).toEqual([originalRuns[1]]);
  });

  it("does not replace an entirely missing selection with unrelated runs", () => {
    mount(ExperimentDataView, "?runs=foreign&baseline=foreign");
    expect(selection("Data").runIds).toEqual([]);
    expect(selection("Data").baselineId).toBeNull();
  });

  it("keeps a compatible history group when the selected runs' set-ups differ", () => {
    mount(ExperimentDataView, "?runs=original-a,retry&baseline=original-a");
    // The newest group is read alone, never the selected runs together.
    expect(selection("Data").runIds).toEqual(["retry"]);
    expect(selection("Data").baselineId).toBe("retry");
    expect(selection("Data").initialRuns).toEqual([retry]);
    expect(
      screen.getByRole("combobox", { name: "Run history" }),
    ).toHaveDisplayValue("Run #3");
    expect(screen.queryByRole("option", { name: /Selected runs/ })).toBeNull();
    expect(
      screen.getByText(
        "Runs #2 and #3 can't be compared side by side because their set-ups differ.",
      ),
    ).toBeInTheDocument();
  });

  it("never opens linked runs together when the catalog names no comparison group", () => {
    state.experiment.runs = [
      { run_id: "older-b", ordinal: 2 },
      { run_id: "older-a", ordinal: 1 },
    ];
    mount(ExperimentDataView, "?runs=older-a,older-b&baseline=older-a");
    // Run history still lists both as one group, but that group is unproven
    // too, so only its newest run is read.
    expect(selection("Data").runIds).toEqual(["older-b"]);
    expect(screen.queryByRole("option", { name: /Selected runs/ })).toBeNull();
    expect(
      screen.getByText("Couldn't check whether these runs can be compared."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/set-ups differ/)).toBeNull();
  });

  it("keeps the linked baseline's history group when the selected runs' set-ups differ", () => {
    mount(
      ExperimentDataView,
      "?runs=original-a,retry&baseline=original-a&history=original-a",
    );
    expect(selection("Data").runIds).toEqual(["original-a", "original-b"]);
    expect(selection("Data").baselineId).toBe("original-a");
    expect(
      screen.getByRole("combobox", { name: "Run history" }),
    ).toHaveDisplayValue("Runs #2, #1");
    expect(
      screen.getByText(
        "Runs #2 and #3 can't be compared side by side because their set-ups differ.",
      ),
    ).toBeInTheDocument();
    // Choosing a group clears the link and its line.
    fireEvent.change(screen.getByRole("combobox", { name: "Run history" }), {
      target: { value: "retry" },
    });
    expect(selection("Data").runIds).toEqual(["retry"]);
    expect(
      new URLSearchParams(screen.getByTestId("location").textContent).has(
        "runs",
      ),
    ).toBe(false);
    expect(screen.queryByText(/set-ups differ/)).toBeNull();
  });

  it("clears the linked run selection when a history group is chosen", () => {
    mount(
      ExperimentDataView,
      "?runs=original-b,original-a&history=original-b&baseline=original-b",
    );
    fireEvent.change(screen.getByRole("combobox", { name: "Run history" }), {
      target: { value: "retry" },
    });
    expect(selection("Data").runIds).toEqual(["retry"]);
    const params = new URLSearchParams(
      screen.getByTestId("location").textContent,
    );
    expect(params.has("runs")).toBe(false);
    expect(params.has("baseline")).toBe(false);
  });
});

describe.each([
  ["Data", ExperimentDataView],
  ["Summary", ExperimentSummaryView],
])("W10-EXPERIMENTS-DATASETS-L01 %s history", (tab, View) => {
  it("defaults to the newest compatible group after a subset retry", () => {
    mount(View);
    expect(selection(tab).runIds).toEqual(["retry"]);
    expect(selection(tab).baselineId).not.toBe("original-a");
    expect(screen.getByRole("combobox", { name: "Run history" }).value).toBe(
      "retry",
    );
  });

  it("does not combine the full plan and retry even with baseline=none", () => {
    mount(View, "?baseline=none");
    expect(selection(tab).runIds).toEqual(["retry"]);
    expect(selection(tab).baselineId).toBe("none");
  });

  it("keeps the original compatible comparison accessible and clears stale scoped state", () => {
    mount(View, "?baseline=retry&cand=retry&row=retry-row&filters=%5B%5D");
    fireEvent.change(screen.getByRole("combobox", { name: "Run history" }), {
      target: { value: "original-a" },
    });
    expect(selection(tab).runIds).toEqual(["original-a", "original-b"]);
    expect(selection(tab).baselineId).not.toBe("retry");
    const params = new URLSearchParams(
      screen.getByTestId("location").textContent,
    );
    expect(params.get("history")).toBe("original-a");
    ["baseline", "cand", "row", "filters"].forEach((key) =>
      expect(params.has(key)).toBe(false),
    );
    expect(state.experiment.runs).toEqual([retry, ...originalRuns]);
  });

  it("restores a historical group from the URL without a foreign baseline or candidate", () => {
    mount(View, "?history=original-a&baseline=retry&cand=retry");
    expect(selection(tab).runIds).toEqual(["original-a", "original-b"]);
    expect(selection(tab).baselineId).not.toBe("retry");
    if (tab === "Summary") expect(selection(tab).cand).toBeUndefined();
  });

  it("keeps an already compatible experiment's comparison unchanged", () => {
    state.experiment.runs = originalRuns;
    mount(View, "?baseline=original-b");
    expect(selection(tab).runIds).toEqual(["original-a", "original-b"]);
    expect(selection(tab).baselineId).toBe("original-b");
    expect(
      screen.queryByRole("combobox", { name: "Run history" }),
    ).not.toBeInTheDocument();
  });

  it("falls back safely when a historical selection is no longer present", () => {
    mount(View, "?history=deleted-run");
    expect(selection(tab).runIds).toEqual(["retry"]);
  });

  it("limits each compatible history selection to the reader's eight-run bound", () => {
    state.experiment.runs = Array.from({ length: 9 }, (_, index) => ({
      run_id: `run-${index}`,
      ordinal: 9 - index,
      comparison_group: "full-plan",
    }));
    mount(View);
    expect(selection(tab).runIds).toHaveLength(8);
    fireEvent.change(screen.getByRole("combobox", { name: "Run history" }), {
      target: { value: "run-8" },
    });
    expect(selection(tab).runIds).toEqual(["run-8"]);
  });
});
