import { describe, expect, it } from "vitest";
import {
  candidateGroupsOf,
  candidateRowsOf,
  checksCountOf,
  winnerSentenceOf,
} from "../epochCandidates";

/* Four candidates: the winner, one that passed and wasn't a finalist, one
   that broke a must-pass scenario, one with no gain on the targets. */
const record = {
  id: "OPT-1",
  result: {
    base: 60,
    heldBase: 20,
    heldScore: 70,
    noise: 2,
    winner: { n: 2, score: 85, delta: 25 },
    finalists: [{ n: 2, heldScore: 70, accepted: true }],
    trials: [
      { n: 1, score: 75, delta: 15, picks: [] },
      { n: 2, score: 85, delta: 25, picks: [] },
      {
        n: 3,
        score: 90,
        delta: 30,
        picks: [],
        brokeBlocker: true,
        mustPassFails: [{ title: "Refund rule", repeated: true }],
      },
      { n: 4, score: 55, delta: -5, picks: [] },
    ],
  },
};

describe("epoch candidates", () => {
  const rows = candidateRowsOf(record, { keptN: 2, hasWinner: true });

  it("puts each candidate at the step that stopped it", () => {
    const stage = Object.fromEntries(rows.map((r) => [r.n, r.stage]));
    expect(stage).toEqual({
      1: "passed",
      2: "winner",
      3: "mustpass",
      4: "target",
    });
    expect(rows.find((r) => r.n === 3).result).toBe(
      "Broke 1 must-pass scenario",
    );
  });

  it("shows held back only where the check ran", () => {
    expect(rows.find((r) => r.n === 2).held).toEqual({ from: 20, to: 70 });
    expect(rows.find((r) => r.n === 1).held).toBeNull();
  });

  it("orders groups winner first, then passed, then the latest step", () => {
    const groups = candidateGroupsOf(rows);
    expect(groups.map((g) => g.id)).toEqual([
      "winner",
      "passed",
      "mustpass",
      "target",
    ]);
    expect(groups.find((g) => g.id === "target").open).toBeFalsy();
    expect(groups.find((g) => g.id === "mustpass").open).toBe(true);
  });

  it("has no winner group when the epoch kept nothing", () => {
    const none = candidateRowsOf(record, { keptN: 2, hasWinner: false });
    const groups = candidateGroupsOf(none, { hasWinner: false });
    expect(groups.map((g) => g.id)).not.toContain("winner");
    expect(none.find((r) => r.n === 1).result).toBe("Not a finalist");
  });

  it("says why a passed candidate wasn't kept, even when it held better", () => {
    /* Two finalists tied on target: the later one held better, and still
       isn't the winner — held back is pass or fail, it doesn't rank. */
    const tied = {
      ...record,
      result: {
        ...record.result,
        finalists: [
          { n: 2, heldScore: 70, accepted: true },
          { n: 5, heldScore: 100, accepted: true },
        ],
        trials: [
          ...record.result.trials,
          { n: 5, score: 85, delta: 25, picks: [] },
        ],
      },
    };
    const list = candidateRowsOf(tied, { keptN: 2, hasWinner: true });
    const five = list.find((r) => r.n === 5);
    expect(five.result).toBe("Passed · tied, found later");
    expect(five.reason).toMatch(/doesn't rank/);
    expect(list.find((r) => r.n === 1).result).toBe("Passed · not a finalist");
  });

  it("names the group of passed candidates when nothing was kept", () => {
    const none = candidateRowsOf(record, { keptN: 2, hasWinner: false });
    const groups = candidateGroupsOf(none, { hasWinner: false });
    expect(groups.find((g) => g.id === "passed").label).toBe("Not a finalist");
  });

  it("leads with the winner's held-back result", () => {
    const checks = [
      { id: "blockers", pass: true },
      { id: "rules", pass: true },
      { id: "calibration", pass: null },
    ];
    const winner = rows.find((r) => r.stage === "winner");
    expect(
      winnerSentenceOf({ checks, heldScore: 70 }, record.result, winner),
    ).toBe(
      "Candidate 2: held-back scenarios it never saw went 20% → 70%. No must-pass scenario broke.",
    );
    expect(checksCountOf(checks)).toEqual({
      passed: 2,
      unknown: 1,
      failed: [],
    });
  });
});
