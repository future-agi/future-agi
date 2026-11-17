// W1 integration S3 review fixes on the screens' models and mounts (component
// proof while the cloud walks are OWED).
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import {
  ledgerLine,
  ledgerNounOf,
  ledgerOf,
  ledgerParts,
  mergeLedgers,
  runCountsText,
  runLedgerLine,
  spendSplitText,
} from "../runLedger";
import { errorCauseOf } from "../errorCause";
import { ResultsStatusLine } from "src/sections/experiment-detail/ExperimentData/ExperimentStatusBar";
import { launchedTitleOf } from "src/sections/develop-detail/Experiment/launchedTitle";
import { trialReducerOfValues } from "src/sections/develop-detail/Experiment/experimentFormValues";
import { dataIssues } from "src/sections/develop-detail/Experiment/stepIssues";
import { columnCatalog } from "src/sections/develop-detail/ExperimentTab/leaderboardColumns";
import { partialProgress } from "src/sections/develop-detail/ExperimentTab/leaderboardModel";
import {
  aggregateFor,
  ruleLabelOf,
} from "src/sections/experiment-detail/results/resultsColumns";
import { trialAggItems } from "src/sections/experiment-detail/results/groupMenu";
import {
  rateInterval,
  reduceRun,
} from "src/sections/common/Analysis/trialReducers";
import MetricHeader from "src/sections/common/Metrics/MetricHeader";

const K = "eval:correctness";
const rec = (row, trial, patch = {}) => ({
  example_id: `r${row}`,
  trial,
  visibility: "completed",
  status: "completed",
  scores: { [K]: { value: 1, passed: true } },
  ...patch,
});
const skippedCell = {
  value: null,
  status: "skipped",
  skip_reason: "no expected value",
};
const capped = ledgerOf({
  records: [
    rec(1, 1),
    rec(2, 1),
    rec(3, 1, { scores: { [K]: skippedCell } }),
    rec(4, 1, {
      visibility: "error",
      status: "error",
      error: "RateLimitError: 429",
      scores: {},
    }),
    rec(5, 1, {
      visibility: "error",
      status: "error",
      error: "lookup_invoice timed out after 30s",
      scores: {},
    }),
    rec(6, 1, { status: "infra", attempt_status: "stopped", scores: {} }),
  ],
  primaryKey: K,
  stopReason: "Stopped at cost cap",
});

describe("M2 · stopped by a limit vs left out", () => {
  it("only attempt_status stopped is a stop; an infra try is left out with its cause", () => {
    const l = ledgerOf({
      records: [
        rec(1, 1, {
          scores: { "eval:task_success": { value: 1, passed: true } },
        }),
        rec(1, 2, {
          status: "infra",
          trial_outcome: "infra",
          error: "Setup failed: image pull timed out",
          scores: {},
        }),
        rec(2, 1, {
          status: "infra",
          trial_outcome: "infra",
          error: "Setup failed: image pull timed out",
          scores: {},
        }),
      ],
      primaryKey: "eval:task_success",
    });
    expect(l.stopped_by).toBeNull();
    expect(l.rows.stopped).toBe(0);
    expect(l.rows.excluded).toEqual({ "setup failed": 1 });
    expect(l.tries.excluded).toBe(2);
    // S1: a Task success ledger says tasks without being told
    expect(ledgerNounOf(l)).toBe("task");
    expect(ledgerLine(l)).toBe(
      "1 task scored · 1 left out (setup failed) · 1 try left out (setup failed)",
    );
    expect(errorCauseOf({ error: "Setup failed: image pull timed out" })).toBe(
      "setup failed",
    );
  });
});

