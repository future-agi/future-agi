// The Providers list: one row per org provider (the gateway config read is
// the source, the org config the fallback), with the org's own last-24-hour
// numbers and status from the providers read merged in by provider id. The
// operator's circuit breaker is never carried: it is not an org provider's.

const DASH = "—";

// The status the providers read derives from the org's own requests in the
// last 24 hours (views/gateway.py providers): Unhealthy at 50% failed or
// more, Degraded at 10% or more, counted from 5 requests. Under 5 requests
// the read still answers healthy; the console reads that as "Too few
// requests", in neutral, never a Healthy claim. A provider the config push
// leaves out (pushed: false) reads "Not pushed", never a health status.
export const MIN_STATUS_REQUESTS = 5;

export const PROVIDER_STATUS = Object.freeze({
  healthy: { label: "Healthy", tone: "healthy" },
  too_few: { label: "Too few requests", tone: "neutral" },
  degraded: { label: "Degraded", tone: "warning" },
  unhealthy: { label: "Unhealthy", tone: "error" },
  not_pushed: { label: "Not pushed", tone: "error" },
  unknown: { label: "Unknown", tone: "neutral" },
});

// `managedOffered` false: the deployment offers no managed models, so the
// rule has no managed requests to leave out.
export function providerStatusRule({ managedOffered = true } = {}) {
  return (
    "Status comes from your organization's own requests to each provider in the last 24 hours: " +
    "Unhealthy at 50% failed or more, Degraded at 10% or more, counted from 5 requests; " +
    "fewer than 5 requests read as Too few requests. " +
    (managedOffered
      ? "Guardrail blocks and requests served on Future AGI credits are not counted. "
      : "Guardrail blocks are not counted. ") +
    "The gateway's circuit breakers belong to the operator's providers and are not shown here."
  );
}

// The status key for one provider's entry in the providers read; with no
// entry it is unknown.
export function providerStatusKey(stats) {
  if (!stats) return "unknown";
  if (stats.pushed === false) return "not_pushed";
  let key = "unknown";
  if (stats.status) key = PROVIDER_STATUS[stats.status] ? stats.status : key;
  else if (stats.healthy === true) key = "healthy";
  else if (stats.healthy === false) key = "unhealthy";
  // Healthy is measured from 5 requests: fewer is too few to tell.
  const requests = Number(stats.request_count);
  if (
    key === "healthy" &&
    !(Number.isFinite(requests) && requests >= MIN_STATUS_REQUESTS)
  ) {
    return "too_few";
  }
  return key;
}

function healthIndex(providerHealth) {
  const list = providerHealth?.providers;
  const index = {};
  if (Array.isArray(list)) {
    list.forEach((p) => {
      const id = p?.id || p?.name || p?.provider_name;
      if (id) index[id] = p;
    });
  } else if (list && typeof list === "object") {
    Object.entries(list).forEach(([id, p]) => {
      index[id] = p || {};
    });
  }
  return index;
}

const num = (value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

export function buildProviderRows(config, orgConfig, providerHealth) {
  const providers = config?.providers || orgConfig?.providers || {};
  if (!providers || typeof providers !== "object" || Array.isArray(providers)) {
    return [];
  }
  const stats = healthIndex(providerHealth);
  return Object.entries(providers).map(([name, cfg = {}]) => {
    const s = stats[name] || {};
    const status = providerStatusKey(stats[name]);
    const requests = num(s.request_count) ?? 0;
    const models = Array.isArray(cfg.models)
      ? cfg.models
      : Array.isArray(s.models)
        ? s.models
        : [];
    return {
      id: name,
      name,
      displayName: (cfg.display_name || cfg.displayName || "").trim() || name,
      baseUrl: cfg.base_url ?? cfg.baseUrl ?? s.base_url ?? "",
      apiFormat: cfg.api_format ?? cfg.apiFormat ?? s.api_format ?? "",
      apiPathPrefix: cfg.api_path_prefix ?? cfg.apiPathPrefix ?? null,
      models,
      timeout: cfg.default_timeout ?? cfg.defaultTimeout ?? null,
      maxConcurrent: cfg.max_concurrent ?? cfg.maxConcurrent ?? null,
      connPoolSize: cfg.conn_pool_size ?? cfg.connPoolSize ?? null,
      requests,
      // No requests means no figure, never a 0 ms latency or a 0 % error rate.
      avgLatencyMs:
        requests > 0 ? num(s.avg_latency ?? s.avg_latency_ms) : null,
      errorRate: requests > 0 ? num(s.error_rate) : null,
      status,
      statusReason: typeof s.status_reason === "string" ? s.status_reason : "",
      config: cfg,
    };
  });
}

/** "api.openai.com/v1": the base URL without its scheme or trailing slash. */
export function endpointLabel(baseUrl) {
  const text = String(baseUrl || "").trim();
  if (!text) return DASH;
  return text.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "").replace(/\/+$/, "");
}

export function formatLatency(ms) {
  if (ms == null) return DASH;
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  return `${Math.round(ms)} ms`;
}

export function formatErrorRate(rate) {
  if (rate == null) return DASH;
  return `${Number(rate).toFixed(1)}%`;
}

export function formatCount(value) {
  return Number(value || 0).toLocaleString("en-US");
}

export function formatSeconds(value) {
  if (value == null || value === "") return DASH;
  const n = Number(value);
  return Number.isFinite(n) ? `${n} s` : String(value);
}

export function matchesProviderSearch(row, query) {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [row.name, row.displayName, row.baseUrl, row.apiFormat, ...row.models]
    .filter(Boolean)
    .some((text) => String(text).toLowerCase().includes(q));
}
