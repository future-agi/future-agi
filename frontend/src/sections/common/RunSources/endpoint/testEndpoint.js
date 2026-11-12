// Test and row execution use the same request builder and response transform.
// Public endpoints are called by the core API; only localhost uses browser
// fetch. Header secrets stay references until the core resolves them in the
// authenticated workspace. Provenance comes from the API, not the URL.
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

const httpError = (status) =>
  status != null && (status < 200 || status >= 300)
    ? `Endpoint returned HTTP ${status}. Check the URL, method and header settings, then retry.`
    : null;

const errorText = (value) => {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(errorText).filter(Boolean).join(" ");
  if (value && typeof value === "object")
    return Object.entries(value)
      .map(([field, detail]) => `${field}: ${errorText(detail)}`)
      .join(" ");
  return "";
};

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
        (endpoint.headers ?? []).map((h) => h?.secret ?? h?.secret_ref).filter(Boolean),
        p.secretNames,
      ),
    );
    if (msg) return { error: msg, blocked: true };
  }
  const traceContext = newTraceContext();
  // Build once for both transports. No independent server-side interpolation:
  // URL escaping, JSON types, column paths and constants follow the run model.
  const byName = !p.row && p.values ? p.values : null;
  const columns = byName
    ? Object.keys(byName).map((n) => ({ id: n, name: n }))
    : p.columns ?? [];
  const rowId = p.rowId ?? p.row?.row_id ?? byName?.row_id ?? null;
  const req = buildRequest({
    endpoint,
    mapping: byName ? {} : p.mapping ?? {},
    values: byName ?? valuesById(p.row, columns),
    columns,
    rowId,
    trial: 1,
    traceContext,
  });
  if (req.missing.length)
    return {
      blocked: true,
      error: `Map ${req.missing.join(", ")} to a column or value, then test again.`,
    };
  if (isLocalUrl(req.url)) {
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
      let isJson = true;
      try {
        data = JSON.parse(text);
      } catch {
        isJson = false;
      }
      const t = isJson ? applyTransform(data, transform) : null;
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
        body: isJson ? JSON.stringify(data, null, 2) : text.slice(0, 2000),
        output: t?.output ?? undefined,
        tool_calls: t?.tool_calls ?? null,
        usage: t?.usage ?? null,
        error: httpError(res.status) ??
          (!isJson ? "The response is not JSON" : t.errors[0] ?? null),
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
  let res;
  try {
    res = await axios.post(ENDPOINT_TEST, {
      url: endpoint.url,
      method: endpoint.method,
      headers: endpoint.headers,
      body_template: endpoint.body_template,
      output_path: endpoint.output_path,
      transform: endpoint.transform,
      mapping: p.mapping ?? {},
      dataset_id: p.datasetId ?? null,
      row_id: rowId,
      trace_context: traceContext,
      prepared_request: {
        url: req.url,
        method: req.method,
        headers: req.headers,
        body: req.body,
      },
    });
  } catch (e) {
    // Axios rejects with a spread response body. The panel needs a string,
    // including for serializer errors, and keeps its current configuration.
    throw new Error(
      errorText(e?.result ?? e?.response?.data?.result ?? e?.response?.data?.detail) ||
      e?.message || "Endpoint test failed. Check the settings and retry.",
    );
  }
  const r = res?.data?.result ?? {};
  if (typeof r.simulated !== "boolean")
    return { error: "Endpoint test returned no response provenance. Refresh and retry." };
  const isJson = r.response_is_json ?? r.response != null;
  const t = isJson ? applyTransform(r.response, transform) : null;
  return {
    simulated: r.simulated,
    host: r.host,
    status: r.status,
    ms: r.latency_ms,
    request: r.request,
    traceparent: r.traceparent ?? null,
    echoed: parseTraceparent(r.echoed) ? r.echoed : null,
    body: isJson
      ? JSON.stringify(r.response, null, 2)
      : r.response == null ? null : String(r.response),
    output: t?.output ?? undefined,
    tool_calls: t?.tool_calls ?? null,
    usage: t?.usage ?? null,
    error: r.error ?? httpError(r.status) ??
      (isJson ? t.errors[0] ?? null : "No JSON response. Check the endpoint and retry."),
    spans: r.spans ?? [],
    path: transform.output,
    row_no: r.row_no ?? null,
  };
}