describe("M4 · one ledger on every screen", () => {
  it("(a) the launched drawer title prints the ledger; the old counts are the fallback", () => {
    expect(
      launchedTitleOf([{ done: 6, total: 6 }], { ledgers: [capped] }),
    ).toEqual({
      title: "Stopped at cost cap",
      helper:
        "2 rows scored · 1 skipped (no expected value) · 2 errored (RateLimitError 1, tool timeout · lookup_invoice 1) · 1 not run. Resume it or raise the cap.",
      stopped: true,
    });
    const done = ledgerOf({ records: [rec(1, 1), rec(2, 1)], primaryKey: K });
    expect(
      launchedTitleOf(
        [
          { done: 2, total: 2 },
          { done: 2, total: 2 },
        ],
        { ledgers: [done, done] },
      ).title,
    ).toBe("Results in · 2 runs · 4 rows scored");
    // a run without a ledger yet: the counts as before
    expect(
      launchedTitleOf([{ done: 2, total: 2 }], { ledgers: [null] }).title,
    ).toBe("Results in · 2/2");
    expect(mergeLedgers([done, done]).rows.scored).toBe(4);
  });

  it("(c, d) the status line prints the ledger parts, opens their rows, and drops its own error counts", () => {
    const onPart = vi.fn();
    render(
      <ResultsStatusLine
        total={6}
        errors={2}
        errorCauses={[{ cause: "RateLimitError", rows: 1 }]}
        onFilterCause={() => {}}
        ledgerParts={ledgerParts(capped)}
        ledgerScope="#3E"
        onLedgerPart={onPart}
      />,
    );
    // no "2 errors" of its own beside the ledger's "2 errored"
    expect(screen.queryByText("2 errors")).toBeNull();
    expect(screen.getByTestId("results-ledger-scope").textContent).toBe("#3E:");
    expect(screen.getByTestId("results-ledger").textContent).toContain(
      "2 rows scored",
    );
    fireEvent.click(screen.getByTestId("ledger-part-skipped"));
    expect(onPart.mock.calls[0][0]).toMatchObject({
      key: "skipped",
      ids: ["r3"],
      label: "Skipped · no expected value",
    });
    // each cause opens its own rows
    const causes = screen.getAllByTestId("error-cause-chip");
    expect(causes.map((c) => c.textContent)).toEqual([
      "RateLimitError 1",
      "tool timeout · lookup_invoice 1",
    ]);
    fireEvent.click(causes[0]);
    expect(onPart.mock.calls[1][0]).toMatchObject({
      key: "errored",
      cause: "RateLimitError",
      ids: ["r4"],
    });
    fireEvent.click(screen.getByTestId("ledger-part-stopped"));
    expect(onPart.mock.calls[2][0].ids).toEqual(["r6"]);
  });

  it("M5 · the status flaky count names its scope like the Matrix pill (any run)", () => {
    render(<ResultsStatusLine total={30} flaky={20} flakyScope="any run" />);
    expect(screen.getByTestId("status-flaky").textContent).toBe(
      "20 flaky on any run",
    );
  });
});

describe("M3 · one spend number, split", () => {
  it("tries + judge calls = the number the Summary cell prints", () => {
    expect(spendSplitText({ usd: 0.73, try_usd: 0.13, judge_usd: 0.6 })).toBe(
      "$0.73 spent: $0.13 tries + $0.60 judge calls",
    );
    expect(spendSplitText({ usd: 0.13, try_usd: 0.13, judge_usd: 0 })).toBe(
      "$0.13 spent on tries",
    );
    expect(spendSplitText(null)).toBeNull();
  });
});

describe("M6 · Most common of a score eval", () => {
  it("is the most common verdict at the bar, a share with a Wilson interval", () => {
    const rows = [
      [0.71, 0.83, 0.92],
      [0.71, 0.72, 0.9],
      [0.85, 0.9, 0.95],
      [0.5, 0.95, 0.4],
    ];
    const r = reduceRun(rows, {
      kind: "mode",
      passFail: false,
      threshold: 0.8,
    });
    // 2 of 3 pass · 1 of 3 · 3 of 3 · 1 of 3
    expect(r.value).toBe(2 / 4);
    expect(r.ci95).toEqual(rateInterval(0.5, 4));
    expect(
      trialAggItems({ kind: "eval" }, { output_type: "score", trials_k: 3 })[0]
        .label,
    ).toBe("Most common verdict");
  });
});

