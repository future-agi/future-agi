import { describe, expect, it } from "vitest";
import {
  formatQ,
  isBorderline,
  notableDirection,
  notableShort,
  notableText,
  notableTooltip,
  sortNotable,
} from "../insightsNotable";

const rate = (metric, label, value, rest, q = 0.003) => ({
  kind: "rate",
  metric,
  label,
  value,
  rest,
  q,
});
const median = (metric, label, value, rest, q = 0.003) => ({
  kind: "median",
  metric,
  label,
  value,
  rest,
  q,
});

describe("notableText", () => {
  it("states a rate as two percentages", () => {
    expect(notableText(rate("failure", "had an agent failure", 1, 0.59))).toBe(
      "100% had an agent failure vs 59% elsewhere",
    );
  });

  it("states latency in seconds", () => {
    expect(
      notableText(median("latency_ms", "median latency", 4100, 1200)),
    ).toBe("median latency 4.1 s vs 1.2 s elsewhere");
  });

  it("states sentiment and turns as medians", () => {
    expect(notableText(median("sentiment", "sentiment", 1, 3))).toBe(
      "median sentiment 1.0 vs 3.0 elsewhere",
    );
    expect(notableText(median("turns", "turns", 10, 4))).toBe(
      "median 10 turns vs 4 elsewhere",
    );
  });

  it("keeps attribute labels from the report", () => {
    expect(
      notableText(rate("attr:asked_for_human", "had asked for human", 0.8, 0)),
    ).toBe("80% had asked for human vs 0% elsewhere");
  });
});

describe("notableShort", () => {
  it("is compact enough for a table chip", () => {
    expect(
      notableShort(
        rate("unresolved", "ended unresolved or abandoned", 0, 0.59),
      ),
    ).toBe("Unresolved 0% vs 59%");
    expect(notableShort(rate("attr:asked_for_human", "x", 0.8, 0))).toBe(
      "Asked for human 80% vs 0%",
    );
  });
});

describe("notableDirection", () => {
  it("treats more failures, errors, latency and turns as worse", () => {
    expect(notableDirection(rate("error", "hit an error", 0.27, 0))).toBe(
      "worse",
    );
    expect(notableDirection(median("latency_ms", "l", 4100, 1200))).toBe(
      "worse",
    );
    expect(notableDirection(median("turns", "t", 10, 4))).toBe("worse");
    expect(notableDirection(rate("unresolved", "u", 0, 0.59))).toBe("better");
  });

  it("treats higher sentiment as better", () => {
    expect(notableDirection(median("sentiment", "s", 4, 2))).toBe("better");
    expect(notableDirection(median("sentiment", "s", 1, 3))).toBe("worse");
  });

  it("gives attributes no direction", () => {
    expect(notableDirection(rate("attr:asked_for_human", "x", 0.8, 0))).toBe(
      "neutral",
    );
  });
});

describe("notableTooltip", () => {
  it("explains the correction and flags borderline results", () => {
    expect(notableTooltip(rate("error", "e", 0.3, 0, 0.0034))).toBe(
      "Holds after correcting for every comparison in this report (q = 0.003).",
    );
    const borderline = rate("error", "e", 0.27, 0, 0.0941);
    expect(isBorderline(borderline)).toBe(true);
    expect(notableTooltip(borderline)).toContain("q = 0.09");
    expect(notableTooltip(borderline)).toContain("Borderline");
  });

  it("formats tiny q values without scientific notation", () => {
    expect(formatQ(3.17e-7)).toBe("< 0.001");
    expect(notableTooltip({ q: 3.17e-7 })).toContain("(q < 0.001)");
  });

  it("sorts the strongest first", () => {
    const notes = [
      rate("a", "a", 1, 0, 0.09),
      rate("b", "b", 1, 0, 0.001),
      rate("c", "c", 1, 0, 0.02),
    ];
    expect(sortNotable(notes).map((n) => n.metric)).toEqual(["b", "c", "a"]);
  });
});
