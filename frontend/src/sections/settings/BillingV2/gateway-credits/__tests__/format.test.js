import { describe, expect, it } from "vitest";

import {
  formatAmount,
  formatBalance,
  monthLabel,
  recentMonths,
} from "../format";

describe("credit amounts", () => {
  it("keeps every digit the ledger holds below a dollar", () => {
    expect(formatAmount(0.000664)).toBe("$0.000664");
    expect(formatAmount(-0.000002)).toBe("-$0.000002");
    expect(formatAmount(0.5)).toBe("$0.50");
    expect(formatAmount(0)).toBe("$0.00");
    expect(formatAmount(1234.5)).toBe("$1,234.50");
    expect(formatAmount(null)).toBe("—");
  });

  it("never rounds a balance up", () => {
    expect(formatBalance(34.999336)).toBe("$34.999336");
    expect(formatBalance(35)).toBe("$35.00");
    expect(formatBalance(0.000336)).toBe("$0.000336");
  });

  it("lists the last months, newest first, across a year end", () => {
    expect(recentMonths(3, new Date("2026-02-10T00:00:00Z"))).toEqual([
      "2026-02",
      "2026-01",
      "2025-12",
    ]);
    expect(monthLabel("2026-10")).toBe("October 2026");
  });
});
