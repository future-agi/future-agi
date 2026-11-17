// The list's Success cell counts a finished try by its Pass/fail
// evaluations: a run with nothing to count shows no figure and says what it
// needs, and a run with a finished try that has no Pass/fail result says so
// on hover. The cell's rule line and the column's description say which
// tries count.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "src/utils/test-utils";
import { EMPTY, fmtPct } from "src/sections/common/format/fmt";
import { SuccessRateCell } from "../LeaderboardSummaryCells";
import { leaderboardNumbers } from "../leaderboardVersions";
import { columnCatalog } from "../leaderboardColumns";

const NEEDS = "Success needs an evaluation with a Pass/fail result.";
const RULE =
  "Success rate = passed tries ÷ counted tries (pass@1). A try counts when it has a Pass/fail result or it errored; errored tries count as fails. Tries that didn't run or have no Pass/fail result are not counted.";
const COLUMN =
  "Success, first try (pass@1): passed tries ÷ counted tries. Errored tries count as fails; tries that didn't run or have no Pass/fail result are not counted.";

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
  created_at: "2026-10-01T00:00:00Z",
  progress: { done: 3, total: 3 },
};
// One row per try: [the try's value on the matrix, the try's status].
const numbersOf = (tries, passFailEvals) =>
  leaderboardNumbers(
    [run],
    {
      rows: tries.map(([value, status], i) => ({
        row_id: `row-${i}`,
        cells: {
          r1: {
            trials: [value],
            trace_summary: [{ status, trial_number: 1, carried: false }],
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
const mount = (numbers) =>
  render(
    <SuccessRateCell
      node={{ group: false, data: run }}
      data={run}
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

describe("the list's Success cell", () => {
  it("shows no figure for a run with nothing to count, and says why", () => {
    const numbers = numbersOf(
      [
        [null, "completed"],
        [null, "completed"],
        ["error", "error"],
      ],
      0,
    );
    const { container } = mount(numbers);
    const tip = screen.getByTestId("tip");
    expect(tip.textContent).toBe(NEEDS);
    expect(container.textContent).toBe(`${EMPTY}${NEEDS}`);
  });

  it("names a finished try with no Pass/fail result in its tip", () => {
    const numbers = numbersOf(
      [
        [true, "completed"],
        [false, "completed"],
        [null, "completed"],
      ],
      1,
    );
    const { container } = mount(numbers);
    expect(container.textContent).toContain(fmtPct(0.5));
    const tip = screen.getByTestId("tip");
    expect(
      within(tip).getByText("1 try has no Pass/fail result · not counted"),
    ).toBeTruthy();
  });

  it("says which tries count in its rule line", () => {
    const numbers = numbersOf(
      [
        [true, "completed"],
        [false, "completed"],
      ],
      1,
    );
    mount(numbers);
    const tip = screen.getByTestId("tip");
    expect(within(tip).getByText(RULE)).toBeTruthy();
  });
});

describe("the list's Success column", () => {
  it("says the same rule in its description", () => {
    const col = columnCatalog([], [run]).find((c) => c.id === "success_rate");
    expect(col.description).toBe(COLUMN);
  });
});
