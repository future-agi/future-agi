// Request correlation for the gateway (CP P9, OBS2, OBS3; READINESS L13).
//
// Every request gets one W3C trace context. An inbound `traceparent` from the browser is
// continued; otherwise a new trace starts. The gateway's own span id is what it sends to
// Django (the exchange) and to EE as their parent, and the trace id is the request id the
// browser sees (`x-request-id`) and every error body carries (`requestId`), so a reference
// on screen resolves to its trace.
//
// One structured access line per request: route template (never the raw path, which can
// carry ids and file names), status, durations, hashed org and workspace, and the refusal
// reason. Never bodies, tokens, headers or query strings (CP SEC3, SEC8).
import crypto from "node:crypto";

const TRACEPARENT = /^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$/;
const TRACESTATE = /^[\x20-\x7e]{1,512}$/;
const ZERO_TRACE = "0".repeat(32);
const ZERO_SPAN = "0".repeat(16);

export function parseTraceparent(value) {
  const match = typeof value === "string" ? TRACEPARENT.exec(value.trim()) : null;
  if (!match || match[1] === ZERO_TRACE || match[2] === ZERO_SPAN) return null;
  return { traceId: match[1], parentSpanId: match[2], flags: match[3] };
}

export const newSpanId = () => crypto.randomBytes(8).toString("hex");

/** A short, stable, non-reversible tag for an org or workspace id in logs and spans. */
export const hashTenant = (value) => (value ? crypto.createHash("sha256").update(String(value)).digest("hex").slice(0, 16) : undefined);

/**
 * Start the context for one request.
 *
 * @param {import("node:http").IncomingMessage} request
 * @param {{ listener?: string, now?: () => number }} [options]
 */
export function startRequest(request, { listener = "app", now = () => performance.now() } = {}) {
  const inbound = parseTraceparent(request.headers.traceparent);
  const traceId = inbound?.traceId ?? crypto.randomBytes(16).toString("hex");
  const spanId = newSpanId();
  const tracestate = inbound && typeof request.headers.tracestate === "string" && TRACESTATE.test(request.headers.tracestate)
    ? request.headers.tracestate
    : undefined;
  const started = now();
  const timings = {};
  return {
    traceId,
    spanId,
    parentSpanId: inbound?.parentSpanId,
    sampled: inbound ? inbound.flags !== "00" : true,
    tracestate,
    listener,
    startedAtUnixNano: BigInt(Date.now()) * 1_000_000n,
    method: request.method,
    route: undefined,
    rateClass: undefined,
    status: undefined,
    reason: undefined,
    tenant: {},
    timings,
    /** The trace context to send to the next hop, as this span's child. */
    outboundHeaders() {
      const out = { traceparent: `00-${traceId}-${spanId}-${this.sampled ? "01" : "00"}`, "x-request-id": traceId };
      if (tracestate) out.tracestate = tracestate;
      return out;
    },
    /** Time one phase (mint, ee) in milliseconds. */
    async time(name, work) {
      const start = now();
      try {
        return await work();
      } finally {
        timings[name] = (timings[name] ?? 0) + (now() - start);
      }
    },
    elapsedMs: () => now() - started,
  };
}

/**
 * `server-timing`: `mint` (the session exchange, when one ran), `ee` (EE until response
 * headers) and `gw` (the gateway's own share: everything else).
 */
export function serverTiming(context) {
  const total = context.elapsedMs();
  const mint = context.timings.mint ?? 0;
  const ee = context.timings.ee ?? 0;
  const parts = [];
  if (context.timings.mint !== undefined) parts.push(`mint;dur=${mint.toFixed(1)}`);
  parts.push(`gw;dur=${Math.max(0, total - mint - ee).toFixed(1)}`);
  if (context.timings.ee !== undefined) parts.push(`ee;dur=${ee.toFixed(1)}`);
  return parts.join(", ");
}

/** The access line: one JSON object, no request content. */
export function accessRecord(context) {
  const record = {
    event: "omega-gateway.request",
    requestId: context.traceId,
    traceId: context.traceId,
    spanId: context.spanId,
    listener: context.listener,
    method: context.method,
    route: context.route ?? "unmatched",
    status: context.status,
    durationMs: Number(context.elapsedMs().toFixed(1)),
  };
  if (context.timings.mint !== undefined) record.mintMs = Number(context.timings.mint.toFixed(1));
  if (context.timings.ee !== undefined) record.eeMs = Number(context.timings.ee.toFixed(1));
  if (context.rateClass) record.rateClass = context.rateClass;
  if (context.rateStore) record.rateStore = context.rateStore;
  if (context.reason) record.reason = context.reason;
  if (context.tenant.org) record.org = hashTenant(context.tenant.org);
  if (context.tenant.workspace) record.workspace = hashTenant(context.tenant.workspace);
  return record;
}
