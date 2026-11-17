import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { ciCommentStep } from "src/sections/common/RunSources/snippets/ciComment";
import ExperimentSummaryView from "../ExperimentSummaryView";

const http = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: http,
  endpoints: {
    develop: {
      experiment: {
        getExperimentDetails: (id) => `/experiments/v2/${id}/`,
      },
      datasetRuns: {
        analysis: (id) => `/datasets/${id}/analysis/`,
      },
    },
  },
}));
// Unrelated ranking and summary-evaluator reads are isolated. The routed
// Summary, history, detail/analysis hooks, CI panel and copy control are real.
vi.mock("../SummaryEvaluators", () => ({ default: () => null }));
vi.mock("src/api/develop/experiment-stats", () => ({
  useExperimentStats: () => ({ data: undefined, isPending: true }),
}));

const surface = "exp.results.ciComment";
const experimentId = "10000000-0000-4000-8000-000000000001";
const candidate = "20000000-0000-4000-8000-000000000002";
const baseline = "30000000-0000-4000-8000-000000000003";
const datasetId = "40000000-0000-4000-8000-000000000004";
const versionId = "50000000-0000-4000-8000-000000000005";
const absent = "60000000-0000-4000-8000-000000000006";
const runs = [candidate, baseline].map((runId, index) => ({
  run_id: runId,
  dataset_id: datasetId,
  dataset_version_id: versionId,
  experiment_id: experimentId,
  label: index === 0 ? "#2 Candidate" : "#1 Baseline",
  name: null,
  status: "completed",
  task_kind: "code",
  comparison_group: "same-plan",
  planned_trials: 2,
  recorded_trials: 2,
  missing_trials: 0,
}));
const unavailable = { state: "unavailable", reason: "Not computed." };
const analysis = {
  dataset_id: datasetId,
  running: false,
  runs,
  baseline_run_id: baseline,
  baseline_label: "#1 Baseline",
  candidate_run_id: candidate,
  examples_in_view: 2,
  examples_total: 2,
  scope: { complete: true, population: "full_filtered_plan" },
  metrics: [],
  verdicts: [],
  default_evals: [],
  breakdown_options: [],
  preference: null,
  human: null,
  agent: null,
  worklist: null,
  breakdown: null,
  pattern: null,
  root_causes: null,
  observed_failures: null,
  availability: {
    uncertainty: unavailable,
    candidate: { state: "available", reason: null },
    root_causes: unavailable,
    ops: unavailable,
    actions: unavailable,
    preference: unavailable,
    human: unavailable,
    agent: unavailable,
    worklist: unavailable,
    breakdown: unavailable,
  },
};
const detail = {
  experiment_id: experimentId,
  dataset_id: datasetId,
  runs,
  runs_total: 2,
  runs_has_more: false,
};
const clients = [];
const clipboardDescriptor = Object.getOwnPropertyDescriptor(
  navigator,
  "clipboard",
);
const response = (result) => ({ data: { status: true, result } });

beforeEach(() => {
  http.get.mockReset();
  http.get.mockImplementation((url) => {
    if (url === `/experiments/v2/${experimentId}/`)
      return Promise.resolve(response(detail));
    if (url === `/datasets/${datasetId}/analysis/`)
      return Promise.resolve(response(analysis));
    throw new Error(`Unexpected fixture request: ${url}`);
  });
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
  if (clipboardDescriptor)
    Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
  else delete navigator.clipboard;
  vi.restoreAllMocks();
});

function renderSummary({ claimed = true, allOn = false, requested } = {}) {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: true,
    madeReal: claimed ? [surface] : [],
    showUnreleased: allOn,
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  clients.push(client);
  const search = requested ? `?baseline=${requested}` : "";
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[
          `/dashboard/develop/experiment/${experimentId}/summary${search}`,
        ]}
      >
        <Routes>
          <Route
            path="/dashboard/develop/experiment/:experimentId/summary"
            element={<ExperimentSummaryView />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Run in CI on the routed experiment Summary", () => {
  it.each([false, true])(
    "shows the unclaimed door and scope line with all-on=%s",
    async (allOn) => {
      renderSummary({ claimed: false, allOn });
      expect(await screen.findByLabelText("Analysis baseline")).toHaveValue(
        baseline,
      );
      expect(screen.getByRole("heading", { name: "Summary" })).toBeVisible();
      expect(screen.getByRole("button", { name: "Run in CI" })).toBeVisible();
      expect(screen.getByTestId(`${surface}-scope-line`)).toHaveTextContent(
        "Copy a GitHub Actions step to comment on run regressions after your existing evaluation upload.",
      );
      expect(screen.queryByRole("dialog")).toBeNull();
    },
  );

  it.each([undefined, baseline, absent])(
    "opens the claimed door and copies canonical baseline data for selection %s",
    async (requested) => {
      const copy = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: { writeText: copy },
      });
      renderSummary({ requested });
      fireEvent.click(await screen.findByRole("button", { name: "Run in CI" }));
      const dialog = screen.getByRole("dialog", { name: "Run in CI" });
      expect(dialog).toBeVisible();
      expect(within(dialog).getByLabelText("Baseline run")).toHaveValue(
        baseline,
      );
      expect(
        Array.from(within(dialog).getByLabelText("Baseline run").options).map(
          (option) => [option.value, option.text],
        ),
      ).toEqual([
        ["", "Choose a baseline"],
        [candidate, "#2 Candidate"],
        [baseline, "#1 Baseline"],
      ]);
      expect(within(dialog).queryByLabelText("Candidate run")).toBeNull();
      expect(http.get).toHaveBeenCalledWith(
        `/datasets/${datasetId}/analysis/`,
        {
          params: {
            runs: `${candidate},${baseline}`,
            ...(requested === baseline ? { baseline } : {}),
            include: "root_causes",
          },
        },
      );
      fireEvent.click(
        within(dialog).getByRole("button", {
          name: "Copy GitHub Actions step",
        }),
      );
      await waitFor(() => expect(copy).toHaveBeenCalledTimes(1));
      expect(copy).toHaveBeenLastCalledWith(
        ciCommentStep({ experimentId, baselineRunId: baseline }),
      );
      expect(copy.mock.calls[0][0]).not.toContain(candidate);
      expect(copy.mock.calls[0][0]).toContain(
        "FI_RUN_ID: ${{ steps.fi_evaluation.outputs.run_id }}",
      );
      fireEvent.change(within(dialog).getByLabelText("Allowed regressions"), {
        target: { value: "2" },
      });
      fireEvent.click(
        within(dialog).getByRole("button", {
          name: "Copy GitHub Actions step",
        }),
      );
      await waitFor(() => expect(copy).toHaveBeenCalledTimes(2));
      expect(copy).toHaveBeenLastCalledWith(
        ciCommentStep({
          experimentId,
          baselineRunId: baseline,
          maxRegressed: 2,
        }),
      );
      fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    },
  );
});
