import { describe, expect, it } from "vitest";

import {
  EMPTY,
  balanceTooltip,
  costTooltip,
  formatBalance as gatewayBalance,
  formatCost,
  formatCostCell,
  formatCostTick,
  formatCostUpperBound,
  formatLatency,
  formatNumber,
  formatPercent,
} from "../formatters";
import {
  formatAmount,
  formatAmountCell,
  formatBalance,
} from "src/sections/settings/BillingV2/gateway-credits/format";

// Gateway summaries use the trace/dataset display rule. Ledger amounts, grid
// tooltips and sub-cent balances preserve exact amounts independently.
// A missing amount is an em dash, never "$0.00".
describe("formatCost (shared display)", () => {
  it("uses seven decimals for tiny costs and four below a cent", () => {
    expect(formatCost(0.000004)).toBe("$0.000004");
    expect(formatCost(0.000039)).toBe("$0.000039");
    expect(formatCost(0.0000042)).toBe("$0.0000042");
    expect(formatCost(0.0004)).toBe("$0.0004");
    expect(formatCost(0.000664)).toBe("$0.000664");
    expect(costTooltip(0.000664)).toBe(null);
    expect(formatCost(0.5)).toBe("$0.50");
    expect(formatCost(0.1 + 0.2)).toBe("$0.30000000000000004");
  });

  it("uses the shared ungrouped cents display for dollar amounts", () => {
    expect(formatCost(1)).toBe("$1.00");
    expect(formatCost(12.345)).toBe("$12.345");
    expect(formatCost(1234.5)).toBe("$1234.50");
  });

  it("writes zero as $0.00 and a missing amount as an em dash", () => {
    expect(formatCost(0)).toBe("$0.00");
    expect(formatCost("0")).toBe("$0.00");
    expect(formatCost(null)).toBe("—");
    expect(formatCost(undefined)).toBe("—");
    expect(formatCost("")).toBe("—");
    expect(formatCost(Number.NaN)).toBe("—");
    expect(formatCost("abc")).toBe("—");
    expect(EMPTY).toBe("—");
  });

  it("puts the sign before the dollar sign", () => {
    expect(formatCost(-0.000004)).toBe("-$0.000004");
    expect(formatCost(-12.5)).toBe("-$12.50");
  });
});

// Release-lead 595: upper bounds preserve small values and never round down.
describe("formatCostUpperBound (caps)", () => {
  it.each([
    [0.087686, "$0.087686"],
    [0.035, "$0.035"],
    [0.00000001, "$0.00000001"],
    [12.341, "$12.341"],
    // R3-01/R3-03: snap first, then ceil to ledger precision (cents above $1).
    [12.340000000000002, "$12.340000000000002"],
    [1.0000000000000002, "$1.0000000000000002"],
    [0.000664, "$0.000664"],
    [0.1 + 0.2, "$0.30000000000000004"],
    [0.8727272727272727, "$0.8727272727272727"],
    [0.0000004, "$0.0000004"],
    [0.00000004123, "$0.00000004123"],
    [0.9999999, "$0.9999999"],
    [-12.3456, "-$12.3456"],
    [-0.0876869, "-$0.0876869"],
    // R3-02: caps share lower bounds' grouped dollar display.
    [1000, "$1,000.00"],
    [1234.561, "$1,234.561"],
    [999.999, "$999.999"],
    [-1234.561, "-$1,234.561"],
    [1e21, "$1,000,000,000,000,000,000,000.00"],
    [0, "$0.00"],
    [null, EMPTY],
    ["invalid", EMPTY],
  ])("formats the upper bound %s as %s", (value, expected) => {
    expect(formatCostUpperBound(value)).toBe(expected);
  });
});

