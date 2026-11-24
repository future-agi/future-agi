import { describe, expect, it } from "vitest";
import {
  CriticalIssueStatus,
  isCriticalIssueInProgress,
  isCriticalIssueNotAnalysed,
  isCriticalIssueOutOfDate,
} from "./criticalIssueStatus";

const { PENDING, RUNNING, COMPLETED, FAILED } = CriticalIssueStatus;

describe("a simulation run's critical issues", () => {
  it("wait only while a summary a person asked for is running", () => {
    expect(isCriticalIssueInProgress("simulate", RUNNING)).toBe(true);
    for (const status of [PENDING, COMPLETED, FAILED, undefined]) {
      expect(isCriticalIssueInProgress("simulate", status)).toBe(false);
    }
  });

  it("say the run is not analysed when nobody has asked and nothing is stored", () => {
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
});

describe("a simulation run's stored critical issues", () => {
  it("are out of date when the run's verdicts changed after they were found", () => {
    expect(isCriticalIssueOutOfDate("simulate", PENDING, { e1: [] })).toBe(
      true,
    );
  });

  it("are current once a person's refresh finished, and absent ones are not out of date", () => {
    expect(isCriticalIssueOutOfDate("simulate", COMPLETED, { e1: [] })).toBe(
      false,
    );
    expect(isCriticalIssueOutOfDate("simulate", RUNNING, { e1: [] })).toBe(
      false,
    );
    expect(isCriticalIssueOutOfDate("simulate", PENDING, null)).toBe(false);
    expect(isCriticalIssueOutOfDate("simulate", PENDING, undefined)).toBe(
      false,
    );
  });

  it("are never out of date on a dataset", () => {
    expect(isCriticalIssueOutOfDate("develop", PENDING, { e1: [] })).toBe(
      false,
    );
  });
});

describe("a dataset's critical issues", () => {
  it("still wait on pending and running", () => {
    expect(isCriticalIssueInProgress("develop", PENDING)).toBe(true);
    expect(isCriticalIssueInProgress("develop", RUNNING)).toBe(true);
    expect(isCriticalIssueInProgress("develop", COMPLETED)).toBe(false);
  });

  it("never say not analysed", () => {
    expect(isCriticalIssueNotAnalysed("develop", PENDING, null)).toBe(false);
  });
});
