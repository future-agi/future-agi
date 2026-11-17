// Test a fix's "first these rows, then the rest" scope for a run with nothing
// to count (no evaluation with a Pass/fail result): its first rows are the
// errored rows and no verdict comes, so the option and the saved label name
// them as errored rows. The rows and their order are unchanged, and a run
// with a figure reads as before.
import { describe, expect, it } from "vitest";
import * as model from "../iterateModel";
import dockSource from "../IterateDock.jsx?raw";

const NONE = "run-none";
const COUNTED = "run-counted";

// Three rows of two tries. NONE errored on every try of rows 1 and 2 and
// has no result on row 3; COUNTED failed one try of row 1.
const matrixOf = (columns) => ({
  rows: [1, 2, 3].map((n) => ({
    row_id: `row-${n}`,
    n,
    label: `T${n}`,
    cells: {
      [NONE]: { trials: n < 3 ? ["error", "error"] : [null, null] },
      [COUNTED]: { trials: n === 1 ? [1, 0] : [1, 1] },
    },
  })),
  columns,
});
const COUNTS = {
  [NONE]: { trials: 2, pass_fail_evals: 0 },
  [COUNTED]: { trials: 2, pass_fail_evals: 1 },
};
const thenOf = (runId, columns = COUNTS) => {
  const sets = model.scopeSets(matrixOf(columns), runId, {});
  return {
    sets,
    option: model
      .scopeOptions(sets, { parentLabel: "#1" })
      .find((o) => o.type === "then"),
  };
};

describe("the then scope of a run with nothing to count", () => {
  it("names its first rows as errored rows", () => {
    expect(thenOf(NONE).option).toMatchObject({
      label: "Errored rows first, then the other 1 (3)",
      tip: "Runs the fix on the 2 errored rows, then reruns the other 1 from that run.",
    });
  });

  it("keeps its rows and their order", () => {
    // Green at the base: the same rows, errored rows first.
    const { sets, option } = thenOf(NONE);
    expect(model.thenSplit(sets)).toEqual({
      first: ["row-1", "row-2"],
      rest: ["row-3"],
    });
    expect(option.count).toBe(3);
  });

  it("saves the chained label with the same words", () => {
    expect(
      model.chainedScopeLabel?.({
        scopeType: "failed",
        first: 2,
        rest: 1,
        noPassFail: true,
      }),
    ).toBe("Errored rows first: 2, then the other 1");
  });

  it("is what the dock saves", () => {
    // Reads the source: a wiring pin.
    expect(dockSource).toMatch(
      /chainedScopeLabel\(\{[^}]*noPassFail: Boolean\(dock\.sets\?\.noPassFail\)/,
    );
  });
});

describe("the then scope of a run with a figure", () => {
  it("reads as before", () => {
    // Green at the base.
    expect(thenOf(COUNTED).option).toMatchObject({
      label: "Failing rows first, then the other 2 (3)",
      tip: "Runs the fix on the 1 failing row, then reruns the other 2 from that run — one verdict for every row",
      count: 3,
    });
  });

  it("reads as before when the matrix does not say", () => {
    // Green at the base: with no count of Pass/fail evaluations the errored
    // rows are its failing rows, as before.
    expect(thenOf(NONE, {}).option.label).toBe(
      "Failing rows first, then the other 1 (3)",
    );
  });

  it("saves the chained label as before", () => {
    // Red at the base only because the helper is new.
    expect(
      model.chainedScopeLabel?.({ scopeType: "failed", first: 2, rest: 1 }),
    ).toBe("Failing rows first: 2, then the other 1");
    expect(
      model.chainedScopeLabel?.({ scopeType: "rows", first: 2, rest: 1 }),
    ).toBe("These rows first: 2, then the other 1");
  });
});
