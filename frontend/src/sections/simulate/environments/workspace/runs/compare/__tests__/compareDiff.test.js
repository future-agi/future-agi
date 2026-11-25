import { describe, it, expect } from "vitest";
import { wordDiff } from "../compareDiff";

const change = (up, better, value) => ({ up, better, change: value });

describe("wordDiff", () => {
  it("words each server change the way its cell shows the value", () => {
    const diff = wordDiff({
      was: "failed",
      subGoals: change(true, true, 1),
      measures: {
        duration: change(true, false, 200),
        csat: change(true, true, 0.3),
        latency: change(false, true, 50),
        stopLatency: change(false, true, 1200),
        tokens: change(true, false, 1500),
        turns: null,
      },
      evals: { tone: change(true, true, 30), policy: null },
    });

    expect(diff.was).toBe("Failed");
    expect(diff.subGoals).toEqual({ up: true, better: true, text: "1" });
    expect(diff.measures.duration).toMatchObject({ up: true, text: "0.2s" });
    expect(diff.measures.csat.text).toBe("0.3");
    expect(diff.measures.latency).toEqual({
      up: false,
      better: true,
      text: "50ms",
    });
    expect(diff.measures.stopLatency.text).toBe("1,200ms");
    expect(diff.measures.tokens.text).toBe("1,500");
    expect(diff.measures.turns).toBeNull();
    expect(diff.evals).toEqual({
      tone: { up: true, better: true, text: "30 pts" },
      policy: null,
    });
  });

  it("names an outcome without a label as inconclusive", () => {
    expect(wordDiff({ was: "unmeasured" }).was).toBe("Inconclusive");
    expect(wordDiff({ was: null }).was).toBeNull();
  });

  it("is nothing for a call the server did not compare", () => {
    expect(wordDiff(null)).toBeNull();
    expect(wordDiff(undefined)).toBeNull();
  });
});
