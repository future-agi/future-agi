import { describe, expect, it } from "vitest";

import {
  EMPTY,
  NO_PRICE,
  costTotal,
  formatCostUpperBound,
  formatExactCost,
} from "../formatters";
import { overviewCost } from "../analyticsHelpers";

// A cost total over requests the gateway found no price for
// (each stored at 0, counted by the API as unpriced_requests) never reads as
// free. Every request unpriced reads as a Logs row does (an em dash titled
// "No price found"); some unpriced reads the priced part as a floor.
describe("costTotal: unpriced totals never read as free", () => {
  it("a total whose every request had no price reads an em dash, titled No price found", () => {
    const shown = costTotal("0", { unpriced: 2, requests: 2 });
    expect(shown.text).toBe(EMPTY);
    expect(shown.title).toBe(NO_PRICE);
    expect(shown.text).not.toMatch(/\$0\.00/);
  });

  it("a total with some unpriced requests reads the priced part as a floor, with how many had none", () => {
    const shown = costTotal("0.035", { unpriced: 3, requests: 6 });
    expect(shown.text).toBe("≥ $0.035");
    expect(shown.title).toBe(
      "3 of 6 requests had no price found, so this total counts only the priced ones.",
    );
  });

  it("one unpriced request of an unknown count still marks the floor", () => {
    const shown = costTotal("0.5", { unpriced: 1 });
    expect(shown.text).toBe("≥ $0.50");
    expect(shown.title).toBe(
      "1 request had no price found, so this total counts only the priced ones.",
    );
  });

  it("a fully priced total uses rounded display or a grid cell with its exact tooltip", () => {
    expect(costTotal("0.035", { unpriced: 0, requests: 6 })).toEqual({
      text: "$0.035",
      title: null,
      unknown: false,
    });
    expect(costTotal("0.001234", { requests: 6, cell: true })).toEqual({
      text: "$0.001234",
      title: null,
      unknown: false,
    });
    expect(costTotal("0", { unpriced: 0, requests: 4 }).text).toBe("$0.00");
  });

  it("a missing total reads an em dash with no title", () => {
    expect(costTotal(null, { unpriced: 0, requests: 0 })).toEqual({
      text: EMPTY,
      title: null,
      unknown: false,
    });
  });
});

describe("overviewCost reads the overview's cost with its unpriced count", () => {
  it("passes the total, its unpriced requests and the request count", () => {
    expect(
      overviewCost({
        total_cost: { value: "0.035", trend: 5, unpriced_requests: 3 },
        total_requests: { value: 6, trend: 0 },
      }),
    ).toEqual({ value: "0.035", unpriced: 3, requests: 6 });
    expect(overviewCost(undefined)).toEqual({
      value: undefined,
      unpriced: undefined,
      requests: undefined,
    });
  });
});

