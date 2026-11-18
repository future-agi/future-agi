import { describe, it, expect } from "vitest";
import { baselineCallFor, callDiff, delta } from "../compareDiff";

const call = (id, fields = {}) => ({
  id,
  trialIndex: 1,
  status: "passed",
  latencyMs: 400,
  tokens: 2000,
  turns: 10,
  csat: 8,
  durationMs: 60000,
  subGoalResults: [
    { name: "a", passed: true },
    { name: "b", passed: true },
  ],
  evalResults: [{ id: "tone", score: 0.9 }],
  ...fields,
});

describe("baselineCallFor", () => {
  const base = [call("b1", { trialIndex: 1 }), call("b2", { trialIndex: 2 })];

  it("takes the baseline's call for the same trial", () => {
    expect(baselineCallFor(call("x", { trialIndex: 2 }), base).id).toBe("b2");
  });

  it("falls back to the baseline's first call", () => {
    expect(baselineCallFor(call("x", { trialIndex: 3 }), base).id).toBe("b1");
    expect(baselineCallFor(call("x", { trialIndex: null }), base).id).toBe(
      "b1",
    );
  });

  it("has none when the baseline did not run the scenario", () => {
    expect(baselineCallFor(call("x"), [])).toBeNull();
  });
});

describe("delta", () => {
  it("calls lower better for latency, duration, cost, tokens and turns", () => {
    ["latency", "duration", "cost", "tokens", "turns"].forEach((m) =>
      expect(delta(m, 5, 10)).toMatchObject({ up: false, better: true }),
    );
  });

  it("calls higher better for the rest", () => {
    expect(delta("csat", 9, 8)).toMatchObject({ up: true, better: true });
    expect(delta("tone", 50, 80)).toMatchObject({ up: false, better: false });
  });

  it("has nothing to say when a side is missing or nothing moved", () => {
    expect(delta("tokens", null, 10)).toBeNull();
    expect(delta("tokens", 10, 10)).toBeNull();
  });
});

describe("callDiff", () => {
  it("names every change against the baseline call", () => {
    const base = call("base", {
      status: "failed",
      subGoalResults: [
        { name: "a", passed: true },
        { name: "b", passed: false },
      ],
      evalResults: [{ id: "tone", score: 0.6 }],
    });
    const diff = callDiff(call("now", { latencyMs: 350 }), base, ["tone"]);
    expect(diff.was).toBe("Failed");
    expect(diff.subGoals).toMatchObject({ up: true, better: true, text: "1" });
    expect(diff.measures.latency).toEqual({
      up: false,
      better: true,
      text: "50ms",
    });
    expect(diff.measures.tokens).toBeNull();
    expect(diff.evals.tone).toEqual({ up: true, better: true, text: "30 pts" });
  });

  it("draws no arrow for a change too small for the cells to show", () => {
    const base = call("base", {
      latencyMs: 812.3,
      stopLatencyMs: 300.2,
      tokens: 2000.4,
      durationMs: 12340,
      csat: 8.04,
    });
    const diff = callDiff(
      call("now", {
        latencyMs: 812.1,
        stopLatencyMs: 300.4,
        tokens: 1999.8,
        durationMs: 12310,
        csat: 7.96,
      }),
      base,
    );
    Object.entries(diff.measures).forEach(([measure, change]) =>
      expect(change, measure).toBeNull(),
    );
    expect(
      callDiff(call("now", { durationMs: 12460 }), base).measures.duration,
    ).toMatchObject({ up: true, text: "0.2s" });
  });

  it("is nothing for the baseline call itself or with no baseline", () => {
    const c = call("same");
    expect(callDiff(c, c)).toBeNull();
    expect(callDiff(c, null)).toBeNull();
  });
});
