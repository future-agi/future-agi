// IT-R2 G2: one definition of better / worse / Mixed, Best value with a
// quality floor, the denominator said inline, the default pair by output.
import { describe, expect, it } from "vitest";
import {
  bestValueOf,
  bestValueText,
  compareRuns,
  extraCostPerSolved,
  notRunText,
  rowRangesText,
} from "../runNumbers";
import { verdictWord } from "src/sections/common/ux/verdictModel";
import { defaultRunsTab, outputModalityOf, sameOutput } from "../runKind";
import { experimentHrefFromRuns } from "../experimentHref";
import { answerLines } from "src/sections/develop-detail/ExperimentTab/leaderboardHeadline";

const cell = (pass, cost) => ({
  trials: pass,
  cost_usd: pass.map(() => cost),
});
const run = (id, rate, cost, n = 10) => ({
  id,
  label: `#${id}`,
  cells: Array.from({ length: n }, (_, i) =>
    cell(i < Math.round(rate * n) ? [1, 1, 1] : [0, 0, 0], cost),
  ),
});

describe("bestValueOf (P4)", () => {
  it("never names a cheap run that solves little; names it Cheapest apart", () => {
    const runs = [
      run("A", 0.5, 0.004), // cheapest, solves 50%
      run("B", 0.9, 0.016), // within 10 pts of the top, on the frontier
      run("C", 0.95, 0.2), // most solved, expensive
    ];
    const bv = bestValueOf(runs);
    expect(bv.id).toBe("B");
    expect(bv.cheapest.id).toBe("A");
    // MONEY-FMT e9f0a2d0dd / ruling 530: display cents, not rounded inputs.
    expect(bv.usd).toBeCloseTo(0.016 / 0.9, 10);
    expect(bestValueText(bv)).toBe(
      "#B · 90.0% at $0.017777777777777788 per solved task (cheapest: #A, passes only 50.0%)",
    );
  });
  it("is the cheapest run when that run is also good", () => {
    const bv = bestValueOf([run("A", 0.9, 0.004), run("B", 0.95, 0.2)]);
    expect(bv.id).toBe("A");
    expect(bv.cheapest).toBeNull();
  });
});

describe("one denominator, said inline (P3)", () => {
  const matrix = {
    rows: [1, 2, 3, 4, 5, 6].map((n) => ({
      id: `r${n}`,
      n,
      cells: n <= 4 ? { b: [0, 0, 0], c: [1, 1, 1] } : { b: [0, 0, 0] }, // the candidate did not run T5, T6
    })),
  };
  const cmp = compareRuns({
    matrix,
    baselineId: "b",
    candidateId: "c",
    noun: "task",
  });
  it("counts only tasks both ran and says which the other skipped", () => {
    expect(cmp.better).toBe(4);
    expect(cmp.notRun).toBe(2);
    expect(
      notRunText(cmp, { candidate: "Claude Code", datasetKind: "task_suite" }),
    ).toBe("Claude Code did not run T5, T6");
    expect(rowRangesText([25, 26, 27, 28, 29, 30], "task_suite")).toBe(
      "T25⁠–⁠T30",
    );
  });
  it("prices each extra solved task", () => {
    expect(
      extraCostPerSolved(
        { cand: 0.21, base: 0.011 },
        { cand: 0.915, base: 0.667 },
      ),
    ).toBeCloseTo(0.8, 2);
    expect(
      extraCostPerSolved({ cand: 0.01, base: 0.02 }, { cand: 0.9, base: 0.5 }),
    ).toBeNull();
  });
  it("reads the three answer lines from the same compare", () => {
    const a = answerLines({
      compare: cmp,
      verdict: {
        tone: "better",
        deltaPts: 100,
        confidence: { level: "medium", tries: 3 },
      },
      cand: { label: "#1D", name: "Claude Code" },
      base: { label: "#2B", name: "Support Agent v3", role: "baseline" },
      notRun: "Claude Code did not run T5, T6",
      costPerTask: { cand: 0.21, base: 0.011 },
      timePerTask: { cand: 74000, base: 9800 },
    });
    const line1 = a.parts.map((p) => p.text).join("");
    expect(line1).toBe(
      "#1D Claude Code passes more tasks than #2B Support Agent v3 (baseline): 100% vs 0% on the 4 tasks both ran (Claude Code did not run T5, T6) · 4 better · 0 worse",
    );
    expect(a.tradeoff).toBe(
      "Costs 19× more ($0.21 vs $0.011 per task) and is 64 s slower — about $0.19899999999999998 per extra task solved.",
    );
    expect(a.confidence.extra).toBe(
      "4 tasks × 3 tries; a gap this size could shrink with more tasks.",
    );
  });
});

describe("verdictWord (P3): Mixed only when some better AND some worse", () => {
  it("a costly win is Better, not Mixed", () => {
    expect(verdictWord({ tone: "mixed", better: 8, worse: 0 })).toBe("Better");
    expect(verdictWord({ tone: "mixed", better: 10, worse: 1 })).toBe("Mixed");
    expect(verdictWord({ tone: "tie", better: 3, worse: 2 })).toBe(
      "Too close to call",
    );
    expect(verdictWord({ tone: "worse", better: 0, worse: 5 })).toBe("Worse");
  });
});

describe("output modality and the default tab (P1, P6)", () => {
  const voice = (depth) => ({
    config: { kind: "voice", voice: { depth } },
  });
  it("tells voice, text-only voice, speech and image runs apart", () => {
    expect(outputModalityOf(voice("full_voice"))).toBe("voice");
    expect(outputModalityOf(voice("text_only"))).toBe("text");
    expect(outputModalityOf({ target_type: "tts", config: {} })).toBe("speech");
    expect(
      outputModalityOf({
        config: { kind: "prompt", output_modality: "image" },
      }),
    ).toBe("image");
    expect(sameOutput(voice("full_voice"), voice("text_only"))).toBe(false);
  });
  it("opens an experiment of 3+ runs on the Matrix", () => {
    const r = (id, exp) => ({
      run_id: id,
      experiment_id: exp,
      target_type: "agent",
      target: { graph_id: "G" },
    });
    const runs = [r("a", "E1"), r("b", "E1"), r("c", "E1"), r("d", "E2")];
    expect(defaultRunsTab(runs.slice(0, 3))).toBe("matrix");
    expect(experimentHrefFromRuns(runs, { experimentId: "E1" })).toBe(
      "/dashboard/develop/experiment/E1/matrix",
    );
    expect(experimentHrefFromRuns(runs, { runId: "a", datasetId: "D" })).toBe(
      "/dashboard/develop/experiment/E1/matrix?datasetId=D&cand=a",
    );
    expect(experimentHrefFromRuns(runs, { experimentId: "E2" })).toBe(
      "/dashboard/develop/experiment/E2/data",
    );
  });
});
