// W5 B7: the match dialog's words and the largest drops (pure).
import { describe, expect, it } from "vitest";
import {
  fmtMatchDelta,
  fmtMatchValue,
  matchByLabel,
  sideLabels,
  summaryLine,
  topDrops,
  verdictOf,
} from "../matchModel";

const side = (label, name) => ({ run_id: label, label, dataset_name: name });

describe("summaryLine", () => {
  it("counts matched, only-in each side and the duplicates kept first", () => {
    const m = {
      a: side("#1A", "Golden"),
      b: side("#2B", "Golden"),
      matched: 238,
      only_a: 0,
      only_b: 3,
      duplicates_a: 0,
      duplicates_b: 2,
      no_key_a: 0,
      no_key_b: 0,
    };
    expect(summaryLine(m)).toBe(
      "238 rows matched · 3 only in #2B · 0 only in #1A · 2 duplicate keys in #2B (first kept)",
    );
  });
  it("names the dataset when both runs carry the same label", () => {
    const m = {
      a: side("#1A", "Golden"),
      b: side("#1A", "Escalations"),
      matched: 3,
      only_a: 1,
      only_b: 0,
      duplicates_a: 0,
      duplicates_b: 0,
      no_key_a: 2,
      no_key_b: 0,
    };
    expect(sideLabels(m)).toEqual({
      a: "#1A · Golden",
      b: "#1A · Escalations",
    });
    expect(summaryLine(m)).toBe(
      "3 rows matched · 0 only in #1A · Escalations · 1 only in #1A · Golden · 2 rows in #1A · Golden have no key",
    );
  });
});

describe("formats and verdicts", () => {
  const rate = { key: "eval:c", output_type: "pass_fail" };
  const score = { key: "eval:g", output_type: "score" };
  it("formats a rate, a score, money and time by the metric's kind", () => {
    expect(fmtMatchValue(rate, 0.866)).toBe("86.6%");
    expect(fmtMatchValue(score, 0.784)).toBe("0.78");
    expect(fmtMatchValue({ key: "ops:latency" }, 1960.4)).toBe("1,960 ms");
    expect(fmtMatchValue(rate, null)).toBe("—");
    expect(fmtMatchDelta(rate, 0.071, 0.04)).toBe("+7.1 pts ± 4.0 pts");
    expect(fmtMatchDelta(score, -0.0821, null)).toBe("−0.082");
  });
  it("calls a change only when it is significant, by its direction", () => {
    expect(verdictOf({ significant: false, direction: "better" })).toBe(
      "no clear change",
    );
    expect(verdictOf({ significant: true, direction: "better" })).toBe(
      "better",
    );
    expect(verdictOf({ significant: true, direction: "worse" })).toBe("worse");
    expect(matchByLabel("input_hash")).toBe("Input hash");
    expect(matchByLabel("order_id")).toBe("Column order_id");
  });
});

describe("topDrops", () => {
  const rows = [
    { key: "a", delta: { "eval:c": -1, "ops:cost": 0.002 } },
    { key: "b", delta: { "eval:c": 0.5, "ops:cost": -0.001 } },
    { key: "c", delta: { "eval:c": -0.25, "ops:cost": 0.004 } },
    { key: "d", delta: {} },
  ];
  it("lists the rows where B got worse, worst first, by the metric's direction", () => {
    expect(
      topDrops(rows, { key: "eval:c", higher_is_better: true }).map(
        (r) => r.key,
      ),
    ).toEqual(["a", "c"]);
    expect(
      topDrops(rows, { key: "ops:cost", higher_is_better: false }).map(
        (r) => r.key,
      ),
    ).toEqual(["c", "a"]);
    expect(topDrops(rows, { key: "eval:c" }, 1).map((r) => r.key)).toEqual([
      "a",
    ]);
  });
});
