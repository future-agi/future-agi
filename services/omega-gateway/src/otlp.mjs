// OTLP/HTTP JSON span export to a Future AGI Observe project (CP P9, D16: `omega-console-ops`).
//
// One SERVER span per request, in the trace the browser, Django and EE share (trace.mjs).
// Attributes carry the route template, status, rate class, refusal reason, phase durations
// and hashed org and workspace; never a raw id, path, query, header or body. Export never
// blocks a request: spans queue in a bounded buffer, are sent in batches, and are dropped
// (and counted) when the buffer is full or the collector is down.
import { accessRecord } from "./trace.mjs";

const MAX_QUEUE = 4_096;
const BATCH = 256;
const FLUSH_MS = 2_000;
const EXPORT_TIMEOUT_MS = 3_000;

const str = (key, value) => ({ key, value: { stringValue: String(value) } });
const int = (key, value) => ({
  key,
  value: { intValue: String(Math.round(value)) },
});
const dbl = (key, value) => ({ key, value: { doubleValue: Number(value) } });

/** The OTLP span for one finished request context. */
export function spanOf(context, endUnixNano = BigInt(Date.now()) * 1_000_000n) {
  const record = accessRecord(context);
  const attributes = [
    str("http.request.method", record.method),
    str("http.route", record.route),
    str("omega.listener", record.listener),
  ];
  if (record.status !== undefined)
    attributes.push(int("http.response.status_code", record.status));
  if (record.rateClass)
    attributes.push(str("omega.rate_class", record.rateClass));
  if (record.rateStore)
    attributes.push(str("omega.rate_store", record.rateStore));
  if (record.reason)
    attributes.push(str("omega.refusal_reason", record.reason));
  if (record.org) attributes.push(str("omega.org_hash", record.org));
  if (record.workspace)
    attributes.push(str("omega.workspace_hash", record.workspace));
  if (record.mintMs !== undefined)
    attributes.push(dbl("omega.mint_ms", record.mintMs));
  if (record.eeMs !== undefined)
    attributes.push(dbl("omega.ee_ms", record.eeMs));
  const span = {
    traceId: context.traceId,
    spanId: context.spanId,
    name: `${record.method} ${record.route}`,
    kind: 2,
    startTimeUnixNano: String(context.startedAtUnixNano),
    endTimeUnixNano: String(endUnixNano),
    attributes,
    status:
      record.status >= 500
        ? { code: 2, message: record.reason || "server error" }
        : { code: 0 },
  };
  if (context.parentSpanId) span.parentSpanId = context.parentSpanId;
  return span;
}

export function exportRequest(spans, { projectName, serviceName }) {
  return {
    resourceSpans: [
      {
        resource: {
          attributes: [
            str("service.name", serviceName),
            str("project_name", projectName),
            str("project_type", "observe"),
          ],
        },
        scopeSpans: [{ scope: { name: "omega-gateway", version: "1" }, spans }],
      },
    ],
  };
}

/**
 * @param {{ url: string, credentials: { apiKey: string, secretKey: string } | null, projectName: string, serviceName: string } | null} config
 * @param {{ fetchImpl?: typeof fetch, log?: (event: object) => void }} [options]
 */
export function createSpanExporter(
  config,
  { fetchImpl = globalThis.fetch, log = () => {} } = {},
) {
  const stats = { queued: 0, exported: 0, dropped: 0, failedBatches: 0 };
  if (!config) {
    return {
      stats,
      enabled: false,
      record() {},
      async flush() {},
      async shutdown() {},
    };
  }
  let queue = [];
  let inFlight = null;
  let lastFailureLog = 0;
  const timer = setInterval(() => {
    flush().catch(() => {});
  }, FLUSH_MS);
  timer.unref?.();
  const headers = { "content-type": "application/json" };
  if (config.credentials) {
    headers["X-Api-Key"] = config.credentials.apiKey;
    headers["X-Secret-Key"] = config.credentials.secretKey;
  }

  async function send(batch) {
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), EXPORT_TIMEOUT_MS);
    try {
      const response = await fetchImpl(config.url, {
        method: "POST",
        redirect: "manual",
        headers,
        body: JSON.stringify(exportRequest(batch, config)),
        signal: controller.signal,
      });
      await response.arrayBuffer().catch(() => {});
      if (!response.ok)
        throw new Error(`collector answered ${response.status}`);
      stats.exported += batch.length;
    } catch (error) {
      stats.failedBatches += 1;
      stats.dropped += batch.length;
      const now = Date.now();
      if (now - lastFailureLog > 60_000) {
        lastFailureLog = now;
        log({
          event: "omega-gateway.otlp.export_failed",
          spans: batch.length,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    } finally {
      clearTimeout(deadline);
    }
  }

  async function flush() {
    if (inFlight) await inFlight;
    while (queue.length > 0) {
      const batch = queue.slice(0, BATCH);
      queue = queue.slice(BATCH);
      inFlight = send(batch);
      await inFlight;
      inFlight = null;
    }
  }

  return {
    stats,
    enabled: true,
    record(context) {
      if (!context.sampled) return;
      if (queue.length >= MAX_QUEUE) {
        stats.dropped += 1;
        return;
      }
      queue.push(spanOf(context));
      stats.queued += 1;
      if (queue.length >= BATCH && !inFlight) flush().catch(() => {});
    },
    flush,
    async shutdown() {
      clearInterval(timer);
      await flush();
    },
  };
}
