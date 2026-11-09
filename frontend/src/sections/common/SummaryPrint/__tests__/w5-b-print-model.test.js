// W5 B8: the printable Summary reads R5 / R1 as they are.
import { describe, expect, it } from "vitest";
import { wilson } from "src/sections/common/Analysis/runStats";
import { formatCi } from "src/sections/common/Metrics/metricFormat";
import { ciText, fmtMetric, printModel, verdictLine } from "../printModel";

const runs = [
  { run_id: "a", label: "#1A", name: "V3", config: { model: { id: "m1" } } },
  { run_id: "b", label: "#2B", name: "V4", config: { model: { id: "m2" } } },
];
const analysis = {
  verdicts: [{ run_id: "b", verdict: "better", sentence: "#2B beats #1A." }],
  metrics: [
    {
      key: "eval:c",
      name: "Correctness",
      family: "eval",
      output_type: "pass_fail",
      higher_is_better: true,
      per_run: [
        { run_id: "a", n: 238, value: 0.794, ci95: 0.05 },
        { run_id: "b", n: 238, value: 0.866, ci95: 0.04 },
      ],
    },
    {
      key: "ops:latency",
      name: "Latency",
      family: "ops",
      higher_is_better: false,
      per_run: [
        { run_id: "a", n: 238, value: 1200 },
        { run_id: "b", n: 238, value: 1758.3 },
      ],
    },
    { key: "human:x", name: "Human", family: "human", per_run: [] },
  ],
  worklist: {
    run_id: "b",
    tabs: {
      worse: {
        items: Array.from({ length: 14 }, (_, i) => ({
          example_id: `e${i}`,
          row_order: i + 1,
          input_preview: `input ${i}`,
          regressed: [{ metric: "eval:c", baseline: 1, candidate: 0 }],
        })),
      },
    },
  },
};

describe("printModel", () => {
  const m = printModel({ analysis, runs, baselineId: "a" });
  it("values are R5 per_run values, best and worst by direction", () => {
    expect(m.metrics.map((x) => x.name)).toEqual(["Correctness", "Latency"]);
    expect(m.metrics[0].cells.map((c) => c.value)).toEqual([0.794, 0.866]);
    expect(m.metrics[0].best).toBe("b");
    expect(m.metrics[0].worst).toBe("a");
    expect(m.metrics[1].best).toBe("a");
    expect(m.metrics[0].cells[1].text).toBe("86.6%");
    // Review M1: a rate prints its Wilson interval over n (the Summary's).
    expect(m.metrics[0].cells[1].ci).toBe("81.7–90.4%");
  });
  it("verdicts, config differences and the worst 10 rows", () => {
    expect(m.verdicts).toEqual([
      {
        runId: "b",
        label: "#2B",
        verdict: "better",
        sentence: "#2B beats #1A.",
      },
    ]);
    expect(m.configDiffs).toHaveLength(1);
    expect(m.configDiffs[0].chips.join(" ")).toContain("m2");
    expect(m.worst).toHaveLength(10);
    expect(m.worst[0].regressed).toBe("Correctness 100.0% → 0.0%");
    expect(m.worklistRun).toBe("#2B");
  });
  it("formats", () => {
    // L7c g6: the one money format (2 significant figures under a cent).
    expect(fmtMetric({ key: "ops:cost", family: "ops" }, 0.00088)).toBe(
      "$0.00088",
    );
    expect(fmtMetric({ key: "eval:t", family: "eval" }, 0.7)).toBe("0.70");
    expect(fmtMetric({ key: "x" }, null)).toBe("—");
  });
});

// Review M1 (rv-20-b8-print: Tone #7B, n 10, printed 53.9%–106.1%) and M2
// (a suppressed verdict printed "#7B ·" with nothing after it).
describe("print intervals and verdict lines (review fixes)", () => {
  const tone = {
    key: "eval:tone",
    name: "Tone",
    family: "eval",
    output_type: "pass_fail",
  };
  const pctOf = (text) => String(text).replace(/%/g, "").split("–").map(Number);
  it("n 10, p 0.8: the Wilson interval, within 0–100%", () => {
    const text = ciText(tone, 0.8, 10, 0.261);
    const [low, high] = pctOf(text);
    const w = wilson(0.8, 10);
    expect(text).toBe("49.0–94.3%");
    expect(low).toBeCloseTo(w.low * 100, 1);
    expect(high).toBeCloseTo(w.high * 100, 1);
    expect(low).toBeGreaterThanOrEqual(0);
    expect(high).toBeLessThanOrEqual(100);
  });
  it("every rate interval stays within 0–100%, at the edges too", () => {
    [
      [1, 10],
      [0, 10],
      [0.9, 3],
      [0.5, 1],
      [0.97, 238],
    ].forEach(([p, n]) => {
      const [low, high] = pctOf(ciText(tone, p, n, 0.4));
      expect(low).toBeGreaterThanOrEqual(0);
      expect(high).toBeLessThanOrEqual(100);
      expect(low).toBeLessThanOrEqual(p * 100 + 1e-9);
      expect(high).toBeGreaterThanOrEqual(p * 100 - 1e-9);
    });
    expect(ciText(tone, 1, 10, 0)).toBe("72.2–100%");
    expect(ciText(tone, 0.8, 0, 0.1)).toBeNull();
  });
  it("a mean keeps ± R5's ci95 in the metric's own format", () => {
    const score = { key: "eval:g", family: "eval", output_type: "score" };
    const ms = { key: "ops:latency", family: "ops", output_type: "ms" };
    expect(ciText(score, 0.74, 48, 0.05)).toBe(formatCi(score, 0.05));
    expect(ciText(score, 0.74, 48, 0.05)).toMatch(/^±/);
    expect(ciText(ms, 1758, 48, 312)).toBe(formatCi(ms, 312));
    expect(ciText(score, 0.74, 48, null)).toBeNull();
  });
  it("a suppressed verdict prints the Summary's reason, never an empty line", () => {
    const suppressed = {
      run_id: "c",
      verdict: null,
      suppressed: true,
      low_sample: true,
      sentence: null,
      caveats: [{ kind: "low_sample", count: 12, text: "n=12" }],
    };
    const line = verdictLine(suppressed);
    expect(line.reason).toBe(true);
    expect(line.text).toMatch(/^No verdict yet: /);
    expect(line.text).toContain("12 comparable so far");
    expect(verdictLine({ sentence: "Better than #1A" })).toEqual({
      text: "Better than #1A",
      reason: false,
    });
    expect(verdictLine({}).text.length).toBeGreaterThan(0);
    const m = printModel({
      analysis: { ...analysis, verdicts: [suppressed] },
      runs: [...runs, { run_id: "c", label: "#7B" }],
      baselineId: "a",
    });
    expect(m.verdicts[0].label).toBe("#7B");
    expect(m.verdicts[0].sentence.trim().length).toBeGreaterThan(0);
  });
});
