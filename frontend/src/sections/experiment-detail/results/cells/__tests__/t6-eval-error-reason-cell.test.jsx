// A native run whose code sandbox failed. The Data tab cell
// read `Not scored · the evaluation failed` and nothing said why. The cell's
// tooltip now adds one plain sentence for the stored reason's kind; the raw
// stored text (launcher log lines, `runChild():486`) never shows. A run
// recorded before the server kept a reason still says only that it failed.
/* eslint-disable react/prop-types */
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { ScoreCellRenderer } from "../MetricCells";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));

const RUN = "run-1";
const METRIC = "metric-code-eval";
const BASE_TIP =
  "Not scored: the evaluation failed. It is left out of the pass rate.";
const EXECUTOR_DOWN =
  "Code executor unavailable: the code-executor service could not be reached";
const LAUNCHER_RAW =
  "Code eval input validation failed: No output. Exit code: 255.\n" +
  "[E][2026-11-11T05:40:12+0000][1] runChild():486 Launching child process failed";
const context = {
  defsByKey: { [METRIC]: { key: METRIC, name: "Code eval" } },
  runsById: { [RUN]: { run_id: RUN, status: "Completed" } },
};
const failed = (explanation) => ({
  value: null,
  status: "not_assessed",
  n: 0,
  error_trials: 1,
  ...(explanation ? { explanation } : {}),
});
const renderCell = (score) =>
  render(
    <ScoreCellRenderer
      data={{
        example_id: "e1",
        expected: { value: null },
        results: {
          [RUN]: { status: "completed", scores: { [METRIC]: score } },
        },
      }}
      context={context}
      runId={RUN}
      metricKey={METRIC}
    />,
  );
const tipOf = () =>
  screen
    .getByText("Not scored · the evaluation failed")
    .getAttribute("aria-label");

describe("Score cell of a failed evaluation", () => {
  it("says in its tooltip that the sandbox could not be reached", () => {
    renderCell(failed(EXECUTOR_DOWN));
    expect(tipOf()).toBe(
      `${BASE_TIP}\nThe code sandbox could not be reached; ask your administrator.`,
    );
    expect(document.body.innerHTML).not.toContain("code-executor service");
  });

  it("never shows a raw multi-line launcher error", () => {
    renderCell(failed(LAUNCHER_RAW));
    expect(tipOf()).toBe(
      `${BASE_TIP}\nThe code sandbox could not start; ask your administrator.`,
    );
    expect(document.body.innerHTML).not.toContain("runChild()");
    expect(document.body.innerHTML).not.toContain("Exit code: 255");
  });

  it("keeps only the failed line for raw text of any other kind", () => {
    renderCell(failed('Traceback (most recent call last):\n  File "x.py"'));
    expect(tipOf()).toBe(BASE_TIP);
    expect(document.body.innerHTML).not.toContain("Traceback");
  });

  it("still says only that it failed when no reason is stored", () => {
    renderCell(failed(null));
    expect(tipOf()).toBe(BASE_TIP);
  });
});
