// The Matrix, grouped: a run with nothing to count (no evaluation with a
// Pass/fail result) shows no figure on a group row, as its run header does:
// `—`, with what Success needs as the hint. Its errored tries read "error"
// on their dots, as the header's note counts them.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import { SUCCESS_NEEDS_PASS_FAIL } from "src/sections/common/Runs/runNumbers";
import { buildMatrixModel } from "../matrixModel";
import MatrixCell, { trialTooltip } from "../MatrixCell";
import gridSource from "../MatrixGrid.jsx?raw";

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

const NONE = "run-none";
const PASSES = "run-passes";

// Two tries per row. NONE has no Pass/fail evaluation and errored on every
// try; PASSES passed every try.
const tries = (value, status) => ({
  trials: [value, value],
  trace_summary: [1, 2].map((n) => ({
    status,
    trial_number: n,
    carried: false,
  })),
  failed_evals: [[], []],
});
const column = (label, passFailEvals) => ({
  label,
  trials: 2,
  status: "completed",
  trial_count_source: "immutable_plan",
  pass_fail_evals: passFailEvals,
});
const MATRIX = {
  rows: [1, 2].map((n) => ({
    row_id: `row-${n}`,
    n,
    label: `T${n}`,
    group_values: { category: "refund" },
    cells: {
      [NONE]: tries("error", "error"),
      [PASSES]: tries(true, "completed"),
    },
  })),
  columns: { [NONE]: column("#1", 0), [PASSES]: column("#2", 1) },
};
const grouped = () =>
  buildMatrixModel(MATRIX, { runIds: [NONE, PASSES], group: "category" })
    .groups[0];

afterEach(cleanup);

describe("a group row of a run with nothing to count", () => {
  it("has no Success figure for it and says what it needs", () => {
    expect(grouped().rates[NONE]).toMatchObject({
      rate: null,
      n: 0,
      lowSample: false,
      tip: SUCCESS_NEEDS_PASS_FAIL,
    });
  });

  it("names no figure for it in the group's line", () => {
    expect(grouped().header).toBe("refund · 2 rows · — · 100%");
  });

  it("takes the group's hint as the figure's hover", () => {
    expect(gridSource).toMatch(/r\?\.tip\s*\?\?/);
  });

  it("leaves a run with something counted as it was", () => {
    // Green at the base.
    expect(grouped().rates[PASSES]).toMatchObject({
      rate: 1,
      pass: 4,
      n: 4,
      lowSample: true,
    });
    expect(grouped().rates[PASSES].tip ?? null).toBeNull();
  });

  it("keeps today's figure when the matrix does not say", () => {
    // Green at the base: with no count of Pass/fail evaluations the errored
    // tries are in the figure, as before.
    const unknown = {
      ...MATRIX,
      columns: {
        [NONE]: { ...column("#1", 0), pass_fail_evals: undefined },
        [PASSES]: column("#2", 1),
      },
    };
    const g = buildMatrixModel(unknown, {
      runIds: [NONE, PASSES],
      group: "category",
    }).groups[0];
    expect(g.rates[NONE]).toMatchObject({ rate: 0, n: 4 });
  });
});

describe("an errored try of a run with nothing to count", () => {
  const cell = tries("error", "error");

  it("reads 'error' on its dot", () => {
    expect(trialTooltip(cell, 0, { noPassFail: true })).toBe("Trial 1 · error");
  });

  it("still reads as a fail where the run has a figure", () => {
    // Green at the base.
    expect(trialTooltip(cell, 0)).toBe("Trial 1 · error · counted as a fail");
  });

  it("reads 'error' on every dot of the cell", () => {
    render(
      <MatrixCell cell={cell} runLabel="#1" rowLabel="T1" noPassFail dense />,
    );
    const dots = screen
      .getAllByTestId("tip")
      .map((t) => t.textContent)
      .filter((t) => t.startsWith("Trial "));
    expect(dots).toEqual(["Trial 1 · error", "Trial 2 · error"]);
  });

  it("is told so by the grid from its run's column", () => {
    expect(gridSource).toMatch(
      /noPassFail=\{\s*model\.columnsById\[id\]\?\.noPassFail\s*\}/,
    );
  });
});
