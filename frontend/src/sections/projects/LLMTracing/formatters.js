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

export { formatUsdCost as formatCost } from "src/utils/format-usd-cost";

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
