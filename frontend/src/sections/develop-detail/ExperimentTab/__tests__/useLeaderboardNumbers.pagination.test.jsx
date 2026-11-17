import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import axios from "src/utils/axios";
import LeaderboardAnswer from "../LeaderboardAnswer";
import { leaderboardNumbers, successFrom } from "../leaderboardVersions";
import useLeaderboardNumbers from "../useLeaderboardNumbers";

vi.mock("src/sections/common/Datasets/datasetKind", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetKind: () => ({ kind: "default" }),
}));

const runs = [
  { run_id: "base", label: "#1A" },
  { run_id: "candidate", label: "#2B" },
].map((run) => ({
  ...run,
  status: "Completed",
  listed: true,
  comparison_group: "same-plan",
  dataset_version: { id: "snapshot", number: 1 },
  trials: 1,
  progress: { done: 501, total: 501 },
  success_rate: { passed: 501, total: 501 },
}));
const view = { datasetId: "dataset", allRuns: runs, baselineRun: runs[0] };
const READ_FAILED = "Could not read this run's results. Try again.";
const PARTIAL = "More tasks than one read holds, so not totalled here";

// The first page is identical for both runs. Only the final row regresses.
function matrixPage(page, regression = true) {
  const start = page * 500;
  const rows = Array.from({ length: page === 0 ? 500 : 1 }, (_, i) => {
    const n = start + i + 1;
    return {
      row_id: `row-${n}`,
      label: `Row ${n}`,
      values: {},
      source: null,
      cells: Object.fromEntries(
        runs.map((run) => [
          run.run_id,
          {
            trials: [!(regression && run.run_id === "candidate" && n === 501)],
            trial_numbers: [1],
            cost_usd: [0.01],
            wall_ms: [1000],
            trace_summary: [{ status: "completed" }],
          },
        ]),
      ),
    };
  });
  return {
    data: {
      status: true,
      result: {
        rows,
        columns: Object.fromEntries(
          runs.map((run) => [
            run.run_id,
            {
              trials: 1,
              status: "completed",
              trial_count_source: "immutable_plan",
              pass_fail_evals: 1,
            },
          ]),
        ),
        total: 501,
        scope: { complete: true, selection_id: "same-selection" },
        pagination: {
          page,
          page_size: 500,
          has_more: page === 0,
          next: page === 0 ? 1 : null,
        },
      },
    },
  };
}

let client;
let get;
let latestNumbers;
// eslint-disable-next-line react/prop-types -- test stub
function Answer({ numbers }) {
  return (
    <LeaderboardAnswer
      runs={runs}
      allRuns={runs}
      numbers={numbers}
      baselineRun={runs[0]}
      datasetId={view.datasetId}
      datasetKind="default"
    />
  );
}
function LiveAnswer() {
  latestNumbers = useLeaderboardNumbers(view);
  return <Answer numbers={latestNumbers} />;
}
const show = (node = <LiveAnswer />) =>
  render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
const leaderboardCalls = () =>
  get.mock.calls
    .filter(([, config]) => config.params.baseline === "none")
    .map(([, config]) => config);

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  latestNumbers = null;
  get = vi
    .spyOn(axios, "get")
    .mockImplementation((url, { params }) =>
      Promise.resolve(matrixPage(params.page)),
    );
});
afterEach(() => {
  cleanup();
  client.clear();
  vi.restoreAllMocks();
});

