// Money in the managed-credit ledger is exact to six decimal places (the
// ledger's integer micros). It is written by the gateway's one money rule
// (sections/gateway/utils/formatters.js), so a charge reads the same here as
// in the gateway's Logs, drawers and statements: exact in a single figure,
// "<$0.0001" in a grid cell (exact in its tooltip), an em dash when missing;
// a balance is cut to the cent (formatBalance).

import {
  balanceTooltip,
  costTooltip,
  formatBalance,
  formatCostCell,
  formatExactCost,
} from "src/sections/gateway/utils/formatters";

/** A ledger amount: exact below a dollar, two decimals from a dollar up. */
export const formatAmount = formatExactCost;

/** A ledger amount in a grid cell ("<$0.0001" under a hundredth of a cent). */
export const formatAmountCell = formatCostCell;

/** The exact amount for a grid cell's tooltip when the cell shows less. */
export const amountTooltip = costTooltip;

/**
 * A balance: cut (never rounded up) to the cent, so it never overstates. The
 * gateway's one balance rule, so Available, the statement's balances, the
 * Credits chip and the log drawer's Credit remaining read alike.
 */
export { formatBalance, balanceTooltip };

/** The last `count` calendar months (UTC), newest first, as YYYY-MM. */
export function recentMonths(count = 12, now = new Date()) {
  const months = [];
  let year = now.getUTCFullYear();
  let month = now.getUTCMonth() + 1;
  for (let i = 0; i < count; i += 1) {
    months.push(`${year}-${String(month).padStart(2, "0")}`);
    month -= 1;
    if (month === 0) {
      month = 12;
      year -= 1;
    }
  }
  return months;
}

export function monthLabel(month) {
  const [year, mm] = month.split("-").map(Number);
  return new Date(Date.UTC(year, mm - 1, 1)).toLocaleString("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}
