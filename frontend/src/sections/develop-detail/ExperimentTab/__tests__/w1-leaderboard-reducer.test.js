// W1-8: the leaderboard reads a run's own trial reducer, with its interval.
import { describe, expect, it } from "vitest";
import { leaderboardNumbers } from "../leaderboardVersions";
import { reducedText } from "src/sections/common/Analysis/trialReducers";
import { passHatK, wilson } from "src/sections/common/Analysis/runStats";

const MATRIX = {
  rows: [
    {
      row_id: "r1",
      n: 1,
      cells: { A: { trials: [1, 1, 1] }, B: { trials: [1, 1, 1] } },
    },
    {
      row_id: "r2",
      n: 2,
      cells: { A: { trials: [1, 0, 1] }, B: { trials: [1, 0, 1] } },
    },
    {
      row_id: "r3",
      n: 3,
      cells: { A: { trials: [0, 0, 0] }, B: { trials: [0, 0, 0] } },
    },
    {
      row_id: "r4",
      n: 4,
      cells: { A: { trials: [1, 1, "error"] }, B: { trials: [1, 1, "error"] } },
    },
  ],
};
const run = (id, extra = {}) => ({
  run_id: id,
  label: `#1${id}`,
  trials: 3,
  status: "Completed",
  created_at: "2026-10-01T00:00:00Z",
  ...extra,
});

describe("W1-8 · leaderboard and the run's reducer", () => {
  it("a run with trial_reducer pass^3 reads pass^3 with its Wilson interval; a run without one has none", () => {
    const n = leaderboardNumbers(
      [run("A", { trial_reducer: { kind: "pass_hat_k" } }), run("B")],
      MATRIX,
    );
    const own = n.byRun.A.own;
    const cells = MATRIX.rows.map((r) => r.cells.A.trials);
    expect(own.value).toBe(passHatK(cells, 3).rate);
    const w = wilson(own.value, own.n);
    expect(own.ci95).toEqual([w.low, w.high]);
    expect(reducedText(own, { k: 3 })).toMatch(/^pass\^3 · 25% \(\d+%–\d+%\)$/);
    expect(n.byRun.B.own).toBeUndefined();
    const atLeast = leaderboardNumbers(
      [run("A", { trial_reducer: { kind: "at_least", n: 2 } })],
      MATRIX,
    ).byRun.A.own;
    // r1 (3 of 3), r2 (2 of 3) and r4 (2 of 3; the errored try fails) pass
    expect(atLeast.value).toBe(3 / 4);
    expect(reducedText(atLeast, { k: 3 })).toMatch(/^At least 2 of 3 · 75% /);
  });
});

describe("W1-8 · the pass^k column shows by default when a run sets its own rule", () => {
  it("hidden for runs without one; shown when a run has trial_reducer", async () => {
    const { columnCatalog } = await import("../leaderboardColumns");
    const passK = (runs) =>
      columnCatalog([], runs).find(
        (c) => c.key === "pass_k" || c.id === "pass_k",
      );
    const plain = passK([run("A")]);
    const own = passK([run("A", { trial_reducer: { kind: "pass_hat_k" } })]);
    expect(plain).toBeTruthy();
    expect(
      Boolean(plain.default ?? plain.defaultVisible ?? plain.visible),
    ).toBe(false);
    expect(Boolean(own.default ?? own.defaultVisible ?? own.visible)).toBe(
      true,
    );
  });
});
