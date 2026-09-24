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
  // Round to whole seconds before splitting, so 119.6s reads "2m", not "1m 60s".
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
}

/**
 * Format a cost value into a dollar string.
 * @param {number|null} value - Cost in dollars
 * @returns {string} Formatted cost ("$0.00", "$0.0015", "$1.23", or "-")
 */
export function formatCost(value) {
  if (value == null || isNaN(value)) return "-";
  if (value === 0) return "$0.00";
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
