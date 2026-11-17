// The list's Success cell: a run with nothing to count shows no figure and
// says what it needs, even when the run carries a Success figure of its own;
// a try the server sends as a fail is counted, and no note names it; a try
// one of whose evaluations passed while another gave no result is missing a
// Pass/fail result.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import { EMPTY, fmtPct } from "src/sections/common/format/fmt";
import { SUCCESS_NEEDS_PASS_FAIL } from "src/sections/common/Runs/runNumbers";
import { SuccessRateCell } from "../LeaderboardSummaryCells";
import { leaderboardNumbers, successFrom } from "../leaderboardVersions";

// The tooltip's text is drawn next to what it wraps, so a test can read it.
vi.mock("src/components/tooltip", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ title, children }) =>
      createElement(
        "span",
        null,
        children,
        createElement("span", { "data-testid": "tip" }, title),
      ),
  };
});

const run = {
  run_id: "r1",
  label: "#1A",
  name: "r1",
  status: "Completed",
  listed: true,
  experiment_id: "exp-1",
  progress: { done: 3, total: 3 },
};
// One row per try: [the try's value on the matrix, the try's status, and
// the evaluations that passed when the matrix names them].
const numbersOf = (forRun, tries, passFailEvals) =>
  leaderboardNumbers(
    [forRun],
    {
      rows: tries.map(([value, status, passed], i) => ({
        row_id: `row-${i}`,
        cells: {
          r1: {
            trials: [value],
            trace_summary: [{ status, trial_number: 1, carried: false }],
            ...(passed ? { passed_evals: [passed] } : {}),
          },
        },
      })),
      columns: {
        r1: {
          trials: 1,
          status: "completed",
          trial_count_source: "immutable_plan",
          pass_fail_evals: passFailEvals,
        },
      },
    },
    { noun: "task" },
  );
const mount = (forRun, numbers) =>
  render(
    <SuccessRateCell
      node={{ group: false, data: forRun }}
      data={forRun}
      context={{
        numbers,
        baselineRun: null,
        baselineId: null,
        baselineLabel: null,
        showDeltas: false,
        allRuns: [],
      }}
    />,
  );

afterEach(cleanup);

describe("the list's Success for a run with nothing to count", () => {
  // The run carries a Success figure of its own (a served success_rate).
  const served = { ...run, success_rate: { passed: 3, total: 4 } };
  const tries = [
    [null, "completed"],
    ["error", "error"],
  ];

  it("gives no figure, never the run's own", () => {
    expect(successFrom(numbersOf(served, tries, 0), served)).toBeNull();
  });

  it("shows no figure in the cell and says what it needs", () => {
    const { container } = mount(served, numbersOf(served, tries, 0));
    expect(screen.getByTestId("tip").textContent).toBe(SUCCESS_NEEDS_PASS_FAIL);
    expect(container.textContent).toBe(`${EMPTY}${SUCCESS_NEEDS_PASS_FAIL}`);
  });

  it("still uses the run's own figure when the matrix does not say", () => {
    // Green at the base: with no count of Pass/fail evaluations and no
    // counted try, the served figure stays the fallback.
    const s = successFrom(
      numbersOf(served, [[null, "completed"]], undefined),
      served,
    );
    expect(s).toMatchObject({ passed: 3, total: 4 });
  });
});

describe("a try the server sends as a fail", () => {
  it("is counted as a fail and named in no note", () => {
    // Green at the base: the cell reads the matrix try.
    const numbers = numbersOf(
      run,
      [
        [true, "completed"],
        [false, "completed"],
      ],
      2,
    );
    const { container } = mount(run, numbers);
    expect(container.textContent).toContain(fmtPct(0.5));
    expect(container.textContent).not.toMatch(/no Pass\/fail result ·/);
    expect(successFrom(numbers, run)).toMatchObject({
      passed: 1,
      total: 2,
      note: null,
    });
  });
});

describe("a finished try that is not counted, in the cell's tip", () => {
  it("is missing a Pass/fail result when one evaluation passed and another gave none", () => {
    const numbers = numbersOf(
      run,
      [
        [true, "completed", ["judge", "check"]],
        [null, "completed", ["judge"]],
      ],
      2,
    );
    mount(run, numbers);
    expect(screen.getByTestId("tip")).toHaveTextContent(
      "1 try is missing a Pass/fail result · not counted",
    );
  });

  it("has no Pass/fail result when none of its evaluations gave one", () => {
    // Green at the base.
    const numbers = numbersOf(
      run,
      [
        [true, "completed", ["judge", "check"]],
        [null, "completed", []],
      ],
      2,
    );
    mount(run, numbers);
    expect(screen.getByTestId("tip")).toHaveTextContent(
      "1 try has no Pass/fail result · not counted",
    );
  });
});
