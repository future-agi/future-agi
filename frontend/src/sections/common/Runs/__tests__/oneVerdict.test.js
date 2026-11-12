// L2 G2 R4: one verdict, one delta, one time helper.
import { describe, expect, it } from "vitest";
import {
  TOO_CLOSE,
  successSignificance,
  successVerdict,
  tooCloseWording,
} from "../oneVerdict";
import { diffRange95, ptsDelta, timeDeltaWords } from "../runNumbers";

const input = (cand, base, n, extra = {}) => ({
  candidate: { label: "#2G", name: "Support Agent v3.1" },
  baseline: { label: "#2B" },
  scopeText: `across ${n} tasks`,
  worse: 2,
  better: 7,
  metrics: {
    success: { name: "Task success", noun: "task", cand, base, n, tries: 3 },
    ...extra,
  },
});

describe("ptsDelta", () => {
  it("rounds once from the unrounded rates", () => {
    expect(ptsDelta(76 / 90, 59 / 90)).toBe(18.9);
    expect(ptsDelta(0.844, 0.656)).toBe(18.8);
    expect(ptsDelta(null, 0.5)).toBeNull();
  });
});

describe("timeDeltaWords", () => {
  it("one helper for every time delta", () => {
    expect(timeDeltaWords(80400, 11200)).toBe("69 s slower");
    expect(timeDeltaWords(9800, 9900)).toBe("same time");
    expect(timeDeltaWords(1200, 2400)).toBe("1.2 s faster");
    expect(timeDeltaWords(null, 1)).toBeNull();
  });
});

describe("successVerdict", () => {
  it("a 95% range that holds 0 is Too close to call, never beats", () => {
    // 84.4% vs 65.6% on 30 tasks: diffRange95 holds 0.
    const range = diffRange95(76 / 90, 59 / 90, 30);
    expect(range.real).toBe(false);
    const v = successVerdict(input(76 / 90, 59 / 90, 30));
    expect(v.tone).toBe("tie");
    expect(v.significant).toBe(false);
    expect(
      v.sentence.startsWith(
        `${TOO_CLOSE}: #2G Support Agent v3.1 vs baseline #2B, +18.9 pts Task success (within noise) across 30 tasks`,
      ),
    ).toBe(true);
    expect(v.sentence).not.toMatch(/beats|is level with/);
    expect(v.range.text).toBe(range.text);
  });

  it("a range clear of 0 keeps verdictOf's sentence", () => {
    const v = successVerdict(input(0.95, 0.4, 60));
    expect(v.significant).toBe(true);
    expect(v.tone).toBe("better");
    expect(v.sentence).toMatch(
      /^#2G Support Agent v3\.1 beats baseline #2B: \+55\.0 pts/,
    );
  });

  it("an explicit significance wins (the compare's own)", () => {
    expect(
      successSignificance({ cand: 0.95, base: 0.4, n: 60, significant: false })
        .significant,
    ).toBe(false);
    const v = successVerdict({
      ...input(0.95, 0.4, 60),
      metrics: {
        success: { cand: 0.95, base: 0.4, n: 60, significant: false },
      },
    });
    expect(v.tone).toBe("tie");
    expect(v.sentence.startsWith(TOO_CLOSE)).toBe(true);
  });

  it("tooCloseWording leaves other tones alone", () => {
    const v = { tone: "better", parts: [], sentence: "x" };
    expect(tooCloseWording(v)).toBe(v);
  });
});
