// The Logs page's quick filters and the chips that name every other applied
// filter, so a deep link (from a run, a session or a key) reads as what it is
// and each part can be removed on its own.

import { sectionEnforcement } from "../settings/sectionEnforcement";
import { BILLING_MODE_LABEL, billingModeLabel } from "../utils/providerLabel";
import { formatLogTime, parseFilterTime } from "./logDates";

export const QUICK_FILTERS = [
  { key: "all", label: "All" },
  { key: "errors", label: "Errors", filter: ["isError", "true"] },
  { key: "slow", label: "Slow > 1 s", filter: ["minLatency", "1000"] },
  { key: "cache", label: "Cache hits", filter: ["cacheHit", "true"] },
  {
    key: "guardrails",
    label: "Guardrails",
    filter: ["guardrailTriggered", "true"],
  },
  { key: "fallbacks", label: "Fallbacks", filter: ["fallbackUsed", "true"] },
];

// The quick filters for a feature this gateway does not run are not offered
// (they could match nothing): Guardrails while its guardrail pipeline is off
// (guardrail_pipeline.enabled false) and Cache hits while it runs no response
// cache (cache missing from org_sections_enforced). Nothing is hidden while
// the gateway config loads or when it does not say.
export function hiddenQuickFilters(gatewayConfigQuery) {
  const hidden = new Set();
  const data = gatewayConfigQuery?.isLoading ? null : gatewayConfigQuery?.data;
  if (!data) return hidden;
  if (data.guardrail_pipeline?.enabled === false) hidden.add("guardrails");
  if (sectionEnforcement(gatewayConfigQuery, "cache") === "not_enforced") {
    hidden.add("cache");
  }
  return hidden;
}

const QUICK_KEYS = new Set(
  QUICK_FILTERS.filter((q) => q.filter).map((q) => q.filter[0]),
);

/** The quick filter the filters match exactly (or "all" when none applies). */
export function activeQuickFilter(filters) {
  const match = QUICK_FILTERS.find(
    (q) => q.filter && filters[q.filter[0]] === q.filter[1],
  );
  return match ? match.key : "all";
}

/** Filters with every quick-filter key replaced by this quick filter's. */
export function withQuickFilter(filters, quick) {
  const next = {};
  Object.entries(filters).forEach(([key, value]) => {
    if (QUICK_KEYS.has(key) || key === "page") return;
    next[key] = value;
  });
  if (quick.filter) next[quick.filter[0]] = quick.filter[1];
  return next;
}

// Not about which rows match.
const NOT_FILTERS = new Set([
  "view",
  "page",
  "pageSize",
  "sort",
  "search",
  "log",
]);

const LABELS = {
  model: "Model",
  provider: "Provider",
  application: "Application",
  service: "Service",
  tags: "Tags",
  startedAfter: "From",
  startedBefore: "To",
  isError: "Errors",
  cacheHit: "Cache hits",
  guardrailTriggered: "Guardrails",
  fallbackUsed: "Fallbacks",
  isStream: "Streaming",
  minLatency: "Latency ≥",
  maxLatency: "Latency ≤",
  minCost: "Cost ≥",
  maxCost: "Cost ≤",
  minTokens: "Tokens ≥",
  maxTokens: "Tokens ≤",
  statusCode: "Status",
  statusCodeMin: "Status ≥",
  statusCodeMax: "Status ≤",
  userId: "User",
  sessionId: "Session",
  apiKeyId: "API key",
  requestId: "Request",
  gatewayId: "Gateway",
  billingMode: BILLING_MODE_LABEL,
  runId: "Run",
  agentId: "Agent",
  step: "Step",
  policyVariant: "Policy variant",
  errorClass: "Error class",
};

const RANGE = new Set([
  "minLatency",
  "maxLatency",
  "minCost",
  "maxCost",
  "minTokens",
  "maxTokens",
  "statusCodeMin",
  "statusCodeMax",
]);

// Values a chip names as the screens do, not as the URL carries them: the
// managed route's Open logs link applies billing_mode=managed, which reads
// "Billing mode: Managed".
const VALUE_LABELS = { billingMode: billingModeLabel };

function chipLabel(key, value) {
  const label = LABELS[key] || key;
  if (value === "true") return label;
  if (value === "false") return `${label}: no`;
  if (key === "startedAfter" || key === "startedBefore") {
    const time = parseFilterTime(value);
    return `${label}: ${time ? formatLogTime(time).replace(/\.\d{3}$/, "") : value}`;
  }
  if (RANGE.has(key)) {
    const unit = key.endsWith("Latency") ? " ms" : "";
    const money = key.endsWith("Cost") ? "$" : "";
    return `${label} ${money}${value}${unit}`;
  }
  const name = VALUE_LABELS[key] || ((part) => part);
  return `${label}: ${String(value)
    .split(",")
    .map((part) => name(part))
    .join(", ")}`;
}

/** One chip per applied filter the quick filters do not already show. */
export function appliedFilterChips(filters) {
  const quick = activeQuickFilter(filters);
  const quickKey = QUICK_FILTERS.find((q) => q.key === quick)?.filter?.[0];
  return Object.entries(filters)
    .filter(
      ([key, value]) =>
        !NOT_FILTERS.has(key) &&
        key !== quickKey &&
        value !== "" &&
        value != null,
    )
    .map(([key, value]) => ({ key, label: chipLabel(key, value) }));
}
