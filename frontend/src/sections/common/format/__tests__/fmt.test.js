// Pins every example of the R3 G0 §B4 formatting table.
import { describe, expect, it } from "vitest";
import {
  EMPTY,
  NUM_SX,
  fmtCost,
  fmtCostDelta,
  fmtCount,
  fmtDeltaMs,
  fmtMs,
  fmtMultiple,
  fmtNumber,
  fmtPct,
  fmtPts,
  fmtRate,
  fmtScore,
  fmtSec,
  fmtTimeAbsolute,
  fmtTimeRelative,
  fmtTokens,
  ptsBetween,
} from "../fmt";

describe("percent", () => {
  it("1 dp; exact 0 and 1; never float garbage", () => {
    expect(fmtPct(0.866)).toBe("86.6%");
    expect(fmtPct(0.86)).toBe("86.0%");
    expect(fmtPct(1)).toBe("100%");
    expect(fmtPct(0)).toBe("0%");
    expect(fmtPct(0.1 + 0.2)).toBe("30.0%");
    expect(fmtPct(59 / 90)).toBe("65.6%");
    expect(fmtPct(81 / 89)).toBe("91.0%");
    expect(fmtPct(null)).toBe(EMPTY);
    expect(fmtPct(undefined)).toBe("—");
    expect(fmtPct(Number.NaN)).toBe("—");
  });
  it("rate = percent + (passed/total)", () => {
    expect(fmtRate(59, 90)).toBe("65.6% (59/90)");
    expect(fmtRate(81, 89)).toBe("91.0% (81/89)");
    expect(fmtRate(0, 0)).toBe("—");
  });
});

describe("pts", () => {
  it("sign always, 1 dp, U+2212, ±0.0 under 0.05", () => {
    expect(fmtPts(20)).toBe("+20.0 pts");
    expect(fmtPts(-12.2)).toBe("−12.2 pts");
    expect(fmtPts(0)).toBe("±0.0 pts");
    expect(fmtPts(-0.04)).toBe("±0.0 pts");
    expect(fmtPts(0.2, { fraction: true })).toBe("+20.0 pts");
    expect(fmtPts(-0.0000001, { fraction: true })).toBe("±0.0 pts");
    expect(fmtPts(null)).toBe("—");
    expect(fmtPts(25.4)).not.toMatch(/pp|-0\.00/);
  });
  it("ptsBetween = difference of the displayed percents", () => {
    // 81/89 = 91.01%, 59/90 = 65.56%: raw 25.46, displayed 91.0 − 65.6
    expect(ptsBetween(81 / 89, 59 / 90)).toBe(25.4);
    expect(fmtPts(ptsBetween(81 / 89, 59 / 90))).toBe("+25.4 pts");
    expect(ptsBetween(0.5, 0.5)).toBe(0);
    expect(ptsBetween(null, 0.5)).toBeNull();
  });
});

describe("cost", () => {
  it("preserves exact costs and plain padding under MONEY-QUAL-31 add.1", () => {
    expect(fmtCost(0.0041)).toBe("$0.0041");
    expect(fmtCost(0.000075)).toBe("$0.000075");
    expect(fmtCost(0.00000012)).toBe("$0.00000012");
    expect(fmtCost(0.011)).toBe("$0.011");
    expect(fmtCost(0.21)).toBe("$0.21");
    expect(fmtCost(0.5)).toBe("$0.50");
    expect(fmtCost(6.3)).toBe("$6.30");
    expect(fmtCost(999.99)).toBe("$999.99");
    expect(fmtCost(1240)).toBe("$1240.00");
    expect(fmtCost(0)).toBe("$0.00");
    expect(fmtCost(null)).toBe("—");
    expect(fmtCost(undefined)).toBe("—");
    expect(fmtCost(-0.21)).toBe("-$0.21");
    expect(fmtCostDelta(0.199)).toBe("+$0.199");
    expect(fmtCostDelta(-0.0041)).toBe("−$0.0041");
  });
});

describe("multiple", () => {
  it("1 dp below 10, whole above", () => {
    expect(fmtMultiple(1.7)).toBe("1.7×");
    expect(fmtMultiple(0.21, 0.011)).toBe("19×");
    expect(fmtMultiple(0.0187, 0.011)).toBe("1.7×");
    expect(fmtMultiple(1, 0)).toBe("—");
    expect(fmtMultiple(null, 1)).toBe("—");
  });
});

