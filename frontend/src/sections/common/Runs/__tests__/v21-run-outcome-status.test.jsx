// the capability contract (ship-blocker, prototype dist v2.1): a run's status chip,
// pill, tray entry and experiment status come from its REAL outcome. A run
// the backend (or the mock) stamps `Completed` while every row it played
// errored reads Failed in red; one with errored rows reads Finished with
// errors (orange); neither ever reads the word Completed or shows green.
import React from "react";
import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import { STATUS_STYLES, getRunStatus } from "../runStatus";
import RunStatusChip from "../RunStatusChip";
import RunChip from "../RunChip";
import { experimentStatusOf } from "src/sections/experiment-detail/DevelopExperimentSelect";
import { finishToastText } from "src/sections/develop-detail/Experiment/runTrayStore";

const GREEN = STATUS_STYLES.Completed.color;

// Every row of #2A errored (a bad provider key), yet the run says Completed.
const FAILED_RUN = {
  run_id: "RUN_2A",
  label: "#2A",
  experiment_id: "EXP_2",
  status: "Completed",
  progress: { done: 5, total: 5, errors: 5 },
  first_error: "AuthenticationError: invalid provider key",
};
const PARTIAL_RUN = {
  run_id: "RUN_2B",
  label: "#2B",
  experiment_id: "EXP_2",
  status: "Completed",
  progress: { done: 5, total: 5, errors: 2 },
};
const OK_RUN = {
  run_id: "RUN_2C",
  label: "#2C",
  experiment_id: "EXP_2",
  status: "Completed",
  progress: { done: 5, total: 5, errors: 0 },
};

describe("run status from the real outcome", () => {
  it("a run whose every row errored is Failed and never green", () => {
    const st = getRunStatus(FAILED_RUN);
    expect(st.key).toBe("Failed");
    expect(st.label).toBe("Failed");
    expect(st.color).not.toBe(GREEN);
    expect(st.terminal).toBe(true);
  });

  it("a run with some errored rows is Finished with errors, never the word Completed", () => {
    const st = getRunStatus(PARTIAL_RUN);
    expect(st.key).toBe("PartialCompleted");
    expect(st.label).toBe("Finished with errors");
    expect(st.label).not.toMatch(/Completed/);
    expect(st.color).not.toBe(GREEN);
    expect(st.icon).toBe("/assets/icons/ic_warning.svg");
  });

  it("the status chip of a partial run never says Completed", () => {
    render(<RunStatusChip run={PARTIAL_RUN} showProgress />);
    expect(screen.getByText("Finished with errors")).toBeInTheDocument();
    expect(screen.queryByText(/Completed/)).toBeNull();
  });

  it("a run with no progress counts reads its ledger's errored rows", () => {
    const st = getRunStatus({
      status: "Completed",
      ledger: { rows: { total: 4, errored: { RateLimitError: 4 } } },
    });
    expect(st.key).toBe("Failed");
  });

  it("the backend's Error and PartialRun words never read grey or green", () => {
    expect(getRunStatus({ status: "Error" }).key).toBe("Failed");
    expect(getRunStatus("PartialRun").key).toBe("PartialCompleted");
  });

  it("stopped and clean runs keep their own status", () => {
    expect(
      getRunStatus({
        status: "Cancelled",
        progress: { done: 2, total: 5, errors: 2 },
      }).key,
    ).toBe("Cancelled");
    expect(getRunStatus(OK_RUN).key).toBe("Completed");
  });

  it("the status chip of a failed run says Failed, never Completed", () => {
    render(<RunStatusChip run={FAILED_RUN} showProgress />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.queryByText("Completed")).toBeNull();
  });

  it("the run pill of a failed run says Failed", () => {
    render(<RunChip run={FAILED_RUN} />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("the experiment pill is the worst run's outcome", () => {
    expect(experimentStatusOf([OK_RUN, FAILED_RUN]).key).toBe("Failed");
    expect(experimentStatusOf([OK_RUN, PARTIAL_RUN]).key).toBe(
      "PartialCompleted",
    );
  });

  it("the tray's finish line names the failure", () => {
    const text = finishToastText(FAILED_RUN, getRunStatus(FAILED_RUN).label);
    expect(text).toContain("Failed");
    expect(text).not.toMatch(/\bCompleted\b/);
  });
});
