/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
} from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import SelectionBar from "../SelectionBar";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", () => ({ default: api }));
vi.mock("src/sections/common/ux/oneview/ActionBar", () => ({
  default: ({ actions }) => (
    <div>
      {actions.map((action) => (
        <button key={action.id} disabled={action.disabled} onClick={action.run}>
          {action.label}
        </button>
      ))}
    </div>
  ),
}));
vi.mock("src/api/develop/run-results", () => ({
  useRerunResultCells: () => ({ request: vi.fn() }),
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({ can: { run: false, edit: false, download: true } }),
}));
vi.mock("src/sections/common/Runs/useExternalRunHandoff", () => ({
  useExternalRunHandoff: () => ({ dialog: null }),
}));
vi.mock("src/sections/common/Runs/useRescore", () => ({
  useRescore: () => ({ dialog: null }),
}));
vi.mock("src/sections/common/Runs/rescoreCopyModel", () => ({
  scoreWithChoices: () => [],
}));
vi.mock("src/sections/common/Selection/useSelectAllMatching", () => ({
  useSelectAllMatching: ({ selectedIds }) => ({ ids: selectedIds }),
}));
vi.mock("src/sections/common/Selection/SelectionCount", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Selection/askFalconAboutRows", () => ({
  askFalconAboutRows: vi.fn(),
}));
vi.mock("src/sections/common/Collab/AssignDialog", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Review/StartReviewDialog", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/export/ExportDialog", () => ({
  ResultsExportDialog: () => null,
}));
vi.mock("../AddResultsToDatasetDialog", () => ({ default: () => null }));
vi.mock("../resultsCollabActions", () => ({ resultsCollabActions: () => [] }));
vi.mock("../../iterate/iterateModel", () => ({ canIterate: () => false }));
vi.mock("src/sections/common/RunSources/rerunBus", () => ({
  openRerunWithChanges: vi.fn(),
}));

const A = "00000000-0000-4000-8000-000000000001";
const B = "00000000-0000-4000-8000-000000000002";
const VERSION = "00000000-0000-4000-8000-000000000003";
const ROOT = "/model-hub/experiments/v2/experiment/pairwise/";
const savedJob = {
  id: "editor-job",
  judge_version: VERSION,
  run_a: A,
  run_b: B,
  status: "completed",
  counts: { completed: 1 },
  total: 1,
};
const summary = {
  human: { a_wins: 1, b_wins: 1, ties: 0, rows_reviewed: 1 },
  judge: { a_wins: 1, b_wins: 0, ties: 0, rows_reviewed: 1 },
  agreement: { matches: 1, compared: 2, rate: 0.5 },
};

function mount({ readOnly = true } = {}) {
  return renderWithRouter(
    <SelectionBar
      datasetId="dataset"
      selectedRows={[{ example_id: "example" }]}
      runs={[
        { run_id: A, task_kind: "prompt", status: "completed" },
        { run_id: B, task_kind: "prompt", status: "completed" },
      ]}
      baselineId={A}
      readOnly={readOnly}
      onClear={vi.fn()}
    />,
    { route: "/dashboard/experiment/experiment/results" },
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  setLaunchOverrideForTest({ launch: true, madeReal: ["exp.pairwiseJudge"] });
});
afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
  setLaunchOverrideForTest(null);
});

it.each(["completed", "running"])(
  "lets a viewer open a saved %s judgment through selection without writing",
  async (status) => {
    const saved = { ...savedJob, status };
    const detail = {
      ...saved,
      next_offset: null,
      items: [
        {
          example_id: "example",
          trial_number: 1,
          status: "completed",
          winner: "a",
          reason: "More complete saved reasoning",
          presentation_order: "ba",
        },
      ],
    };
    api.get.mockImplementation(async (path) => ({
      data:
        path === `${ROOT}judges/`
          ? { jobs: [saved], next_offset: null }
          : path === `${ROOT}summary/`
            ? summary
            : detail,
    }));
    mount();
    const start = screen.getByRole("button", { name: "Start judge job…" });
    expect(start).toBeDisabled();
    fireEvent.click(start);
    expect(api.get.mock.calls).toEqual([]);
    const open = screen.getByRole("button", { name: "Open saved judgments…" });
    expect(open).toBeEnabled();
    fireEvent.click(open);
    const savedButton = await screen.findByRole("button", {
      name: `Open judge ${VERSION} · ${status}`,
    });
    expect(
      screen.getByRole("button", { name: "Start judge job" }),
    ).toBeDisabled();
    expect(screen.getByLabelText("Judge version ID")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Start judge job" }));
    expect(api.post.mock.calls).toEqual([]);
    fireEvent.click(savedButton);
    expect(
      await screen.findByText("More complete saved reasoning"),
    ).toBeInTheDocument();
    expect(screen.getByText("Human: A 1 · B 1 · Tie 0")).toBeInTheDocument();
    expect(screen.getByText("Judge: A 1 · B 0 · Tie 0")).toBeInTheDocument();
    expect(screen.getByText("Agreement: 50% (1/2 votes)")).toBeInTheDocument();
    expect(
      screen.getByText("1/1 completed · 0 failed · 0 cancelled"),
    ).toBeInTheDocument();
    expect(api.get.mock.calls).toEqual([
      [
        `${ROOT}judges/`,
        { params: { run_a: A, run_b: B, offset: 0, limit: 20 } },
      ],
      [`${ROOT}judges/editor-job/`, { params: { offset: 0, limit: 50 } }],
      [
        `${ROOT}summary/`,
        { params: { run_a: A, run_b: B, judge_job: "editor-job" } },
      ],
    ]);
    if (status === "running") {
      const cancel = screen.getByRole("button", { name: "Cancel judge" });
      expect(cancel).toBeDisabled();
      fireEvent.click(cancel);
    }
    expect(api.post.mock.calls).toEqual([]);
  },
);

it.each([false, true])(
  "shows judge entry points and a scope line without a claim for read-only=%s",
  (readOnly) => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: [],
      showUnreleased: true,
    });
    mount({ readOnly });
    expect(
      screen.getByTestId("exp.pairwiseJudge-scope-line"),
    ).toHaveTextContent(
      "Compare completed runs on the same dataset version with a saved judge model; normal evaluator usage applies.",
    );
    expect(
      screen.getByRole("button", { name: "Open saved judgments…" }),
    ).toBeEnabled();
    const start = screen.getByRole("button", { name: "Start judge job…" });
    if (readOnly) expect(start).toBeDisabled();
    else expect(start).toBeEnabled();
    expect(api.get.mock.calls).toEqual([]);
    expect(api.post.mock.calls).toEqual([]);
  },
);

it("shows an empty saved list to a viewer without offering a writable fallback", async () => {
  api.get.mockResolvedValue({ data: { jobs: [], next_offset: null } });
  mount();
  fireEvent.click(
    screen.getByRole("button", { name: "Open saved judgments…" }),
  );
  expect(
    await screen.findByText("No saved judgments for this pair."),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Start judge job" }),
  ).toBeDisabled();
  expect(api.get.mock.calls).toEqual([
    [
      `${ROOT}judges/`,
      { params: { run_a: A, run_b: B, offset: 0, limit: 20 } },
    ],
  ]);
  expect(api.post.mock.calls).toEqual([]);
});