describe("formatCostCell (grids)", () => {
  it("reads <$0.0001 under a hundredth of a cent, with the exact value in the tooltip", () => {
    expect(formatCostCell(0.000004)).toBe("$0.000004");
    expect(costTooltip(0.000004)).toBe(null);
    expect(formatCostCell(-0.000004)).toBe("-$0.000004");
    expect(costTooltip(-0.000004)).toBe(null);
  });

  // One significant-figure rule per cost column. A Sessions
  // column mixed $0.00027, $0.001898 and $0.0006 (2 to 4 significant
  // figures); under a dollar every cell now has exactly two, from a dollar
  // up the cents, and the tooltip keeps the exact amount.
  it("reads two significant figures under a dollar, the exact amount in the tooltip", () => {
    const column = [0.00027, 0.001898, 0.0006, 0.00116, 0.0001, 0.5, 0.012];
    expect(column.map(formatCostCell)).toEqual([
      "$0.00027",
      "$0.001898",
      "$0.0006",
      "$0.00116",
      "$0.0001",
      "$0.50",
      "$0.012",
    ]);
    expect(costTooltip(0.001898)).toBe(null);
    expect(costTooltip(0.00116)).toBe(null);
    // Nothing hidden by the rounding: no tooltip.
    expect(costTooltip(0.00027)).toBeNull();
    expect(costTooltip(0.0006)).toBeNull();
    expect(costTooltip(0.0001)).toBeNull();
  });

  it("reads dollars to the cent, and zero as $0.00, with no tooltip", () => {
    expect(formatCostCell(2.5)).toBe("$2.50");
    expect(formatCostCell(1234.5)).toBe("$1,234.50");
    expect(formatCostCell(0)).toBe("$0.00");
    expect(costTooltip(2.5)).toBeNull();
    expect(costTooltip(0)).toBeNull();
  });

  it("writes a missing amount as an em dash", () => {
    expect(formatCostCell(null)).toBe("—");
    expect(formatCostCell(Number.NaN)).toBe("—");
    expect(costTooltip(null)).toBeNull();
  });
});

describe("chart axis ticks stay compact", () => {
  it("drops zeros past the cents and abbreviates thousands", () => {
    expect(formatCostTick(0)).toBe("$0.00");
    expect(formatCostTick(0.0005)).toBe("$0.0005");
    expect(formatCostTick(0.5)).toBe("$0.50");
    expect(formatCostTick(1500)).toBe("$1500.00");
  });
});

describe("central Billing preserves ledger details", () => {
  it("keeps exact ledger amounts separate from rounded display and shares grid cells", () => {
    // One 4-millionths-of-a-dollar charge read
    // "$0.0000040" in Logs and "-$0.000004" in the Billing ledger.
    expect(formatAmount(-0.000004)).toBe("-$0.000004");
    expect(formatAmount(0.000664)).toBe("$0.000664");
    expect(formatAmountCell(-0.000004)).toBe(formatCostCell(-0.000004));
    expect(formatAmountCell(0.000004)).toBe(formatCostCell(0.000004));
    expect(formatAmount(1234.5)).toBe("$1,234.50");
    expect(formatAmount(null)).toBe("—");
    expect(formatBalance(null)).toBe("—");
  });
});

// Billing's Available read $34.99 while its Closing
// balance and the log drawer's Credit remaining read $35.00 for the same
// 34.99942 the backend sends (agentcc/tests/test_managed_credits_api.py
// TestBalanceAndSubCentMarkup). One balance rule: cut to the cent, never
// rounded up, exact when under a cent.
describe("one rule for a balance", () => {
  it("cuts a balance to the cent, never rounding it up", () => {
    expect(gatewayBalance(34.99942)).toBe("$34.99942");
    expect(gatewayBalance("34.999420")).toBe("$34.99942");
    expect(gatewayBalance(35)).toBe("$35.00");
    expect(gatewayBalance(1234.567)).toBe("$1,234.567");
    expect(gatewayBalance(-1.239)).toBe("-$1.239");
    expect(gatewayBalance(0.000336)).toBe("$0.000336");
    expect(gatewayBalance(null)).toBe(EMPTY);
  });

  it("is central Billing's balance rule", () => {
    expect(formatBalance).toBe(gatewayBalance);
  });

  it("gives the exact balance when the figure shows less", () => {
    expect(balanceTooltip(34.99942)).toBe(null);
    expect(balanceTooltip(35)).toBeNull();
    expect(balanceTooltip(0.000336)).toBeNull();
    expect(balanceTooltip(null)).toBeNull();
  });
});

describe("the other gateway formatters mark a missing value with the same dash", () => {
  it("formatNumber, formatPercent and formatLatency return the em dash", () => {
    expect(formatNumber(null)).toBe("—");
    expect(formatPercent(undefined)).toBe("—");
    expect(formatLatency(null)).toBe("—");
    expect(formatNumber("x")).toBe("—");
  });
});
