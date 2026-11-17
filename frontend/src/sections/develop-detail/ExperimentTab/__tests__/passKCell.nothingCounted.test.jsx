// The list's pass^k cell: a run with nothing to count (no evaluation with a
// Pass/fail result) shows no figure there and says what it needs, by pass^k
// or by the run's own reducer, as its Success cell does. Its errored rows
// still enable "Rerun failed rows".
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "src/utils/test-utils";
import { EMPTY } from "src/sections/common/format/fmt";
import { SUCCESS_NEEDS_PASS_FAIL } from "src/sections/common/Runs/runNumbers";
import { PassKCell } from "../R4Cells";
import { hasFailingRows } from "../RunRowMenu";
import { leaderboardNumbers } from "../leaderboardVersions";

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
  trials: 2,
  progress: { done: 2, total: 2, errors: 0 },
};
// Two rows of two tries; every try errored.
const errored = () => ({
  trials: ["error", "error"],
  trace_summary: [1, 2].map((n) => ({
    status: "error",
    trial_number: n,
    carried: false,
  })),
});
const numbersOf = (forRun, passFailEvals) =>
  leaderboardNumbers(
    [forRun],
    {
      rows: [0, 1].map((i) => ({
        row_id: `row-${i}`,
        cells: { r1: errored() },
      })),
      columns: {
        r1: {
          trials: 2,
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
    <PassKCell
      node={{ group: false, data: forRun }}
      data={forRun}
      context={{ numbers }}
    />,
  );

afterEach(cleanup);

describe("the list's pass^k for a run with nothing to count", () => {
  it("shows no figure and says what it needs", () => {
    const { container } = mount(run, numbersOf(run, 0));
    expect(container.textContent).toBe(`${EMPTY}${SUCCESS_NEEDS_PASS_FAIL}`);
  });

  it("shows no figure by the run's own reducer either", () => {
    const own = { ...run, trial_reducer: { kind: "pass_hat_k" } };
    const { container } = mount(own, numbersOf(own, 0));
    expect(container.textContent).toBe(`${EMPTY}${SUCCESS_NEEDS_PASS_FAIL}`);
  });

  it("keeps Rerun failed rows for its errored rows", () => {
    // Green at the base: the menu reads the numbers object, which keeps the
    // rows' pass^k.
    const numbers = numbersOf(run, 0);
    expect(hasFailingRows(run, numbers.byRun.r1)).toBe(true);
  });
});

describe("the list's pass^k when the matrix does not say", () => {
  it("keeps the figure", () => {
    // Green at the base: with no count of Pass/fail evaluations the errored
    // tries are in the figure, as before.
    const { container } = mount(run, numbersOf(run, undefined));
    expect(container.textContent).toBe("0% (0/2)");
  });
});
