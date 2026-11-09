// L7c g6: Best of N has one scorer (no judge mode), outright wins (a tie
// is never a fractional win) and an order only for results that judged.
import { describe, expect, it } from "vitest";
import {
  judgedOrder,
  outrightShares,
  pickOptions,
  summaryLine,
  winsText,
} from "../bestOfModel";

const rows = [
  {
    example_id: "r1",
    winner: "a",
    tied: [],
    order: ["b", "a"],
    scores: { a: 1, b: 0 },
  },
  {
    example_id: "r2",
    winner: "tie",
    tied: ["a", "b"],
    order: ["a", "b"],
    scores: { a: 1, b: 1 },
  },
  {
    example_id: "r3",
    winner: "tie",
    tied: ["a", "b"],
    order: ["a", "b"],
    scores: { a: 1, b: 1 },
  },
  {
    example_id: "r4",
    winner: "b",
    tied: [],
    order: ["a", "b"],
    scores: { a: null, b: 1 },
  },
  {
    example_id: "r5",
    winner: null,
    tied: [],
    skipped: true,
    order: [],
    scores: {},
  },
];
const rec = {
  mode: "max_score",
  run_ids: ["a", "b"],
  n: 4,
  ties: 2,
  skipped: 1,
  rows,
};

describe("best-of g6", () => {
  it("never offers a judge mode", () => {
    const opts = pickOptions([
      { key: "eval:tone", name: "Tone", judge: true, judge_model: "m" },
      { key: "check:x", name: "Status", judge: false },
    ]);
    expect(opts.every((o) => o.mode === "max_score")).toBe(true);
    expect(opts.map((o) => o.label).join(" ")).not.toMatch(/Judge|check\)/);
  });
  it("counts outright wins; ties are whole rows, never fractions", () => {
    const { n, shares, tieRows, tieShare } = outrightShares(rec);
    expect(n).toBe(4);
    const a = shares.find((s) => s.run_id === "a");
    const b = shares.find((s) => s.run_id === "b");
    expect([a.wins, a.ties, b.wins, b.ties]).toEqual([1, 2, 1, 2]);
    expect(a.share).toBe(0.25);
    expect(Number.isInteger(a.wins)).toBe(true);
    expect(tieRows).toBe(2);
    expect(tieShare).toBe(0.5);
    // a run's wins equal the rows its filter opens (winner === run)
    expect(rows.filter((r) => r.winner === "a")).toHaveLength(a.wins);
  });
  it("words counts with grammar", () => {
    expect(winsText({ wins: 1, ties: 1 })).toBe("1 win · 1 tie");
    expect(winsText({ wins: 3, ties: 0 })).toBe("3 wins · 0 ties");
    expect(summaryLine({ ...rec, n: 1, ties: 1 }, (x) => x)).toMatch(
      /^1 row decided · 1 tie/,
    );
  });
  it("shows a judged order only for judged results, only runs with output", () => {
    expect(judgedOrder(rec, rows[3])).toEqual([]);
    expect(judgedOrder({ mode: "judge" }, rows[3])).toEqual(["b"]);
  });
});
