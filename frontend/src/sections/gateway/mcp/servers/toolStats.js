// Per-tool call stats for the Tools list (v1.58.0's Tools tab columns), from
// each tool's `stats` on the mcp-tools read: this organization's stored
// tool calls of the last `window_days` days (agentcc/services/mcp_tool_stats.py).
// A tool with no `stats` (a list saved before the read sent them) shows dashes.

const DASH = "—";

export const STATS_WINDOW_NOTE =
  "Last 30 days, from this organization's stored tool calls. Denied calls are not counted.";

function stat(data, key) {
  const value = data?.stats?.[key];
  return value === undefined ? null : value;
}

function hasCalls(data) {
  return Number(stat(data, "call_count")) > 0;
}

export function formatCount(value) {
  return Number.isFinite(value) ? value.toLocaleString("en-US") : DASH;
}

export function formatLatency(value) {
  return Number.isFinite(value) ? `${value.toFixed(1)} ms` : DASH;
}

// `error_rate` is a fraction (0 to 1); a tool with no calls has no rate.
export function formatErrorRate(value, data) {
  return Number.isFinite(value) && hasCalls(data)
    ? `${(value * 100).toFixed(1)}%`
    : DASH;
}

export function formatLastCalled(value, now = Date.now()) {
  if (!value) return DASH;
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return DASH;
  const ago = now - at.getTime();
  if (ago < 60_000) return "just now";
  if (ago < 3_600_000) return `${Math.floor(ago / 60_000)}m ago`;
  if (ago < 86_400_000) return `${Math.floor(ago / 3_600_000)}h ago`;
  return at.toLocaleDateString("en-US");
}

const statColumn = (headerName, colId, key, valueFormatter, minWidth) => ({
  headerName,
  colId,
  valueGetter: ({ data }) => stat(data, key),
  valueFormatter,
  headerTooltip: STATS_WINDOW_NOTE,
  minWidth,
  flex: 0.6,
});

// AG Grid columns for ToolsGrid's `statsColumns` slot.
export const TOOL_STATS_COLUMNS = [
  statColumn(
    "Calls (30 days)",
    "calls",
    "call_count",
    ({ value }) => formatCount(value),
    130,
  ),
  statColumn(
    "Errors",
    "errors",
    "error_count",
    ({ value }) => formatCount(value),
    90,
  ),
  statColumn(
    "Avg latency",
    "avg_latency",
    "avg_latency_ms",
    ({ value }) => formatLatency(value),
    110,
  ),
  statColumn(
    "Error rate",
    "error_rate",
    "error_rate",
    ({ value, data }) => formatErrorRate(value, data),
    100,
  ),
  {
    ...statColumn(
      "Last called",
      "last_called",
      "last_called",
      ({ value }) => formatLastCalled(value),
      120,
    ),
    // The exact time on hover.
    tooltipValueGetter: ({ data }) => stat(data, "last_called") || undefined,
  },
];