describe("duration", () => {
  it("ms, 1 dp s, whole s, m ss", () => {
    expect(fmtMs(842)).toBe("842 ms");
    expect(fmtMs(9800)).toBe("9.8 s");
    expect(fmtMs(74000)).toBe("74 s");
    expect(fmtMs(725000)).toBe("12 m 05 s");
    expect(fmtMs(999.7)).toBe("1.0 s");
    expect(fmtMs(9970)).toBe("10 s");
    expect(fmtMs(599700)).toBe("10 m 00 s");
    expect(fmtMs(null)).toBe("—");
    expect(fmtSec(74)).toBe("74 s");
  });
  it("deltas, signed and in words", () => {
    expect(fmtDeltaMs(64200)).toBe("+64 s");
    expect(fmtDeltaMs(-1200)).toBe("−1.2 s");
    expect(fmtDeltaMs(0)).toBe("±0 ms");
    expect(fmtDeltaMs(64200, { style: "words" })).toBe("64 s slower");
    expect(fmtDeltaMs(-3700, { style: "words" })).toBe("3.7 s faster");
    expect(fmtDeltaMs(0, { style: "words" })).toBe("same time");
  });
});

describe("tokens, score, count", () => {
  it("formats", () => {
    expect(fmtTokens(1017)).toBe("1,017 tok");
    expect(fmtScore(0.7423)).toBe("0.74");
    expect(fmtScore(-0.001)).toBe("0.00");
    expect(fmtCount(1248, "row")).toBe("1,248 rows");
    expect(fmtCount(1, "row")).toBe("1 row");
    expect(fmtCount(3, "trial")).toBe("3 trials");
    expect(fmtCount(1248)).toBe("1,248");
    expect(fmtNumber(1234.56)).toBe("1,234.6");
    expect(fmtNumber(-2)).toBe("−2");
  });
  it("NUM_SX is tabular and right-aligned", () => {
    expect(NUM_SX).toMatchObject({
      fontVariantNumeric: "tabular-nums",
      textAlign: "right",
    });
  });
});

describe("time", () => {
  const now = Date.parse("2026-10-16T10:00:00Z");
  it("relative short", () => {
    expect(fmtTimeRelative(now - 52_000, { now })).toBe("52s ago");
    expect(fmtTimeRelative(now - 3_000, { now })).toBe("just now");
    expect(fmtTimeRelative(now - 5 * 60_000, { now })).toBe("5m ago");
    expect(fmtTimeRelative(now - 2 * 3_600_000, { now })).toBe("2h ago");
    expect(fmtTimeRelative(now - 4 * 86_400_000, { now })).toBe("4d ago");
    expect(fmtTimeRelative(now + 5 * 60_000, { now })).toBe("in 5m");
    expect(
      fmtTimeRelative("2026-09-12T10:38:00Z", { now, tz: "Asia/Kolkata" }),
    ).toBe("Sep 12");
    expect(
      fmtTimeRelative("2025-10-12T10:38:00Z", { now, tz: "Asia/Kolkata" }),
    ).toBe("Oct 12, 2025");
    expect(fmtTimeRelative(null, { now })).toBe("—");
  });
  it("absolute with zone name and offset", () => {
    expect(
      fmtTimeAbsolute("2026-10-12T10:38:00Z", { tz: "Asia/Kolkata" }),
    ).toBe("Oct 12, 2026, 4:08 PM IST (UTC+5:30)");
    expect(
      fmtTimeAbsolute("2026-10-12T10:38:00Z", {
        tz: "Asia/Kolkata",
        by: "Ravi Menon",
      }),
    ).toBe("Oct 12, 2026, 4:08 PM IST (UTC+5:30) · by Ravi Menon");
    expect(fmtTimeAbsolute("2026-10-12T10:38:00Z", { tz: "UTC" })).toBe(
      "Oct 12, 2026, 10:38 AM UTC",
    );
    expect(
      fmtTimeAbsolute("2026-10-12T10:38:00Z", { tz: "America/New_York" }),
    ).toBe("Oct 12, 2026, 6:38 AM EDT (UTC-4)");
  });
});
