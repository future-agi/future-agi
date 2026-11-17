/**
 * Formatting utilities for trace list cell renderers.
 * Pure functions — no React, no side effects.
 */
import { formatUsdCost } from "src/utils/format-usd-cost";

/**
 * Format milliseconds into a human-readable latency string.
 * @param {number|null} ms - Latency in milliseconds
 * @returns {string} Formatted latency ("500ms", "1.5s", "1m 5s", or "-")
 */
export function formatLatency(ms) {
  if (ms == null || isNaN(ms)) return "-";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.round((ms % 60000) / 1000);
  return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
}

/**
 * Drop binary float noise from a cost that arrives as a JSON number: the
 * twelve-significant-digit rule formatCost explains below. The trace and span
 * grids' cost cell uses it too, so grid and drawer read the same amount.
 * Decimal strings and other values pass through unchanged.
 * @param {number|string|null} value - Cost in USD
 * @returns {number|string|null} The value, a float cut to 12 digits
 */
export function withoutFloatNoise(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? Number(value.toPrecision(12))
    : value;
}

/**
 * Format a USD cost for the trace, session and user views.
 * A cost that arrives as a JSON number is a float, and a summed one (a trace,
 * session or user total) carries binary noise such as 0.000042000000000000004.
 * Twelve significant digits drop that noise and keep every sub-cent digit a
 * price can produce: the same rule as the partial-cost label
 * (formatKnownSubtotal). Decimal strings keep every digit.
 * @param {number|string|null} value - Cost in USD
 * @param {string} [missing] - Text for a missing cost (defaults to "-")
 * @returns {string} Formatted cost ("$0.000042", "$1.20", or "-")
 */
export function formatCost(value, missing) {
  return formatUsdCost(withoutFloatNoise(value), missing);
}

/**
 * Format a token count into a readable string.
 * @param {number|null} count - Token count
 * @returns {string} Formatted count ("1,234", "12.3K", or "-")
 */
export function formatTokenCount(count) {
  if (count == null || isNaN(count)) return "-";
  if (count >= 10000) return `${(count / 1000).toFixed(1)}K`;
  return count.toLocaleString();
}
