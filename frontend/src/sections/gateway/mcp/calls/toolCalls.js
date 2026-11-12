import {
  formatExactMoney,
  multiplyMoneyValues,
} from "src/sections/common/format/money";
// Pure helpers for the Tool calls view: the API's filters, its cursor, and
// how a recorded call reads.

import { billingModeLabel } from "../../utils/providerLabel";

export const PAGE_SIZE = 50;

// Outcomes of an mcp-tool-call-v1 record (agentcc/models/mcp_tool_call.py).
export const OUTCOMES = [
  { value: "success", label: "Success", tone: "healthy" },
  { value: "is_error", label: "Tool error", tone: "warning" },
  { value: "denied", label: "Denied", tone: "error" },
  { value: "error", label: "Error", tone: "error" },
  { value: "timeout", label: "Timed out", tone: "error" },
  { value: "cancelled", label: "Cancelled", tone: "neutral" },
];

export function outcomeOf(value) {
  return (
    OUTCOMES.find((outcome) => outcome.value === value) || {
      value,
      label: value ? String(value) : "Unknown",
      tone: "neutral",
    }
  );
}

export const RANGES = [
  { value: "1h", label: "Last hour", ms: 60 * 60 * 1000 },
  { value: "24h", label: "Last 24 hours", ms: 24 * 60 * 60 * 1000 },
  { value: "7d", label: "Last 7 days", ms: 7 * 24 * 60 * 60 * 1000 },
  { value: "all", label: "All time", ms: null },
];

// Latency floors the list can filter on (min_latency_ms, in ms).
export const LATENCIES = [
  { value: "100", label: "Over 100 ms" },
  { value: "1000", label: "Over 1 s" },
  { value: "5000", label: "Over 5 s" },
];

export const EMPTY_FILTERS = Object.freeze({
  runId: "",
  agentId: "",
  apiKeyId: "",
  billingMode: "",
  serverId: "",
  tool: "",
  outcome: "",
  minLatency: "",
  range: "all",
});

export function hasFilters(filters) {
  return Boolean(
    filters.runId ||
      filters.agentId ||
      filters.apiKeyId ||
      filters.billingMode ||
      filters.serverId ||
      filters.tool ||
      filters.outcome ||
      filters.minLatency ||
      (filters.range && filters.range !== "all"),
  );
}

// The query the list API takes (agentcc/views/mcp_tool_calls.py); it has no
// virtual-server filter, so none is sent.
export function toolCallParams(filters, now = new Date()) {
  const params = { limit: PAGE_SIZE };
  if (filters.runId) params.run_id = filters.runId;
  if (filters.agentId) params.agent_id = filters.agentId;
  if (filters.apiKeyId) params.api_key_id = filters.apiKeyId;
  if (filters.billingMode) params.billing_mode = filters.billingMode;
  if (filters.serverId) params.server_id = filters.serverId;
  if (filters.tool) params.tool = filters.tool;
  if (filters.outcome) params.outcome = filters.outcome;
  if (filters.minLatency) params.min_latency_ms = Number(filters.minLatency);
  const range = RANGES.find((r) => r.value === filters.range);
  if (range?.ms) params.from = new Date(now.getTime() - range.ms).toISOString();
  return params;
}

// The export's query: the list's filters, without its page size or cursor.
export function toolCallExportParams(filters, now = new Date()) {
  // eslint-disable-next-line no-unused-vars
  const { limit, ...params } = toolCallParams(filters, now);
  return params;
}

// DRF cursor pagination gives the next page as a full URL.
export function cursorFrom(nextUrl) {
  if (!nextUrl) return undefined;
  try {
    return (
      new URL(nextUrl, "http://localhost").searchParams.get("cursor") ??
      undefined
    );
  } catch {
    return undefined;
  }
}

// vs_ id -> the virtual server's title (or slug), from the org's mcp config.
export function virtualServerNames(mcp) {
  const names = {};
  const servers = mcp?.virtual_servers;
  if (!servers || typeof servers !== "object") return names;
  Object.entries(servers).forEach(([slug, entry]) => {
    if (entry?.id) names[entry.id] = entry.title || slug;
  });
  return names;
}

// The tool's own name: the upstream one when the record has it, else the
// exposed name without its "<server>__" prefix.
export function toolLabel(call) {
  if (call?.upstream_tool) return call.upstream_tool;
  const tool = call?.tool || "";
  const prefix = `${call?.server_id}__`;
  return call?.server_id && tool.startsWith(prefix)
    ? tool.slice(prefix.length)
    : tool;
}

// Integer micros (USD x 1e6) as dollars; zero reads as $0.
export function formatMicros(micros) {
  return formatExactMoney(multiplyMoneyValues(micros, "0.000001"), {
    minimumFractionDigits: Number(micros) === 0 ? 0 : 2,
  });
}

export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return "—";
  const value = Number(bytes);
  if (Number.isNaN(value)) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

/** A call's billing mode by its one name ("Managed", "Own key"), or "—". */
export function billingLabel(mode) {
  return billingModeLabel(mode) || "—";
}

/** A call's time to the second, the same in the list and the drawer. */
export function formatCallTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
