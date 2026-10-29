/**
 * Formatting utilities for trace list cell renderers.
 * Pure functions — no React, no side effects.
 */

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

// Costs below this round to $0.0000000 at seven decimals.
const SMALLEST_SHOWN_COST = 0.00000005;

/**
 * Format a cost value into a dollar string.
 *
 * Below $0.0001, four decimals would round a real cost to "$0.0000" (a
 * 40-token OpenRouter call costs about $0.00002), so those costs get the
 * seven decimals the trace list's Total Cost cell and the gateway use, with
 * trailing zeros dropped; anything that would still round to zero reads
 * "< $0.0000001".
 * @param {number|null} value - Cost in dollars
 * @returns {string} Formatted cost ("$0.00", "$0.000024", "$0.0015", "$1.23", or "-")
 */
export function formatCost(value) {
  if (value == null || isNaN(value)) return "-";
  if (value === 0) return "$0.00";
  const abs = Math.abs(value);
  if (abs < SMALLEST_SHOWN_COST) return "< $0.0000001";
  if (abs < 0.0001) return `$${value.toFixed(7).replace(/0+$/, "")}`;
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
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
