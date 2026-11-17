import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import RowRunDetail from "../RowRunDetail";
import {
  filterRunsByVerdict,
  runEvalVerdict,
  verdictSegments,
} from "../runsModel";

vi.mock("../RunLinks", () => ({ default: () => null }));
vi.mock("src/sections/common/Collab/RunCommentsSection", () => ({
  default: () => null,
}));
vi.mock("../EvalChip", () => ({
  default: ({ verdict }) => (
    <span data-testid="trial-verdict">{JSON.stringify(verdict)}</span>
  ),
}));

const metric = {
  key: "threshold",
  name: "Threshold",
  output_type: "score",
  higher_is_better: false,
  pass_threshold: 0.5,
};

afterEach(cleanup);

describe("row run metric direction", () => {
  it.each([
    [true, "pass"],
    [false, "fail"],
    [null, "neutral"],
  ])("uses the run's authoritative verdict %s", (passed, status) => {
    expect(
      runEvalVerdict(
        { value: 0.5, passed },
        { ...metric, higher_is_better: null },
      ),
    ).toEqual({ status, value: 0.5, display: "0.50" });
    expect(runEvalVerdict({ value: 0.5, passed }, metric, 2)).toEqual({
      status,
      value: 0.5,
      display: "0.50 · 2 trials",
    });
  });

  it("filters and segments mixed historical and declared pins by each run", () => {
    const shared = { ...metric, higher_is_better: null };
    const runs = [
      {
        run_id: "historical",
        evals: { threshold: { value: 0.9, passed: null } },
      },
      { run_id: "passing", evals: { threshold: { value: 0.5, passed: true } } },
      {
        run_id: "failing",
        evals: { threshold: { value: 0.9, passed: false } },
      },
    ];
    expect(verdictSegments(runs, shared)).toEqual([
      { runId: "passing", label: undefined, status: "pass" },
      { runId: "failing", label: undefined, status: "fail" },
    ]);
    expect(filterRunsByVerdict(runs, shared, "pass")).toEqual([runs[1]]);
    expect(filterRunsByVerdict(runs, shared, "fail")).toEqual([runs[2]]);
    expect(filterRunsByVerdict(runs, shared, "all")).toEqual(runs);
  });

  it("honors trial verdicts including unknown with a finite pass/fail threshold", () => {
    const run = {
      run_id: "run",
      evals: { threshold: { value: 0.5, passed: true } },
      trials_detail: [null, true, false].map((passed, index) => ({
        trial_number: index + 1,
        status: "completed",
        scores: { threshold: { status: "assessed", value: 0.5, passed } },
      })),
    };
    render(
      <RowRunDetail
        run={run}
        rowId="row"
        metrics={[
          { ...metric, output_type: "pass_fail", higher_is_better: true },
        ]}
      />,
    );
    expect(JSON.parse(screen.getByTestId("trial-verdict").textContent)).toEqual(
      { status: "neutral", value: 0.5, display: "0.50" },
    );
    fireEvent.click(screen.getByRole("button", { name: "Trial 2" }));
    expect(JSON.parse(screen.getByTestId("trial-verdict").textContent)).toEqual(
      { status: "pass", value: 0.5, display: "Pass" },
    );
    fireEvent.click(screen.getByRole("button", { name: "Trial 3" }));
    expect(JSON.parse(screen.getByTestId("trial-verdict").textContent)).toEqual(
      { status: "fail", value: 0.5, display: "Fail" },
    );
  });

  it("does not turn missing or skipped scores into a verdict", () => {
    expect(runEvalVerdict({ value: null, passed: true }, metric)).toEqual({
      status: "empty",
      value: null,
      display: "Not scored",
    });
    expect(
      runEvalVerdict({ status: "skipped", value: 1, passed: true }, metric),
    ).toEqual({
      status: "empty",
      value: null,
      display: "Skipped · not scored",
    });
  });

  it.each([
    [false, 0.1, "pass", "0.10"],
    [false, 0.5, "pass", "0.50"],
    [false, 0.9, "fail", "0.90"],
    [true, 0.1, "fail", "0.10"],
    [true, 0.5, "pass", "0.50"],
    [true, 0.9, "pass", "0.90"],
  ])("uses direction %s at score %s", (higher, value, status, display) => {
    expect(
      runEvalVerdict({ value }, { ...metric, higher_is_better: higher }),
    ).toEqual({ status, value, display });
  });

  it.each([undefined, null, "false", 0])(
    "leaves undeclared numeric direction %s neutral",
    (higher) => {
      const unknown = { ...metric, higher_is_better: higher };
      expect(runEvalVerdict({ value: 0.5 }, unknown)).toEqual({
        status: "neutral",
        value: 0.5,
        display: "0.50",
      });
      expect(
        verdictSegments(
          [{ run_id: "run", evals: { threshold: { value: 0.5 } } }],
          unknown,
        ),
      ).toEqual([]);
    },
  );

  it.each([
    [0, 1, "fail", "Fail"],
    [1, 1, "pass", "Pass"],
    [0.5, 1, "neutral", "0.50"],
    [2 / 3, 3, "neutral", "0.67 · 3 trials"],
  ])(
    "keeps binary outcomes distinct from means (%s)",
    (value, trials, status, display) => {
      expect(
        runEvalVerdict(
          { value },
          { output_type: "pass_fail", pass_threshold: null },
          trials,
        ),
      ).toEqual({ status, value, display });
    },
  );

  it.each([
    [false, "pass", "fail"],
    [undefined, "neutral", "neutral"],
  ])(
    "renders selected trials with declared direction %s",
    (higher, first, second) => {
      const run = {
        run_id: "run",
        trials_detail: [0.5, 0.9].map((value, index) => ({
          trial_number: index + 1,
          status: "completed",
          scores: { threshold: { status: "assessed", value } },
        })),
      };
      render(
        <RowRunDetail
          run={run}
          rowId="row"
          metrics={[{ ...metric, higher_is_better: higher }]}
        />,
      );
      expect(
        JSON.parse(screen.getByTestId("trial-verdict").textContent),
      ).toEqual({ status: first, value: 0.5, display: "0.50" });
      fireEvent.click(screen.getByRole("button", { name: "Trial 2" }));
      expect(
        JSON.parse(screen.getByTestId("trial-verdict").textContent),
      ).toEqual({ status: second, value: 0.9, display: "0.90" });
    },
  );
});