describe("M7 · so far from the ledger", () => {
  it("rows still running, queued or stopped are not in a playing run's numbers", () => {
    const run = {
      status: "ExperimentEvaluation",
      progress: { done: 238, total: 238 },
      ledger: {
        rows: { total: 238, running: 56, queued: 0, stopped: 0, carried: 182 },
      },
    };
    expect(partialProgress(run)).toEqual({ done: 182, total: 238 });
    expect(partialProgress({ ...run, ledger: undefined })).toBeNull();
  });
});

describe("S1, S6 · nouns and carried rows", () => {
  it("the tray's counts name tasks on a task suite and the run carried rows came from", () => {
    const l = ledgerOf({
      records: [rec(1, 1, { carried_from: "x" }), rec(2, 1)],
      primaryKey: "eval:task_success",
    });
    const run = { ledger: l, started_from: { run_id: "p", label: "#2A" } };
    const l2 = ledgerOf({
      records: [
        rec(1, 1, {
          carried_from: "x",
          scores: { "eval:task_success": { value: 1, passed: true } },
        }),
        rec(2, 1, {
          scores: { "eval:task_success": { value: 0, passed: false } },
        }),
      ],
      primaryKey: "eval:task_success",
    });
    const run2 = { ledger: l2, started_from: { run_id: "p", label: "#2A" } };
    expect(runCountsText(run2)).toBe("2 tasks scored · 1 carried from #2A");
    expect(runLedgerLine(run2)).toBe(runCountsText(run2));
    // without a parent label the old wording stays
    expect(ledgerLine(l2)).toBe("2 tasks scored · 1 carried");
    expect(runLedgerLine(run)).toMatch(/ carried from #2A$/);
  });
});

describe("S2 · the reducer sent is the one on screen", () => {
  it("At least n follows the tries down; no reducer when no run plays more than one try", () => {
    const v = { trials: 2, trialReducer: { kind: "at_least", n: 3 } };
    expect(trialReducerOfValues(v)).toEqual({ kind: "at_least", n: 2 });
    expect(
      trialReducerOfValues({ trials: 1, trialReducer: { kind: "pass_hat_k" } }),
    ).toBeNull();
  });
});

describe("S3 · the leaderboard column is named for what it shows", () => {
  it("pass^k for plain runs; From tries when a run sets its own rule", () => {
    const run = (extra = {}) => ({ run_id: "a", trials: 3, ...extra });
    const col = (runs) =>
      columnCatalog([], runs).find((c) => (c.key ?? c.id) === "pass_k");
    expect(col([run()]).label).toBe("pass^k");
    expect(col([run({ trial_reducer: { kind: "max" } })]).label).toBe(
      "From tries",
    );
  });
});

describe("S4, S5 · intervals and rules in the results header", () => {
  it("Average and Pass rate carry their 95% interval; mixed rules are named", () => {
    const entry = {
      avg: 0.64,
      pass_rate: 0.64,
      n: 238,
      value_ci95: [0.58, 0.7],
    };
    expect(
      aggregateFor({ output_type: "pass_fail" }, entry, "avg").ci95,
    ).toEqual([0.58, 0.7]);
    expect(
      aggregateFor({ output_type: "score", pass_threshold: 0.7 }, entry, "pass")
        .ci95,
    ).toEqual(rateInterval(0.64, 238));
    expect(ruleLabelOf("avg")).toBe("Mean");
    expect(ruleLabelOf("pass_hat_k", 3)).toBe("pass^3");
    render(
      <MetricHeader
        def={{ key: K, name: "Correctness", output_type: "pass_fail" }}
        aggregate={{ value: 0.25, agg: "pass_rate", reducer: "pass_hat_k" }}
        ruleLabel="pass^3"
      />,
    );
    expect(screen.getByTestId("metric-header-rule").textContent).toBe("pass^3");
  });
});

describe("S8 · a sample with no row count blocks Continue", () => {
  it("says why", () => {
    expect(dataIssues({ sample: { on: true, n: null, seed: 7 } })).toEqual([
      "Enter how many rows to sample, or untick A random sample",
    ]);
    expect(dataIssues({ sample: { on: true, n: 25, seed: 7 } })).toEqual([]);
    expect(
      dataIssues({ sample: { on: true, n: null }, rowIds: ["a"] }),
    ).toEqual([]);
  });
});
