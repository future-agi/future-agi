// Rerun with changes for a run with nothing to count (no evaluation with a
// Pass/fail result): the failed scope keeps its rows and its tries, and its
// label reads "—" where the number was. A run with a figure is unchanged.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "src/utils/test-utils";
import { useMatrix } from "src/api/develop/r2";
import * as dialog from "../RerunWithChangesDialog";
import { failedScopeLabel } from "../failedScopeLabel";
import dialogSource from "../RerunWithChangesDialog.jsx?raw";
import RerunWithChangesOpen from "../RerunWithChangesOpen";

const seen = vi.hoisted(() => ({ props: null, prototype: true }));

vi.mock("src/utils/prototype", async (original) => ({
  ...(await original()),
  isPrototypeMode: () => seen.prototype,
}));

vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useMatrix: vi.fn(),
}));
vi.mock(
  "src/sections/experiment-detail/ExperimentBarRightSection/rerunPlan",
  () => ({ rerunPlan: () => ({ perRun: [] }) }),
);
vi.mock("../RerunWithChangesDialog", async (importOriginal) => ({
  ...(await importOriginal()),
  default: (props) => {
    seen.props = props;
    return null;
  },
}));

const NONE = "run-none";
const COUNTED = "run-counted";
const matrixOf = (passFailEvals) => ({
  rows: [1, 2, 3].map((n) => ({
    row_id: `row-${n}`,
    n,
    label: `T${n}`,
    cells: {
      [NONE]: { trials: n < 3 ? ["error", "error"] : [null, null] },
      [COUNTED]: { trials: n === 1 ? [1, 0] : [1, 1] },
    },
  })),
  columns: {
    [NONE]: { trials: 2, pass_fail_evals: passFailEvals },
    [COUNTED]: { trials: 2, pass_fail_evals: 1 },
  },
});
const runOf = (id) => ({
  run_id: id,
  label: "#1A",
  experiment_id: "exp-1",
  experiment_ordinal: 1,
  trials: 2,
});
const open = (id, passFailEvals = 0) => {
  useMatrix.mockReturnValue({ data: matrixOf(passFailEvals) });
  render(
    <RerunWithChangesOpen
      run={runOf(id)}
      datasetId="d1"
      datasetKind="task_suite"
      siblingsOf={[]}
      request={{ scope: "failed" }}
      close={vi.fn()}
    />,
  );
  return seen.props;
};

beforeEach(() => {
  seen.props = null;
  seen.prototype = true;
  useMatrix.mockClear();
});
afterEach(cleanup);

describe("Rerun with changes for a run with nothing to count", () => {
  it("keeps its failed rows and tells the dialog the run has no figure", () => {
    expect(open(NONE)).toMatchObject({ failedRows: 2, noPassFail: true });
  });

  it("labels the failed scope with — where the number was", () => {
    expect(failedScopeLabel(2, "task", { noPassFail: true })).toBe(
      "Failed tasks (—)",
    );
    expect(failedScopeLabel(2, "row", { noPassFail: true })).toBe(
      "Failed rows (—)",
    );
  });

  it("puts that label on the failed scope", () => {
    // Reads the source: a wiring pin.
    expect(dialogSource).toMatch(
      /failedScopeLabel\(failedRows, rowNoun, \{ noPassFail \}\)/,
    );
  });

  it("still makes the same tries", () => {
    // Green at the base: the tries a rerun of the failed scope makes.
    expect(
      dialog.rerunTries({ scope: "failed", failedRows: 2, tries: 2 }),
    ).toBe(4);
  });
});

it("opens a real rerun without requesting the prototype matrix", () => {
  seen.prototype = false;
  const props = open(COUNTED);
  expect(useMatrix).not.toHaveBeenCalled();
  expect(props.run).toMatchObject({ run_id: COUNTED, dataset_id: "d1" });
  expect(props.initialScope).toBe("failed");
  expect(props.initialRowIds).toEqual([]);
});

describe("Rerun with changes for a run with a figure", () => {
  it("keeps its count", () => {
    // Green at the base.
    const props = open(COUNTED);
    expect(props.failedRows).toBe(1);
    expect(props.noPassFail ?? false).toBe(false);
  });

  it("labels the failed scope with its number, as before", () => {
    // Red at the base only because the helper is new.
    expect(failedScopeLabel(1, "task")).toBe("Failed tasks (1)");
    expect(failedScopeLabel(undefined, "row")).toBe("Failed rows");
  });
});
