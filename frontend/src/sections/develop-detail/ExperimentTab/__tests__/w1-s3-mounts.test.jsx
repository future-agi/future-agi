// W1 integration S3: the screens that print the one ledger, the reducer
// intervals and a re-run cell's History render what the models say
// (component proof while the cloud walks are OWED).
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import EvalHoverCard from "../EvalHoverCard";
import { ResultsStatusLine } from "src/sections/experiment-detail/ExperimentData/ExperimentStatusBar";
import MetricHeader from "src/sections/common/Metrics/MetricHeader";
import CellHistory from "src/sections/experiment-detail/results/drawer/CellHistory";
import { wilson } from "src/sections/common/Analysis/runStats";

vi.mock("src/sections/common/Analysis/workbench/JudgeTrust", () => ({
  TrustLine: () => null,
  useJudgeTrust: () => null,
}));

const passFail = {
  key: "eval:task_success",
  name: "Task success",
  output_type: "pass_fail",
  higher_is_better: true,
  trials_k: 3,
};

describe("W1 S3 · mounts", () => {
  it("the results status line prints the ledger line and names the flaky count's run", () => {
    render(
      <ResultsStatusLine
        total={30}
        flaky={6}
        flakyScope="#3E"
        ledger="28 tasks scored · 2 errored (RateLimitError)"
      />,
    );
    expect(screen.getByTestId("results-ledger").textContent).toBe(
      "28 tasks scored · 2 errored (RateLimitError)",
    );
    expect(screen.getByTestId("status-flaky").textContent).toBe(
      "6 flaky on #3E",
    );
  });

  it("the metric header shows a reducer's value with its 95% interval", () => {
    render(
      <MetricHeader
        def={passFail}
        aggregate={{
          value: 0.25,
          agg: "pass_rate",
          ci95: [0.07, 0.7],
          reducer: "pass_hat_k",
        }}
      />,
    );
    expect(screen.getByTestId("metric-header-ci").textContent).toMatch(
      /^\(7(\.0)?%–70(\.0)?%\)$/,
    );
  });

  it("the leaderboard hover card shows the pass rate's Wilson interval", () => {
    const run = {
      run_id: "r-b",
      label: "#2B",
      metrics: {
        aggregates: { [passFail.key]: { avg: 0.64, std: 0.48, n: 238 } },
      },
    };
    render(<EvalHoverCard def={passFail} run={run} datasetId="d" />);
    const w = wilson(0.64, 238);
    const text = screen.getByText(/^95% interval /).textContent;
    expect(text).toContain(`${(w.low * 100).toFixed(1)}%`);
  });

  it("a re-run cell offers History with each result, the current one last", () => {
    const trial = {
      trial: 1,
      output: "new reply",
      status: "completed",
      scores: { "eval:correctness": { value: 1, display: "Pass" } },
      amended_at: "2026-10-23T17:42:00Z",
      amended_by: { name: "Maya Chen" },
      history: [
        {
          output: "old reply",
          status: "completed",
          scores: { "eval:correctness": { value: 0, display: "Fail" } },
          at: "2026-10-22T10:00:00Z",
          by: { name: "Asha" },
        },
      ],
    };
    render(<CellHistory trial={trial} />);
    const btn = screen.getByTestId("cell-history");
    expect(btn.textContent).toBe("History · 2 results");
    fireEvent.click(btn);
    const list = screen.getByTestId("cell-history-list").textContent;
    expect(list).toContain("Result 2 (now)");
    expect(list).toContain("by Maya Chen");
    expect(list).toContain("new reply");
    expect(list).toContain("Result 1");
    expect(list).toContain("old reply");
    expect(list.indexOf("Result 2 (now)")).toBeLessThan(
      list.indexOf("Result 1"),
    );
  });

  it("a cell never re-run has no History", () => {
    const { container } = render(<CellHistory trial={{ trial: 1 }} />);
    expect(container.querySelector('[data-testid="cell-history"]')).toBeNull();
  });
});

describe("W1 S3 · the waiting panel's ledger line", () => {
  it("prints the run's ledger line from the polled experiment detail", async () => {
    vi.resetModules();
    vi.doMock("src/api/develop/runs", () => ({
      useExperimentDetail: () => ({
        data: {
          runs: [
            {
              run_id: "r1",
              ledger: {
                rows: {
                  total: 20,
                  done: 12,
                  running: 0,
                  queued: 8,
                  scored: 12,
                  ran: 0,
                  unscored: 0,
                  na: 0,
                  skipped: {},
                  errored: {},
                  stopped: 0,
                  carried: 0,
                },
                tries: {},
                stopped_by: null,
              },
            },
          ],
        },
      }),
    }));
    const { default: RunLedgerLine } = await import(
      "src/sections/develop-detail/Experiment/RunLedgerLine"
    );
    render(<RunLedgerLine experimentId="e1" runId="r1" noun="task" />);
    expect(screen.getByTestId("waiting-ledger").textContent).toBe(
      "12 tasks scored · 8 queued",
    );
  });
});
