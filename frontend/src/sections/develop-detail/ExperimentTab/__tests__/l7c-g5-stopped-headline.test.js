// L7c g5 (action:stop-resume): the answer line says `Still running` only
// while the candidate can still play; once Stop ended it, its partial
// numbers say they cover only the tries it ran.
import { describe, expect, it } from "vitest";
import { answerLines } from "../leaderboardHeadline";

const rows = (better, worse, same) => [
  ...Array.from({ length: better }, (_, i) => ({
    id: `b${i}`,
    n: i + 1,
    change: "better",
  })),
  ...Array.from({ length: worse }, (_, i) => ({
    id: `w${i}`,
    n: 3 * (i + 1),
    change: "worse",
  })),
  ...Array.from({ length: same }, (_, i) => ({
    id: `s${i}`,
    n: 100 + i,
    change: "same",
  })),
];
const cmp = {
  rows: rows(3, 2, 6),
  better: 3,
  worse: 2,
  notRun: 0,
  partial: { scored: 22, total: 96 },
  candidate: { rate: 0.545 },
  baseline: { rate: 0.455 },
};
const text = (cand) =>
  answerLines({
    compare: cmp,
    verdict: { tone: "better", deltaPts: 9, significant: false },
    cand,
    base: { label: "#2B", name: "Support Reply V4", role: "baseline" },
    noun: "row",
  })
    .parts.map((p) => p.text)
    .join("");

describe("answer line after a Stop", () => {
  it("a running candidate's numbers will move", () => {
    expect(
      text({ label: "#7B", name: "Support Reply V5", status: "Running" }),
    ).toMatch(/Still running, so these numbers will move\.$/);
  });
  it("a stopped candidate never reads Still running", () => {
    const t = text({
      label: "#7B",
      name: "Support Reply V5",
      status: "Cancelled",
    });
    expect(t).not.toMatch(/still running/i);
    expect(t).toMatch(
      /Stopped part-way, so these numbers cover only the tries it ran\.$/,
    );
  });
});
