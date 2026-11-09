import { formatExactMoney } from "src/sections/common/format/money";

// Retain the plain display's padding, including $1000.00 without grouping.
// This is a MINIMUM only: authoritative decimal digits are never discarded.
export function minimumUsdCostFractionDigits(value) {
  const amount = Math.abs(Number(value));
  return amount === 0 ? 2 : amount < 0.0001 ? 0 : amount < 0.01 ? 4 : 2;
}

export function formatUsdCost(value, missing = "-") {
  return formatExactMoney(value, {
    missing,
    minimumFractionDigits: minimumUsdCostFractionDigits(value),
  });
}
