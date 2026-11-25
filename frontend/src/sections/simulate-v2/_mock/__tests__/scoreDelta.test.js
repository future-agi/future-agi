import { describe, expect, it } from "vitest";
import {
  comparedWithText,
  isMeaningful,
  pointsDelta,
  readDelta,
  rowLabelOf,
  runLabelOf,
  signedPoints,
  toPoints,
} from "../scoreDelta";
import { cellFactsOf, subGoalCountOf } from "../compareRowFacts";

describe("absolute score deltas", () => {
  it("is points, never a percent of the old value", () => {
    expect(pointsDelta(98, 80)).toBe(18);
    expect(pointsDelta(58, 1)).toBe(57);
    expect(signedPoints(pointsDelta(58, 1))).toBe("+57");
    expect(signedPoints(pointsDelta(98, 80))).toBe("+18");
    expect(signedPoints(pointsDelta(70, 75))).toBe("−5");
  });
  it("works from a 0 baseline and says nothing without both numbers", () => {
    expect(pointsDelta(40, 0)).toBe(40);
    expect(pointsDelta(null, 50)).toBeNull();
    expect(readDelta(undefined, 50)).toBeNull();
  });
  it("reads direction, arrow and better/worse", () => {
    expect(readDelta(98, 80)).toMatchObject({
      pts: 18,
      arrow: "▲",
      text: "+18",
      better: true,
    });
    expect(readDelta(50, 80)).toMatchObject({
      pts: -30,
      arrow: "▼",
      better: false,
    });
    expect(readDelta(80, 80)).toMatchObject({
      arrow: "",
      text: "",
      better: null,
    });
    expect(readDelta(8, 12, { lowerIsBetter: true }).better).toBe(true);
  });
  it("turns a 0–1 share into whole points", () => {
    expect(toPoints(0.984)).toBe(98);
    expect(toPoints(null)).toBeNull();
  });
});

describe("labels", () => {
  const runs = [{ ordinal: 4 }, { ordinal: 5 }, { kind: "trial", trialN: 2 }];
  it("names the baseline and the rest by number", () => {
    expect(rowLabelOf(runs[0], 0)).toBe("Baseline");
    expect(rowLabelOf(runs[1], 1)).toBe("Run 5");
    expect(runLabelOf(runs[2])).toBe("Epoch · Candidate 2");
    expect(comparedWithText(runs)).toBe("Compared with Run 4 (baseline)");
  });
});

describe("cellFactsOf", () => {
  const evals = [{ id: "a" }];
  const mk = (score, passes) => ({
    measured: true,
    status: "passed",
    passes,
    task: { evalResults: [{ id: "a", score, passed: true }] },
  });
  it("reads each eval against the baseline in points", () => {
    const base = mk(0.8, 3);
    const f = cellFactsOf(mk(0.98, 2), base, evals);
    expect(f.evals[0].delta.text).toBe("+18");
    expect(f.passesDelta.text).toBe("−1");
  });
  it("gives the baseline no arrows", () => {
    const base = mk(0.8, 3);
    expect(cellFactsOf(base, base, evals).evals[0].delta).toBeNull();
  });
  it("counts sub-goals met", () => {
    expect(subGoalCountOf([{ status: "met" }, { status: "failed" }])).toEqual({
      met: 1,
      total: 2,
    });
    expect(subGoalCountOf([])).toBeNull();
  });
});

describe("noise threshold", () => {
  it("scores under 5 points are noise", () => {
    expect(isMeaningful(readDelta(97, 96))).toBe(false);
    expect(isMeaningful(readDelta(80, 98))).toBe(true);
    expect(isMeaningful(readDelta(62, 99))).toBe(true);
    expect(isMeaningful(readDelta(95, 90))).toBe(true);
    expect(isMeaningful(-4)).toBe(false);
    expect(isMeaningful(null)).toBe(false);
  });
  it("counts matter from 1", () => {
    expect(isMeaningful(readDelta(4, 5), "count")).toBe(true);
    expect(isMeaningful(0, "count")).toBe(false);
  });
});

describe("cellFactsOf noise and trial counts", () => {
  const evals = [{ id: "a" }];
  const mk = (score, passes, repeats) => ({
    measured: true,
    status: "passed",
    passes,
    repeats,
    task: { evalResults: [{ id: "a", score, passed: true }] },
  });
  it("marks a 1-point eval move as not meaningful", () => {
    const f = cellFactsOf(mk(0.97, 3, 3), mk(0.96, 3, 3), evals);
    expect(f.evals[0].delta.meaningful).toBe(false);
    expect(f.trialsNote).toBeNull();
  });
  it("drops the trials delta when counts differ", () => {
    const f = cellFactsOf(mk(0.9, 4, 5), mk(0.9, 3, 3), evals);
    expect(f.passesDelta).toBeNull();
    expect(f.trialsNote).toEqual({ n: 5, baseN: 3 });
  });
  it("keeps the trials delta when counts match", () => {
    const f = cellFactsOf(mk(0.9, 2, 3), mk(0.9, 3, 3), evals);
    expect(f.passesDelta.meaningful).toBe(true);
  });
});
