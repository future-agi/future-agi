// IT-R3 G2 P1: the headline answers like for like and says ship or not.
import { describe, expect, it } from "vitest";
import { coverageOf, headlineRank, shipLine } from "../leaderboardHeadline";
import { defaultPair, waitingNote } from "../LeaderboardAnswer";

const run = (label, rate, extra = {}) => ({
  run_id: `id-${label}`,
  label,
  experiment_id: `e${label[1]}`,
  experiment_ordinal: Number(label[1]),
  status: "Completed",
  dataset_version: { number: Number(label[1]) },
  progress: {
    done: label[1] === "1" ? 24 : 30,
    total: label[1] === "1" ? 24 : 30,
  },
  success_rate: { passed: Math.round(rate * 90), total: 90 },
  metrics: { cost_usd: 1 },
  ...extra,
});
const B = run("#2B", 0.656);
const D = run("#2D", 0.91);
const OLD = run("#1D", 0.915);
const numbers = {
  byRun: Object.fromEntries(
    [B, D, OLD].map((r) => [
      r.run_id,
      { rate: { rate: r.success_rate.passed / 90, n: 90 } },
    ]),
  ),
};

describe("like for like", () => {
  it("a run on another dataset version is partial", () => {
    expect(coverageOf(OLD, B)).toMatchObject({
      sameVersion: false,
      full: false,
      likeForLike: false,
      version: 1,
    });
    expect(coverageOf(D, B).likeForLike).toBe(true);
    expect(headlineRank(D, B, true)).toBe(0);
    expect(headlineRank(OLD, B, true)).toBe(1);
    expect(headlineRank(D, B, false)).toBeNull();
  });

  it("headlines the full-coverage best over a better partial run", () => {
    const pair = defaultPair(numbers, [B, D, OLD], B);
    expect(pair).toMatchObject({ candId: D.run_id, baseId: B.run_id });
  });

  it("follows two or more ticked runs", () => {
    const pair = defaultPair(
      numbers,
      [B, D, OLD],
      B,
      new Set([OLD.run_id, B.run_id]),
    );
    expect(pair).toMatchObject({
      candId: OLD.run_id,
      baseId: B.run_id,
      kind: "selection",
    });
  });
});

describe("ship line (S6 copy)", () => {
  const rowLabel = (n) => `T${n}`;
  const cmp = (rows, deltaPts = 25.4) => ({ rows, deltaPts, partial: null });
  it("names the first task that got worse", () => {
    const s = shipLine(
      cmp([
        { id: "r1", n: 1, change: "better" },
        { id: "r25", n: 25, change: "worse" },
      ]),
      { cand: "#2D Claude Code", base: "#2B", rowLabel },
    );
    expect(s.text).toBe(
      "Don't ship #2D Claude Code yet: it fails T25, which #2B passes.",
    );
    expect(s).toMatchObject({ rowId: "r25", rowText: "Open T25" });
  });
  it("says ship when no task got worse", () => {
    const s = shipLine(cmp([{ id: "r1", n: 1, change: "better" }]), {
      cand: "#2D",
      base: "#2B",
      rowLabel,
    });
    expect(s.text).toBe("No task got worse: review it, then ship.");
  });
  it("never says ship for a partial run or a run still going", () => {
    const rows = [
      { id: "r1", n: 1, change: "better" },
      { id: "r30", n: 30, change: "not_run", missing: "candidate" },
    ];
    expect(
      shipLine(cmp(rows), {
        cand: "#1D",
        base: "#2B",
        rowLabel,
        partial: true,
        total: 30,
      }).text,
    ).toBe(
      "No task got worse on the 1 it ran: run it on all 30 before you ship.",
    );
    expect(
      shipLine(
        { ...cmp(rows), partial: { scored: 1, total: 2 } },
        {
          cand: "#1D",
          base: "#2B",
          rowLabel,
        },
      ),
    ).toBeNull();
  });
});

describe("waiting runs (P2)", () => {
  it("says the experiment waits, never a verdict", () => {
    const w = (l) => ({
      run_id: l,
      label: l,
      experiment_id: "e4",
      experiment_ordinal: 4,
      status: "WaitingForResults",
      progress: { done: 0, total: 30 },
    });
    expect(waitingNote([w("#4A"), w("#4B"), w("#4C")])).toBe(
      "#4 waits for your terminal (3 runs).",
    );
    expect(waitingNote([w("#4A")])).toBe("#4A waits for your terminal.");
    expect(waitingNote([B])).toBeNull();
  });
});