describe("leaderboard result pagination", () => {
  it("includes the last page in totals and the live recommendation", async () => {
    show();
    await waitFor(() => expect(latestNumbers.matrix?.rows.length).toBe(501));
    expect(leaderboardCalls().map(({ params }) => params.page)).toEqual([0, 1]);
    expect(leaderboardCalls().map(({ params }) => params.page_size)).toEqual([
      500, 500,
    ]);
    expect(
      leaderboardCalls().map(({ signal }) => signal instanceof AbortSignal),
    ).toEqual([true, true]);
    expect(leaderboardCalls()[1].signal).toBe(leaderboardCalls()[0].signal);
    const success = successFrom(latestNumbers, runs[1]);
    expect([success.passed, success.total]).toEqual([500, 501]);
    expect(latestNumbers.best.id).toBe("base");
    expect(screen.getByTestId("leaderboard-ship").textContent).toBe(
      "Don't ship #2B yet: it fails Row 501, which #1A passes. Open Row 501",
    );
    expect(screen.getByTestId("leaderboard-ship-row").textContent).toBe(
      "Open Row 501",
    );
  });

  it("does not publish the first page while the continuation is pending", async () => {
    let resolvePage;
    const continuation = new Promise((resolve) => {
      resolvePage = resolve;
    });
    get.mockImplementation((url, { params }) =>
      params.baseline === "none" && params.page === 1
        ? continuation
        : Promise.resolve(matrixPage(params.page)),
    );
    show();
    await waitFor(() =>
      expect(leaderboardCalls().map(({ params }) => params.page)).toEqual([
        0, 1,
      ]),
    );
    expect(latestNumbers.matrix).toBe(null);
    expect(latestNumbers.best).toBe(null);
    expect(screen.queryByTestId("leaderboard-ship")).toBe(null);
    await act(async () => resolvePage(matrixPage(1)));
    await waitFor(() => expect(latestNumbers.matrix?.rows.length).toBe(501));
    expect(screen.getByTestId("leaderboard-ship-row").textContent).toBe(
      "Open Row 501",
    );
  });

  it("shows a read failure instead of a recommendation after continuation fails", async () => {
    get.mockImplementation((url, { params }) =>
      params.baseline === "none" && params.page === 1
        ? Promise.reject(new Error("Unavailable"))
        : Promise.resolve(matrixPage(params.page)),
    );
    show();
    await waitFor(() => expect(latestNumbers.matrixError).toBe(true));
    expect(latestNumbers.matrix).toBe(null);
    expect(latestNumbers.best).toBe(null);
    expect(successFrom(latestNumbers, runs[1])).toBe(null);
    expect(screen.getByText(READ_FAILED).textContent).toBe(READ_FAILED);
    expect(screen.queryByTestId("leaderboard-ship")).toBe(null);
    expect(screen.getByTestId("leaderboard-change-runs").disabled).toBe(false);
  });

  it.each([
    ["Run to compare", "#2B", "Run to compare: #3C #3C"],
    ["Compare with", "#1A", "Compare with: #3C #3C"],
  ])(
    "recovers from an unread selection in %s without remounting",
    async (side, readableLabel, unreadSelection) => {
      const readableRuns = runs.map((run) => ({
        ...run,
        progress: { done: 3, total: 3 },
        success_rate: { passed: 3, total: 3 },
      }));
      const unread = {
        ...readableRuns[1],
        run_id: "unread",
        label: "#3C",
        comparison_group: "unread-plan",
      };
      const recoveryView = {
        ...view,
        allRuns: [...readableRuns, unread],
        baselineRun: readableRuns[0],
      };
      const response = matrixPage(0, false);
      response.data.result.rows = response.data.result.rows.slice(0, 3);
      response.data.result.total = 3;
      response.data.result.pagination.has_more = false;
      response.data.result.pagination.next = null;
      get.mockImplementation((url, { params }) =>
        params.runs.split(",").includes("unread")
          ? Promise.reject(new Error("Unavailable"))
          : Promise.resolve(response),
      );
      function RecoveryAnswer() {
        latestNumbers = useLeaderboardNumbers(recoveryView);
        return (
          <LeaderboardAnswer
            runs={recoveryView.allRuns}
            allRuns={recoveryView.allRuns}
            numbers={latestNumbers}
            baselineRun={recoveryView.baselineRun}
            datasetId={view.datasetId}
            datasetKind="default"
          />
        );
      }

      // One render: switch to the unread run and back using the real pickers.
      show(<RecoveryAnswer />);
      await waitFor(() =>
        expect(screen.getByTestId("leaderboard-ship").textContent).toBe(
          "No row got worse: review it, then ship.",
        ),
      );
      expect(latestNumbers.byRun.unread).toEqual({ unread: READ_FAILED });
      fireEvent.click(screen.getByTestId("leaderboard-more"));
      fireEvent.click(screen.getByTestId("leaderboard-change-runs"));
      fireEvent.click(
        screen.getByRole("button", { name: new RegExp(`^${side}:`) }),
      );
      fireEvent.click(screen.getByRole("button", { name: /^#3C\b/ }));

      expect(screen.getByText(READ_FAILED).textContent).toBe(READ_FAILED);
      expect(screen.queryByTestId("leaderboard-ship")).toBe(null);
      expect(screen.queryByTestId("verdict-line")).toBe(null);
      expect(screen.getByTestId("leaderboard-change-runs").disabled).toBe(
        false,
      );
      fireEvent.click(screen.getByTestId("leaderboard-change-runs"));
      fireEvent.click(screen.getByRole("button", { name: unreadSelection }));
      fireEvent.click(
        screen.getByRole("button", {
          name: new RegExp(`^${readableLabel}\\b`),
        }),
      );

      await waitFor(() =>
        expect(screen.getByTestId("leaderboard-ship").textContent).toBe(
          "No row got worse: review it, then ship.",
        ),
      );
      expect(screen.queryByText(READ_FAILED)).toBe(null);
      expect(latestNumbers.byRun.unread).toEqual({ unread: READ_FAILED });
      fireEvent.click(screen.getByTestId("leaderboard-change-runs"));
      expect(
        screen
          .getAllByTestId("compare-pair-picker")
          .map((picker) => picker.getAttribute("aria-label")),
      ).toEqual([
        "Run to compare: #2B #2B",
        "Compare with: #1A #1A (baseline)",
      ]);
    },
  );

  it("withdraws a cached recommendation when the refreshed continuation fails", async () => {
    get.mockImplementation((url, { params }) =>
      Promise.resolve(matrixPage(params.page, false)),
    );
    show();
    await waitFor(() =>
      expect(screen.getByTestId("leaderboard-ship").textContent).toBe(
        "No row got worse: review it, then ship.",
      ),
    );
    get.mockImplementation((url, { params }) =>
      params.baseline === "none" && params.page === 1
        ? Promise.reject(new Error("Unavailable"))
        : Promise.resolve(matrixPage(params.page, false)),
    );
    await act(async () => {
      await client.invalidateQueries({
        queryKey: ["develop", "r4-leaderboard-matrix"],
      });
    });
    await waitFor(() => expect(latestNumbers.matrixError).toBe(true));
    expect(successFrom(latestNumbers, runs[1])).toBe(null);
    expect(latestNumbers.best).toBe(null);
    expect(screen.getByText(READ_FAILED).textContent).toBe(READ_FAILED);
    expect(screen.queryByTestId("leaderboard-ship")).toBe(null);
  });

  it("labels a cached partial population instead of recommending it", () => {
    const matrix = matrixPage(0).data.result;
    const numbers = {
      ...leaderboardNumbers(runs, matrix, {
        noun: "row",
        partialRunIds: ["base", "candidate"],
      }),
      matrix,
      noun: "row",
    };
    show(<Answer numbers={numbers} />);
    expect(screen.getByText(PARTIAL).textContent).toBe(PARTIAL);
    expect(screen.queryByTestId("leaderboard-ship")).toBe(null);
    expect(screen.getByTestId("leaderboard-change-runs").disabled).toBe(false);
  });
});
