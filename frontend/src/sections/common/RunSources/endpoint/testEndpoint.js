// `Test on one row` for an HTTP endpoint (options-truth L1 G5; W6 B1/B2).
// The request is the run's own (requestModel.buildRequest), and the reply
// goes through the card's transform (transformModel.applyTransform).
// - A URL on your machine (127.0.0.1 / localhost) is called by your browser
//   for real. A browser cannot read workspace secrets, so secret headers
//   are left out and the result says so.
// - Any other host is answered by Future AGI's endpoint test, which sends
//   the secret headers (shown masked) and stands in for that host.
// - A secret that does not exist blocks the Test before anything is sent.
// Each Test sends a W3C `traceparent` of its own; the result shows it.
import axios from "src/utils/axios";
import { isLocalUrl } from "./endpointModel";
import {
  browserHeaders,
  browserSecretNote,
  buildRequest,
  headerLines,
  missingSecretMessage,
  missingSecrets,
  parseTraceparent,
  valuesById,
} from "./requestModel";
import { applyTransform, transformOf } from "./transformModel";

const ENDPOINT_TEST = "/model-hub/r4/endpoints/test/";

const hexOf = (n) => {
  const bytes = new Uint8Array(n);
  globalThis.crypto.getRandomValues(bytes);
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
};

/** A fresh trace context for one Test (16-byte trace id, 8-byte span id). */
export const newTraceContext = () => ({ traceId: hexOf(16), spanId: hexOf(8) });

/** The card's endpoint fields in the run's shape. */
export function endpointFields(p) {
  return {
    url: String(p.url ?? "").trim(),
    method: String(p.method || "POST").toUpperCase(),
    headers: p.headers ?? [],
    body_template: p.bodyTemplate ?? p.body_template ?? "",
    output_path: p.outputPath ?? p.output_path ?? "",
    transform: p.transform ?? null,
  };
}

/**
 * @param {{url, method, headers, bodyTemplate, outputPath, transform,
 *   row, columns, mapping, datasetId, rowId, secretNames?: string[]}} p
 * @returns {Promise<object>} `{status, ms, request, body, output,
 *   tool_calls, usage, error, simulated, host, note, traceparent, echoed}`
 */
export async function testEndpoint(p) {
  const endpoint = endpointFields(p);
  const transform = transformOf(endpoint);
  if (p.secretNames) {
    const msg = missingSecretMessage(
      missingSecrets(
        (endpoint.headers ?? []).map((h) => h?.secret).filter(Boolean),
        p.secretNames,
      ),
    );
    if (msg) return { error: msg, blocked: true };
  }
  const traceContext = newTraceContext();
  if (isLocalUrl(endpoint.url)) {
    // Values by column id from the row; a caller that only has values by
    // column name (`values`) reads them as columns named so.
    const byName = !p.row && p.values ? p.values : null;
    const columns = byName
      ? Object.keys(byName).map((n) => ({ id: n, name: n }))
      : p.columns ?? [];
    const req = buildRequest({
      endpoint,
      mapping: byName ? {} : p.mapping ?? {},
      values: byName ?? valuesById(p.row, columns),
      columns,
      rowId: p.rowId ?? p.row?.row_id ?? byName?.row_id ?? null,
      trial: 1,
      traceContext,
    });
    const started = performance.now();
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 15000);
    try {
      const res = await fetch(req.url, {
        method: req.method,
        headers: { "Content-Type": "application/json", ...browserHeaders(req) },
        body: req.method === "GET" ? undefined : req.body,
        signal: ctrl.signal,
      });
      const text = await res.text();
      let data = null;
      try {
        data = JSON.parse(text);
      } catch {
        data = null;
      }
      const t = data ? applyTransform(data, transform) : null;
      const echoedRaw =
        res.headers?.get?.("traceparent") ?? data?.traceparent ?? null;
      return {
        simulated: false,
        status: res.status,
        ms: performance.now() - started,
        request: {
          method: req.method,
          url: req.url,
          headers: headerLines(req, { from: "browser" }),
          body: req.body,
        },
        note: browserSecretNote(req),
        traceparent: req.traceparent,
        echoed: parseTraceparent(echoedRaw) ? echoedRaw : null,
        body: data ? JSON.stringify(data, null, 2) : text.slice(0, 2000),
        output: t?.output ?? undefined,
        tool_calls: t?.tool_calls ?? null,
        usage: t?.usage ?? null,
        error: !data
          ? "The response is not JSON"
          : t.errors.length
            ? t.errors[0]
            : null,
        missing: req.missing,
        path: transform.output,
      };
    } catch (e) {
      return {
        error:
          e?.name === "AbortError"
            ? `No answer from ${req.url} in 15 s`
            : `Couldn't reach ${req.url} from your browser: is it running, and does it allow CORS?`,
      };
    } finally {
      clearTimeout(timer);
    }
  }
  const res = await axios.post(ENDPOINT_TEST, {
    url: endpoint.url,
    method: endpoint.method,
    headers: endpoint.headers,
    body_template: endpoint.body_template,
    output_path: endpoint.output_path,
    transform: endpoint.transform,
    mapping: p.mapping ?? {},
    dataset_id: p.datasetId ?? null,
    row_id: p.rowId ?? null,
    trace_context: traceContext,
  });
  const r = res?.data?.result ?? {};
  return {
    simulated: true,
    host: r.host,
    status: r.status,
    ms: r.latency_ms,
    request: r.request,
    traceparent: r.traceparent ?? null,
    echoed: null,
    // L7c g2: nothing answered (an unreachable host): no response to show.
    body: r.response == null ? null : JSON.stringify(r.response, null, 2),
    output: r.output ?? undefined,
    tool_calls: r.tool_calls ?? null,
    usage: r.usage ?? null,
    error: r.error ?? null,
    spans: r.spans ?? [],
    path: transform.output,
    row_no: r.row_no ?? null,
  };
}
