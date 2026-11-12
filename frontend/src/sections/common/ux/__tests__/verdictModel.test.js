// Verdict sentence, pinned on the TS #2 world (r2-after-import, STORY.md):
// #2D 81/89 = 91.0%, #2B 59/90 = 65.6%, cost per attempt $0.21 vs $0.011,
// p50 74 s vs 9.8 s, per row vs #2B: D ▲10 ▼1.
import { describe, expect, it } from "vitest";
import { confidenceOf, sortVerdicts, verdictOf } from "../verdictModel";

const TS2 = {
  B: {
    label: "#2B",
    name: "Support Agent v3",
    ok: 59,
    n: 90,
    cost: 0.011,
    p50: 9800,
  },
  A: {
    label: "#2A",
    name: "Support Ops Reply V1",
    ok: 48,
    n: 90,
    cost: 0.004,
    p50: 6100,
  },
  C: {
    label: "#2C",
    name: "LangGraph support agent",
    ok: 77,
    n: 90,
    cost: 0.019,
    p50: 12400,
  },
  D: {
    label: "#2D",
    name: "Claude Code",
    ok: 81,
    n: 89,
    cost: 0.21,
    p50: 74000,
  },
  E: { label: "#2E", name: "Codex CLI", ok: 75, n: 90, cost: 0.12, p50: 58000 },
};
const vs = (l, extra = {}) => {
  const c = TS2[l];
  const b = TS2.B;
  return verdictOf({
    candidate: { label: c.label, name: c.name },
    baseline: { label: b.label, name: b.name },
    metrics: {
      success: {
        cand: c.ok / c.n,
        base: b.ok / b.n,
        n: 30,
        tries: 3,
        candN: c.n,
        baseN: b.n,
      },
      costPerTask: { cand: c.cost, base: b.cost },
      timePerTask: { cand: c.p50, base: b.p50 },
    },
    ...extra,
  });
};

describe("verdictOf", () => {
  it("pins the TS #2D vs #2B sentence", () => {
    const v = vs("D", { worse: 1, better: 10 });
    expect(v.sentence).toBe(
      "#2D Claude Code beats baseline #2B: +25.4 pts success · improved 10 · worse 1 · 19× cost · 64 s slower. Confidence: medium (30 tasks × 3 tries).",
    );
    expect(v.verb).toBe("beats");
    // success leads; the cost/time trade-off turns the dot amber
    expect(v.tone).toBe("mixed");
    expect(v.tradeoff).toBe(true);
    expect(v.confidence.level).toBe("medium");
    expect(v.parts.filter((p) => p.t === "run").map((p) => p.text)).toEqual([
      "#2D Claude Code",
      "#2B",
    ]);
    expect(v.parts.find((p) => p.t === "term" && p.k === "delta").text).toBe(
      "+25.4 pts success",
    );
    expect(v.chips.map((c) => c.label)).toEqual([
      "+25.4 pts success",
      "19× cost",
      "64 s slower",
      "1 worse",
    ]);
  });

  it("#2A vs #2B overlaps (Wilson): level, within noise", () => {
    const v = vs("A", { worse: 14 });
    expect(v.tone).toBe("tie");
    expect(v.sentence).toBe(
      "#2A Support Ops Reply V1 is level with baseline #2B: −12.3 pts success (within noise) · worse 14 · 0.4× cost · 3.7 s faster. Confidence: low (30 tasks × 3 tries).",
    );
  });

  it("trails when success is significantly lower", () => {
    const v = verdictOf({
      candidate: { label: "#4A" },
      baseline: { label: "#3A" },
      metrics: {
        success: { cand: 0.71, base: 0.86, n: 248, significant: true },
      },
      worse: 21,
    });
    expect(v.tone).toBe("worse");
    expect(v.sentence).toBe(
      "#4A trails baseline #3A: −15.0 pts success · worse 21. Confidence: high (248 tasks).",
    );
  });

  it("is level within noise and says low confidence", () => {
    const v = verdictOf({
      candidate: "#2F",
      baseline: "#2B",
      metrics: {
        success: {
          cand: 0.656,
          base: 0.656,
          n: 30,
          tries: 3,
          significant: false,
        },
      },
      worse: 0,
    });
    expect(v.tone).toBe("tie");
    expect(v.sentence).toBe(
      "#2F is level with baseline #2B: ±0.0 pts success (within noise) · worse 0. Confidence: low (30 tasks × 3 tries).",
    );
  });

  it("never recomputes an API significance; uses the API delta", () => {
    const v = verdictOf({
      candidate: "#9A",
      baseline: "#9B",
      metrics: { success: { cand: 0.9, base: 0.5, n: 60, significant: false } },
    });
    expect(v.significant).toBe(false);
    expect(v.tone).toBe("tie");
    const w = verdictOf({
      candidate: "#9A",
      baseline: "#9B",
      metrics: {
        success: { cand: 0.9, base: 0.5, n: 60, delta: 0.4, significant: true },
      },
    });
    expect(w.deltaPts).toBe(40);
    expect(w.confidence.text).toBe("high (60 tasks)");
    expect(w.tone).toBe("better");
  });

  it("is mixed when a quality metric is significantly worse", () => {
    expect(vs("C", { worse: 7 }).verb).toBe("beats");
    const m = verdictOf({
      candidate: "#2C",
      baseline: "#2B",
      metrics: {
        success: {
          cand: 77 / 90,
          base: 59 / 90,
          n: 30,
          tries: 3,
          candN: 90,
          baseN: 90,
        },
        others: [{ name: "Tone", significantlyWorse: true }],
      },
    });
    expect(m.verb).toBe("is mixed vs");
    expect(m.tone).toBe("mixed");
  });

  it("confidence counts tasks", () => {
    expect(confidenceOf({ significant: true, tasks: 50, tries: 3 }).level).toBe(
      "high",
    );
    expect(confidenceOf({ significant: true, tasks: 30, tries: 3 }).text).toBe(
      "medium (30 tasks × 3 tries)",
    );
    expect(confidenceOf({ significant: false, tasks: 248 }).level).toBe("low");
  });

  it("sorts best first", () => {
    const list = sortVerdicts([vs("A"), vs("D"), vs("C"), vs("E")]);
    expect(list.map((v) => v.parts[0].text.slice(0, 3))).toEqual([
      "#2D",
      "#2C",
      "#2E",
      "#2A",
    ]);
  });
});
