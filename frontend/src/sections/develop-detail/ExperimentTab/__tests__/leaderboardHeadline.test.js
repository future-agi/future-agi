import { describe, expect, it } from "vitest";
import {
  answerLines,
  costPerTask,
  headlineVerdict,
  isFinished,
  pctChangeText,
  shownMs,
  pickHeadline,
  shipLine,
  successOf,
  successPts,
  timePerTaskMs,
} from "../leaderboardHeadline";

// TS #2 at r2-after-import (numbers as the R1 mock returns them).
const run = (label, name, passed, total, cost, p50, extra = {}) => ({
  run_id: `id-${label}`,
  label,
  name,
  experiment_id: label.startsWith("#2") ? "e2" : "e1",
  experiment_ordinal: label.startsWith("#2") ? 2 : 1,
  created_at: label.startsWith("#2")
    ? "2026-10-16T10:08:40Z"
    : "2026-10-10T10:00:00Z",
  status: "Completed",
  trials: 3,
  examples: 30,
  progress: { done: 30, total: 30 },
  success_rate: { passed, total },
  metrics: { cost_usd: cost, latency_p50_s: p50 },
  ...extra,
});
const A = run(
  "#2A",
  "Support Ops Reply V1 + tools × gpt-4o-mini",
  48,
  90,
  0.12,
  6.1,
);
const B = run("#2B", "Support Agent v3", 59, 90, 0.33, 9.8);
const C = run("#2C", "LangGraph support agent", 77, 90, 0.57, 12.2);
const D = run("#2D", "Claude Code × claude-sonnet-4-5", 81, 89, 6.3, 74.35);
const E = run("#2E", "Codex CLI × gpt-5-codex", 75, 90, 3.6, 57);
const OLD = run("#1D", "Claude Code", 65, 71, 5.04, 70, {
  progress: { done: 24, total: 24 },
});

describe("locked numbers", () => {
  it("reads success, cost per task and time per task", () => {
    expect(successOf(D)).toMatchObject({ passed: 81, total: 89 });
    expect(successOf({ success_rate: { passed: 0, total: 0 } })).toBeNull();
    expect(costPerTask(D)).toBeCloseTo(0.21, 5);
    expect(
      costPerTask({ metrics: { cost_usd: 1 }, progress: { done: 0 } }),
    ).toBeNull();
    expect(timePerTaskMs(B)).toBe(9800);
    // L2 G2 R4: rounded once from the unrounded rates (25.46 → 25.5).
    expect(successPts(D, B)).toBeCloseTo(25.5, 5);
  });

  it("prints relative change grouped with a real minus", () => {
    // L2 G2 (consistency #11): the `×` glyph everywhere.
    expect(pctChangeText(0.21, 0.011)).toBe("19×");
    expect(pctChangeText(0.0055, 0.00088)).toBe("6.2×");
    expect(pctChangeText(0.016, 0.011)).toBe("+45%");
    expect(pctChangeText(0.004, 0.011)).toBe("−64%");
    expect(pctChangeText(1, 1)).toBe("±0%");
    expect(pctChangeText(1, 0)).toBeNull();
  });

  it("rounds durations like fmtMs before taking a delta", () => {
    expect(shownMs(74350) - shownMs(9800)).toBe(64200);
    expect(shownMs(842.4)).toBe(842);
    expect(shownMs(9849)).toBe(9800);
    expect(shownMs(null)).toBeNull();
  });

  it("never headlines a partial or stopped run", () => {
    expect(isFinished({ ...D, status: "Running" })).toBe(false);
    expect(isFinished({ ...D, status: "Cancelled" })).toBe(false);
    expect(isFinished({ ...D, progress: { done: 11, total: 30 } })).toBe(false);
    expect(isFinished(D)).toBe(true);
  });
});

describe("answer line", () => {
  it("prints the brief's sentence for TS #2", () => {
    const h = headlineVerdict([A, B, C, D, E, OLD], B);
    expect(h.candidate.label).toBe("#2D");
    expect(h.verdict.sentence).toBe(
      "Best: #2D Claude Code 91.0% (+25.5 pts vs baseline #2B) at 19× the cost per task.",
    );
    expect(["better", "mixed"]).toContain(h.verdict.tone);
  });

  it("names running runs and skips them", () => {
    const R = run("#3A", "New", 3, 10, 0.1, 5, {
      status: "Running",
      experiment_id: "e3",
      experiment_ordinal: 3,
      created_at: "2026-10-17T00:00:00Z",
      progress: { done: 4, total: 30 },
    });
    const h = headlineVerdict([R, A, B, D], B);
    expect(h.candidate.label).toBe("#2D");
    expect(h.verdict.sentence).toMatch(/1 run still running \(#3A\)\.$/);
  });

  it("says when no run beats the baseline, with noise wording", () => {
    const h = headlineVerdict([A, B], B);
    expect(h.verdict.sentence).toBe(
      "No run beats baseline #2B (65.6%). Closest: #2A Support Ops Reply V1 + tools 53.3% (−12.2 pts, within noise) at 0.4× the cost per task.",
    );
    expect(h.verdict.tone).toBe("tie");
  });

  it("returns null with nothing finished or running", () => {
    expect(pickHeadline([], null)).toBeNull();
  });
});

describe("IT-R4 G2 P5/P8: New, Too close to call and the count ship line", () => {
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
  const cmp = (cand, base, b, w, s) => ({
    rows: rows(b, w, s),
    better: b,
    worse: w,
    notRun: 0,
    partial: null,
    candidate: { rate: cand },
    baseline: { rate: base },
  });
  it("names the new run and the current baseline", () => {
    const a = answerLines({
      compare: cmp(0.91, 0.656, 10, 1, 19),
      verdict: { tone: "better", deltaPts: 25.4, significant: true },
      cand: { label: "#2D", name: "Claude Code", role: "new" },
      base: { label: "#2B", name: "Support Agent v3", role: "baseline" },
      noun: "task",
    });
    expect(a.parts.map((p) => p.text).join("")).toBe(
      "#2D Claude Code passes more tasks than the current baseline, #2B Support Agent v3: 91.0% vs 65.6% on 30 tasks.",
    );
  });
  it("reads Too close to call when the 95% range holds 0 (IT-R5 G2 P5)", () => {
    const a = answerLines({
      compare: cmp(19 / 24, 16 / 24, 7, 4, 13),
      verdict: { tone: "better", deltaPts: 12.5, significant: true },
      cand: { label: "#1B", name: "Acme voice v2" },
      base: { label: "#1A", name: "Acme voice v1", role: "baseline" },
      noun: "call",
    });
    expect(a.tone).toBe("tie");
    expect(a.parts.map((p) => p.text).join("")).toMatch(
      /^Too close to call: #1B Acme voice v2 and #1A v1 pass 79\.2% vs 66\.7% of 24 calls \(95% range −\d+ to \+\d+ pts\)\. 7 calls better, 4 worse\.$/,
    );
  });
  it("counts 3+ worse rows and names what they share", () => {
    const s = shipLine(cmp(0.79, 0.67, 7, 4, 13), {
      cand: "#1B Acme voice v2",
      candLabel: "#1B",
      base: "#1A",
      noun: "call",
      rowLabel: (n) => `Mike · call ${n}`,
      rowGroup: () => "Mike",
    });
    expect(s.text).toBe(
      "Don't ship #1B yet: 4 calls got worse (all with Mike), which #1A handled.",
    );
    expect(s.rowText).toBe("Open Mike · call 3");
  });
});
