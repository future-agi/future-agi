import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import {
  setLaunchOverrideForTest,
  surfaceShownIn,
} from "src/launch/launchSwitch";
import PairwiseJudgeDialog from "../PairwiseJudgeDialog";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", () => ({ default: api }));

const A = "00000000-0000-4000-8000-000000000001";
const B = "00000000-0000-4000-8000-000000000002";
const VERSION = "00000000-0000-4000-8000-000000000003";
const ROOT = "/model-hub/experiments/v2/experiment/pairwise/";
const job = {
  id: "job",
  run_a: A,
  run_b: B,
  status: "completed",
  counts: { completed: 1 },
  total: 1,
  next_offset: null,
  items: [
    {
      example_id: "example",
      trial_number: 1,
      status: "completed",
      winner: "a",
      reason: "More complete",
      presentation_order: "ba",
    },
  ],
};
const summary = {
  human: { a_wins: 1, b_wins: 0, ties: 0, rows_reviewed: 1 },
  judge: { a_wins: 1, b_wins: 0, ties: 0, rows_reviewed: 1 },
  agreement: { matches: 1, compared: 1, rate: 1 },
};

beforeEach(() => {
  vi.clearAllMocks();
  setLaunchOverrideForTest({ launch: true, madeReal: ["exp.pairwiseJudge"] });
  api.post.mockResolvedValue({ data: job });
  api.get.mockImplementation(async (path) => ({
    data: path.endsWith("judges/")
      ? { jobs: [], next_offset: null }
      : path.endsWith("summary/")
        ? summary
        : job,
  }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  setLaunchOverrideForTest(null);
});

function mount(props = {}) {
  return render(
    <PairwiseJudgeDialog
      open
      experimentId="experiment"
      runA={A}
      runB={B}
      onClose={vi.fn()}
      {...props}
    />,
  );
}

it("opens the judge dialog without a launch claim", async () => {
  setLaunchOverrideForTest({
    launch: true,
    madeReal: [],
    showUnreleased: true,
  });
  expect(
    surfaceShownIn("exp.pairwiseJudge", {
      launch: true,
      experimentsBasic: true,
      madeReal: new Set(),
    }),
  ).toBe(true);
  mount();
  expect(screen.getByRole("dialog")).toBeVisible();
  expect(
    await screen.findByText("No saved judgments for this pair."),
  ).toBeVisible();
  expect(api.get).toHaveBeenCalledWith(`${ROOT}judges/`, {
    params: { run_a: A, run_b: B, offset: 0, limit: 20 },
  });
  expect(api.post).not.toHaveBeenCalled();
});

it("starts the pinned pair and shows separate counts and reasoning", async () => {
  mount();
  fireEvent.change(screen.getByLabelText("Judge version ID"), {
    target: { value: VERSION },
  });
  fireEvent.click(screen.getByRole("button", { name: "Start judge job" }));
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith(`${ROOT}judges/`, {
      run_a: A,
      run_b: B,
      judge_version: VERSION,
    }),
  );
  expect(await screen.findByText("More complete")).toBeInTheDocument();
  expect(screen.getByText("Human: A 1 · B 0 · Tie 0")).toBeInTheDocument();
  expect(screen.getByText("Judge: A 1 · B 0 · Tie 0")).toBeInTheDocument();
  expect(screen.getByText("Agreement: 100% (1/1 votes)")).toBeInTheDocument();
  expect(screen.getByText(/Presented BA/)).toBeInTheDocument();
});

it("keeps a refused start editable without exposing server internals", async () => {
  api.post.mockRejectedValue({ statusCode: 500, result: "private traceback" });
  mount();
  fireEvent.change(screen.getByLabelText("Judge version ID"), {
    target: { value: VERSION },
  });
  fireEvent.click(screen.getByRole("button", { name: "Start judge job" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Could not update the judge job. Try again.",
  );
  expect(screen.queryByText(/private traceback/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Start judge job" })).toBeEnabled();
});

it("cancels a pending job and keeps the saved results visible", async () => {
  let current = { ...job, status: "running" };
  api.post.mockImplementation(async (path) => {
    if (path.endsWith("cancel/")) current = { ...job, status: "cancelled" };
    return { data: current };
  });
  api.get.mockImplementation(async (path) => ({
    data: path.endsWith("judges/")
      ? { jobs: [], next_offset: null }
      : path.endsWith("summary/")
        ? summary
        : current,
  }));
  mount();
  fireEvent.change(screen.getByLabelText("Judge version ID"), {
    target: { value: VERSION },
  });
  fireEvent.click(screen.getByRole("button", { name: "Start judge job" }));
  fireEvent.click(await screen.findByRole("button", { name: "Cancel judge" }));
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith(`${ROOT}judges/job/cancel/`, {}),
  );
  expect(await screen.findByText("Status: cancelled")).toBeInTheDocument();
});

it("refreshes a cancelled job until its admitted verdict arrives", async () => {
  vi.useFakeTimers();
  let current = {
    ...job,
    status: "running",
    counts: { running: 1 },
    finished_at: null,
    items: [{ ...job.items[0], status: "running", winner: "", reason: "" }],
  };
  api.post.mockImplementation(async (path) => {
    if (path.endsWith("cancel/")) current = { ...current, status: "cancelled" };
    return { data: current };
  });
  api.get.mockImplementation(async (path) => ({
    data: path.endsWith("judges/")
      ? { jobs: [], next_offset: null }
      : path.endsWith("summary/")
        ? summary
        : current,
  }));
  mount();
  fireEvent.change(screen.getByLabelText("Judge version ID"), {
    target: { value: VERSION },
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Start judge job" }));
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Cancel judge" }));
  });
  expect(screen.getByText("Status: cancelled")).toBeInTheDocument();
  expect(screen.getByText("example · Trial 1 · running")).toBeInTheDocument();
  expect(screen.queryByText("More complete")).not.toBeInTheDocument();
  const reads = api.get.mock.calls.length;
  current = { ...job, status: "cancelled", finished_at: "finished" };
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(api.get).toHaveBeenCalledTimes(reads + 2);
  expect(screen.getByText("Status: cancelled")).toBeInTheDocument();
  expect(screen.getByText("example · Trial 1 · A")).toBeInTheDocument();
  expect(screen.getByText("More complete")).toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(9000);
  });
  expect(api.get).toHaveBeenCalledTimes(reads + 2);
});

it("does not admit writes for a read-only viewer", async () => {
  mount({ readOnly: true });
  await screen.findByText("No saved judgments for this pair.");
  expect(
    screen.getByRole("button", { name: "Start judge job" }),
  ).toBeDisabled();
  expect(api.post).not.toHaveBeenCalled();
});
