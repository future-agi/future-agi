import {
  formatExactMoney,
  isMoneyValue,
} from "src/sections/common/format/money";
import { formatUsdCost } from "src/utils/format-usd-cost";

/**
 * Centralized formatting utilities for the gateway dashboard.
 * Single source of truth — do NOT duplicate these in section components.
 */

/** The one mark for a value that is missing or unknown (never 0, "-" or N/A). */
export const EMPTY = "\u2014";

export function formatNumber(value) {
  if (value == null) return EMPTY;
  const num = Number(value);
  if (Number.isNaN(num)) return EMPTY;
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
  if (num >= 1_000) return `${(num / 1_000).toFixed(1)}K`;
  return num.toLocaleString();
}

// MONEY-QUAL-31 add.4: exact primary text, not rounded cells with hover-only detail.
function money(value) {
  return isMoneyValue(value) ? value : null;
}

/** Ledger/cell/balance styles retain grouping without a fractional digit cap. */
export function formatExactCost(value) {
  return formatExactMoney(value, { grouping: true });
}

/** Plain trace/dataset style deliberately remains ungrouped ($1000.00). */
export function formatCost(value) {
  return formatUsdCost(value, EMPTY);
}

export function formatCostCell(value) {
  return formatExactCost(value);
}

/** Exact primary text leaves no concealed digits requiring a tooltip. */
export function costTooltip() {
  return null;
}

export function formatBalance(value) {
  return formatExactCost(value);
}

export function balanceTooltip() {
  return null;
}

/** What a request's cost reads when the gateway found no price for it. */
export const NO_PRICE = "No price found";

/** Whether the gateway found no price for a request (its cost is unknown, not 0). */
export function isUnpriced(log) {
  return [
    log?.cost_source,
    log?.metadata?.cost_source,
    log?.billing?.cost_source,
  ].includes("unpriced");
}

// A floor: the priced part of a total some of whose requests had no price.
const AT_LEAST = "\u2265\u00a0";

// An exact endpoint is itself a valid bound; do not snap stored decimals.
function boundCost(value) {
  return formatExactCost(value);
}

export function formatCostUpperBound(value) {
  return formatExactCost(value);
}

/** "3 of 6 requests had no price found"; "1 request ..." when the count is not known. */
export function unpricedNote(unpriced, requests) {
  const n = Number(unpriced) || 0;
  const total = requests == null || requests === "" ? null : Number(requests);
  const counted = Number.isFinite(total)
    ? `${n.toLocaleString()} of ${total.toLocaleString()} requests`
    : `${n.toLocaleString()} ${n === 1 ? "request" : "requests"}`;
  return `${counted} had no price found`;
}

/**
 * How a cost total reads when the gateway may have found no price for some
 * of its requests (the API's unpriced_requests: each is summed as 0).
 * Unknown is not free:
 * - every request unpriced: EMPTY, titled NO_PRICE, as a Logs row reads;
 * - some, with a priced part of 0: EMPTY too ("≥ $0.00" says nothing),
 *   titled "No price found for 20 of 30 requests";
 * - some: the priced part as a floor ("≥ $0.035"), titled with how many had
 *   none; floored to ledger precision below a dollar, cents above, regardless of the
 *   cell or custom display format (never "≥ <$0.0001");
 * - none: the amount (`cell`: formatCostCell, titled with the exact amount).
 * `format` replaces a fully priced amount's format (a non-zero amount it
 * rounds to zero uses shared display); `of` names the figure in the title
 * ("total", "average", "estimate"). Returns { text, title, unknown }.
 */
export function costTotal(
  value,
  { unpriced, requests, cell = false, format, of = "total" } = {},
) {
  const n = Number(unpriced) || 0;
  const total = requests == null || requests === "" ? null : Number(requests);
  const known = Number.isFinite(total);
  if (n > 0 && known && n >= total) {
    return { text: EMPTY, title: NO_PRICE, unknown: true };
  }
  const amount = money(value);
  if (amount === null) return { text: EMPTY, title: null, unknown: false };
  if (
    n > 0 &&
    formatExactMoney(amount, { minimumFractionDigits: 0 }) === "$0"
  ) {
    const of_ = known
      ? `${n.toLocaleString()} of ${total.toLocaleString()}`
      : n.toLocaleString();
    return {
      text: EMPTY,
      title: `${NO_PRICE} for ${of_} ${n === 1 && !known ? "request" : "requests"}`,
      unknown: true,
    };
  }
  if (n > 0) {
    return {
      text: `${AT_LEAST}${boundCost(amount)}`,
      title: `${unpricedNote(n, total)}, so this ${of} counts only the priced ones.`,
      unknown: false,
    };
  }
  // Callers may no longer silently replace money with a rounded custom label.
  const shown = cell ? formatCostCell(value) : formatCost(value);
  return {
    text: shown,
    title: cell && !format ? costTooltip(value) : null,
    unknown: false,
  };
}

/** Exact ticks need no separate focus-only/hover-only recovery path. */
export function formatCostTick(value) {
  return formatCost(value);
}

export function formatPercent(value, decimals = 1) {
  if (value == null) return EMPTY;
  const num = Number(value);
  if (Number.isNaN(num)) return EMPTY;
  return `${num.toFixed(decimals)}%`;
}

export function formatLatency(ms) {
  if (ms == null) return EMPTY;
  const num = Number(ms);
  if (Number.isNaN(num)) return EMPTY;
  if (num >= 1000) return `${(num / 1000).toFixed(2)}s`;
  return `${Math.round(num)}ms`;
}

export function formatDate(dateStr) {
  if (!dateStr) return EMPTY;
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function formatDateTime(dateStr) {
  if (!dateStr) return EMPTY;
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatTokens(value) {
  if (value == null) return EMPTY;
  const num = Number(value);
  if (Number.isNaN(num)) return EMPTY;
  return num.toLocaleString();
}
