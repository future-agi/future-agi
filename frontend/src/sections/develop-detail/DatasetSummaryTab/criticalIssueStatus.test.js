import { describe, expect, it } from "vitest";
import {
  CriticalIssueStatus,
  isCriticalIssueFailed,
  isCriticalIssueInProgress,
  isCriticalIssueNotAnalysed,
  isCriticalIssueOutOfDate,
} from "./criticalIssueStatus";

const { PENDING, RUNNING, COMPLETED, FAILED, NOT_REQUESTED } =
  CriticalIssueStatus;

describe("a simulation run's critical issues", () => {
  it("wait, and spin the Refresh icon, only while a summary a person asked for is running", () => {
    expect(isCriticalIssueInProgress("simulate", RUNNING)).toBe(true);
    for (const status of [
      NOT_REQUESTED,
      PENDING,
      COMPLETED,
      FAILED,
      undefined,
    ]) {
      expect(isCriticalIssueInProgress("simulate", status)).toBe(false);
    }
  });

  it("say the run is not analysed yet when nobody has asked and nothing is stored", () => {
    expect(isCriticalIssueNotAnalysed("simulate", NOT_REQUESTED, null)).toBe(
      true,
    );
    expect(isCriticalIssueNotAnalysed("simulate", PENDING, null)).toBe(true);
    expect(isCriticalIssueNotAnalysed("simulate", PENDING, undefined)).toBe(
      true,
    );
  });

  it("show a stored summary, and any other state, as before", () => {
    expect(isCriticalIssueNotAnalysed("simulate", PENDING, { e1: [] })).toBe(
      false,
    );
    for (const status of [RUNNING, COMPLETED, FAILED]) {
      expect(isCriticalIssueNotAnalysed("simulate", status, null)).toBe(false);
    }
  });

  it("mark stored issues out of date when the run's verdicts changed after they were found", () => {
    expect(
      isCriticalIssueOutOfDate("simulate", NOT_REQUESTED, { e1: [] }),
    ).toBe(true);
    expect(isCriticalIssueOutOfDate("simulate", PENDING, { e1: [] })).toBe(
      true,
    );
    expect(isCriticalIssueOutOfDate("simulate", COMPLETED, { e1: [] })).toBe(
      false,
    );
    expect(isCriticalIssueOutOfDate("simulate", PENDING, null)).toBe(false);
  });

  it("say so when the last analysis failed", () => {
    expect(isCriticalIssueFailed("simulate", FAILED)).toBe(true);
    for (const status of [PENDING, RUNNING, COMPLETED, undefined]) {
      expect(isCriticalIssueFailed("simulate", status)).toBe(false);
    }
  });
});

describe("a dataset's critical issues", () => {
  it("still wait on pending and running", () => {
    expect(isCriticalIssueInProgress("develop", PENDING)).toBe(true);
    expect(isCriticalIssueInProgress("develop", RUNNING)).toBe(true);
    expect(isCriticalIssueInProgress("develop", COMPLETED)).toBe(false);
  });

  it("have none of the simulation run's states", () => {
    expect(isCriticalIssueNotAnalysed("develop", PENDING, null)).toBe(false);
    expect(isCriticalIssueOutOfDate("develop", PENDING, { e1: [] })).toBe(
      false,
    );
    expect(isCriticalIssueFailed("develop", FAILED)).toBe(false);
  });
});
