import { isValid, parseISO } from "date-fns";

/**
 * Parse a chart timestamp into epoch milliseconds for plotting.
 *
 * Backend timestamps arrive as ISO-8601 strings with an explicit UTC
 * offset (e.g. "2024-01-01T14:30:00+00:00"). Stripping the offset and
 * feeding the bare string to `new Date()` makes it parse as local time,
 * which shifts every point on the chart for non-UTC viewers. Parsing
 * with `parseISO` preserves the offset so the value is a correct UTC
 * instant; ApexCharts then renders it in the viewer's local timezone.
 *
 * Mirrors `parseTimestampToMs` in the LLMTracing GraphSection
 * (see https://github.com/future-agi/future-agi/pull/1329).
 *
 * @param {string|number|Date|null|undefined} ts
 * @returns {number|null} epoch ms, or null if the value is missing/invalid
 */
export function parseTimestampToMs(ts) {
  if (ts == null) return null;
  const date = typeof ts === "string" ? parseISO(ts) : new Date(ts);
  return isValid(date) ? date.getTime() : null;
}