// Lower bounds must never round up; plain amounts are unchanged.
describe("costTotal: direction-aware lower bounds", () => {
  it.each([
    [0.087686, "$0.087686"],
    [0.035, "$0.035"],
    [12.3456, "$12.3456"],
    // R3-01/R3-03: snap binary arithmetic noise before the directional cut.
    [12.349999999999998, "$12.349999999999998"],
    [1.15, "$1.15"],
    [1234.5678, "$1,234.5678"],
    [0.000664, "$0.000664"],
    [0.1 + 0.2, "$0.30000000000000004"],
    [0.10069499999999999, "$0.10069499999999999"],
    [0.8727272727272727, "$0.8727272727272727"],
    [0.0876869, "$0.0876869"],
    [0.0000004, formatExactCost(0.0000004)],
    [0.00000004123, "$0.00000004123"],
    [0.00000001, "$0.00000001"],
    [-0.0876869, "-$0.0876869"],
    [-12.3456, "-$12.3456"],
  ])("never overstates the lower bound for %s", (value, expected) => {
    for (const options of [
      {},
      { cell: true },
      { format: (v) => `$${Number(v).toFixed(2)}` },
    ]) {
      expect(
        costTotal(value, { unpriced: 1, requests: 2, ...options }).text,
      ).toBe(`\u2265\u00a0${expected}`);
    }
  });

  it("bounds 10,000 sums of six-place ledger costs without float noise (R3-01)", () => {
    // Seeded samples are reproducible; integer microdollars are the exact oracle,
    // independent of both the formatter and the floating-point sum it receives.
    let seed = 595;
    const random = () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return seed / 2 ** 32;
    };
    const decimal = (text) => {
      const match = /^(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?$/i.exec(text);
      expect(match).not.toBeNull();
      const [, whole, fraction = "", exponent = "0"] = match;
      const places = fraction.length - Number(exponent);
      return {
        n: BigInt(whole + fraction) * 10n ** BigInt(Math.max(-places, 0)),
        d: 10n ** BigInt(Math.max(places, 0)),
      };
    };
    const amountIn = (text) => {
      const match = /\$(\d+\.\d+)$/.exec(text);
      expect(match).not.toBeNull();
      return decimal(match[1]);
    };
    for (let sample = 0; sample < 10000; sample += 1) {
      let sum = 0;
      let trueMicros = 0n;
      const count = 2 + Math.floor(random() * 9);
      for (let term = 0; term < count; term += 1) {
        const micros = 1 + Math.floor(random() * 99999);
        sum += micros / 1000000;
        trueMicros += BigInt(micros);
      }
      // The ledger total is authoritative when supplied as an exact decimal.
      // Retain both directional bounds against the independent integer sum.
      const ledger = `${trueMicros / 1000000n}.${String(trueMicros % 1000000n).padStart(6, "0")}`;
      const lower = costTotal(ledger, { unpriced: 1, requests: 2 }).text;
      const upper = formatCostUpperBound(ledger);
      expect(lower).toMatch(/^≥\s\$0\.\d+$/);
      expect(upper).toMatch(/^\$0\.\d+$/);
      const low = amountIn(lower);
      const high = amountIn(upper);
      expect(low.n * 1000000n).toBeLessThanOrEqual(trueMicros * low.d);
      expect(high.n * 1000000n).toBeGreaterThanOrEqual(trueMicros * high.d);
      // Exact rendering strengthens those bounds to equality, without losing
      // the original 10,000 numeric-sum inputs: their own decimal value must
      // also survive, rather than silently guessing a six-place ledger value.
      expect(low.n * 1000000n).toBe(trueMicros * low.d);
      expect(high.n * 1000000n).toBe(trueMicros * high.d);
      const numeric = decimal(String(sum));
      for (const text of [
        costTotal(sum, { unpriced: 1, requests: 2 }).text,
        formatCostUpperBound(sum),
      ]) {
        const shown = amountIn(text);
        expect(shown.n * numeric.d).toBe(numeric.n * shown.d);
      }
    }
  });

  it("leaves fully priced amounts on their existing display rules", () => {
    expect(costTotal(12.3456).text).toBe("$12.3456");
    expect(costTotal(0.087686).text).toBe("$0.087686");
    expect(costTotal(0.035).text).toBe("$0.035");
  });
});

// Analytics > Models, 7D: o1-mini read "≥ $0.00" (20 of
// its 30 requests unpriced, the rest failed upstream at cost 0) and small
// floors read "≥ <$0.0001". A floor of nothing says nothing: a total whose
// priced part is 0 is unknown, and a floor under the cell's resolution
// reads its exact amount.
describe("costTotal: a floor always says something", () => {
  it("reads a total whose priced part is 0 as unknown, saying how many had no price", () => {
    const shown = costTotal("0", { unpriced: 20, requests: 30, cell: true });
    expect(shown).toEqual({
      text: EMPTY,
      title: "No price found for 20 of 30 requests",
      unknown: true,
    });
  });

  it("marks every unknown total unknown", () => {
    expect(costTotal("0", { unpriced: 2, requests: 2 }).unknown).toBe(true);
    expect(costTotal("0.5", { unpriced: 1 }).unknown).toBe(false);
    expect(costTotal("0.5", { unpriced: 0, requests: 2 }).unknown).toBe(false);
  });

  it("reads a floor under the cell's resolution exactly, never ≥ <$0.0001", () => {
    expect(
      costTotal("0.000023", { unpriced: 1, requests: 10, cell: true }).text,
    ).toBe("≥ $0.000023");
  });

  // A caller's own format (the cache savings estimate's two
  // decimals) never rounds a non-zero amount to "$0.00".
  it("uses shared non-zero display when a custom format rounds it to zero", () => {
    const twoDecimals = (v) => `$${Number(v).toFixed(2)}`;
    expect(
      costTotal(0.004, { unpriced: 1, requests: 10, format: twoDecimals }).text,
    ).toBe("\u2265\u00a0$0.004");
    expect(costTotal(0.004, { format: twoDecimals }).text).toBe("$0.0040");
    expect(costTotal(0.8727, { format: twoDecimals }).text).toBe("$0.8727");
    expect(costTotal(0, { format: twoDecimals }).text).toBe("$0.00");
  });
});
