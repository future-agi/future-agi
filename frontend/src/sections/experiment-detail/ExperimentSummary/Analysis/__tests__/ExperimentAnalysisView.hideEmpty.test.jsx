// With "Hide rows with no output" on in the Data tab, the link keeps the
// switch's expression in `filters`. The analysis endpoint refuses that
// expression with a 400, so the Summary showed "…not supported. Check the
// link." and no summary. The Summary reads without the switch's filter, keeps
// every other filter, and says in one line that the switch applies only to the
// Data tab.
import React from "react";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderWithRouter, screen } from "src/utils/test-utils";
import { expressionPill } from "src/sections/common/filters/expressionPill";
import { HIDE_EMPTY_EXPR } from "src/sections/common/filters/hideEmpty";
import ExperimentAnalysisView from "../ExperimentAnalysisView";

const hooks = vi.hoisted(() => ({ analysis: vi.fn(), stats: vi.fn() }));
vi.mock("src/api/develop/analysis", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunAnalysis: hooks.analysis,
}));
vi.mock("src/api/develop/experiment-stats", async (importOriginal) => ({
  ...(await importOriginal()),
  useExperimentStats: hooks.stats,
}));

const DATASET = "11111111-1111-4111-8111-111111111111";
const RUN_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const RUN_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const SPLIT = { type: "split", values: ["test"] };
const NOTE =
  "The Hide rows with no output switch applies only to the Data tab. It is not applied here.";

beforeEach(() => {
  vi.clearAllMocks();
  hooks.analysis.mockReturnValue({
    data: undefined,
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  hooks.stats.mockReturnValue({
    data: undefined,
    isPending: false,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
});

function renderSummary(filters) {
  const search = filters
    ? `?filters=${encodeURIComponent(JSON.stringify(filters))}`
    : "";
  return renderWithRouter(
    <Routes>
      <Route
        path="/dashboard/develop/experiment/:experimentId/summary"
        element={
          <ExperimentAnalysisView
            datasetId={DATASET}
            runIds={[RUN_A, RUN_B]}
            baselineId={RUN_A}
            mode="experiment"
          />
        }
      />
    </Routes>,
    { route: `/dashboard/develop/experiment/E1/summary${search}` },
  );
}

const sentFilters = () => hooks.analysis.mock.calls.at(-1)[1].filters;

describe("Summary with Hide rows with no output on", () => {
  it("reads the analysis without the switch's filter and keeps the others", () => {
    renderSummary([SPLIT, expressionPill(HIDE_EMPTY_EXPR)]);

    expect(hooks.analysis).toHaveBeenCalled();
    expect(sentFilters()).toEqual([SPLIT]);
  });

  it("says once that the switch applies only to the Data tab", () => {
    renderSummary([expressionPill(HIDE_EMPTY_EXPR)]);

    expect(screen.getByTestId("summary-hide-empty-note")).toHaveTextContent(
      NOTE,
    );
    expect(sentFilters()).toEqual([]);
  });

  it("changes nothing when the switch is off", () => {
    renderSummary([SPLIT]);

    expect(sentFilters()).toEqual([SPLIT]);
    expect(screen.queryByTestId("summary-hide-empty-note")).toBeNull();
  });
});
