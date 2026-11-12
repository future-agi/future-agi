import { createServer } from 'node:http';
import { isDeepStrictEqual } from 'node:util';
import { decodeGatewayOtlp } from './otlp-wire';
import { requireCohort, requireId } from '../../_shared/identifiers';

// Source da4c81cc62: internal/otel/otlp.go posts uncompressed protobuf to
// /v1/traces; plugins/otel/{otel,bodies,messages}.go emits agentcc.request_id,
// input.value/output.value and llm.{input,output}_messages.*. These are NOT
// invented MCP org/tool attributes: the candidate has no W13 trace_content
// consent or H10 SIEM emitter. Their product binding remains F13-OWED.
export interface SafeSpan {
  requestId: string; inputPresent: boolean; outputPresent: boolean;
  inputMatches: boolean; outputMatches: boolean;
  contentAttributes: number; sentinelPresent: boolean;
}
export interface PrivacyExpectation { requestId: string; consent: boolean; operatorBodies: boolean }
function contains(value: unknown, sentinel: string): boolean {
  if (typeof value === 'string') return value.includes(sentinel);
  if (Buffer.isBuffer(value)) return value.includes(Buffer.from(sentinel));
  if (value instanceof Map) return [...value].some(([k, v]) => contains(k, sentinel) || contains(v, sentinel));
  if (Array.isArray(value)) return value.some(v => contains(v, sentinel));
  return false;
}
const isContentKey = (key: string) => key.startsWith('input.') || key.startsWith('output.') ||
  key.startsWith('llm.input_messages.') || key.startsWith('llm.output_messages.');

/** Data-only detector. Consent/operator states must be independently read from
 * real product/runtime configuration, not inferred from the received body.
 * Request membership does NOT establish org attribution; W13.A05 stays owed.
 * No sentinel/body/header value occurs in an error, return or attachment.
 */
export function assertPrivacyProjection(actual: readonly SafeSpan[], expected: readonly PrivacyExpectation[]): void {
  requireCohort(expected.map(e => e.requestId));
  if (expected.some(e => typeof e.consent !== 'boolean' || typeof e.operatorBodies !== 'boolean')) {
    throw new Error('PRIVACY_EXPECTATION_INVALID');
  }
  if (!isDeepStrictEqual(actual.map(s => s.requestId).sort(), expected.map(s => s.requestId).sort())) {
    throw new Error('PRIVACY_REQUEST_SET');
  }
  for (const row of actual) {
    if (row.sentinelPresent !== false) throw new Error('PRIVACY_SENTINEL');
    const plan = expected.find(e => e.requestId === row.requestId)!;
    const allowed = plan.consent && plan.operatorBodies;
    if (!Number.isSafeInteger(row.contentAttributes) || row.contentAttributes < 0 ||
        row.inputPresent !== allowed || row.outputPresent !== allowed ||
        row.inputMatches !== allowed || row.outputMatches !== allowed ||
        (allowed ? row.contentAttributes < 2 : row.contentAttributes !== 0)) {
      throw new Error('PRIVACY_CONJUNCTION');
    }
  }
}

/** Explicit callable fixture, never starts on import. The accepted runner must
 * start it INSIDE the same stack network namespace as the emitter, under its
 * guard/census, and stop it in finally. There is no consumer process/Compose
 * launcher or outbound network call. Bind is always IPv4 loopback (not 0.0.0.0).
 * No HTTP read/reset endpoint: safe state is returned only to the owning caller.
 */
export async function createOtlpSink(options: {
  port: number; requestIds: readonly string[]; syntheticSentinels: readonly string[];
  // Independently minted expected synthetic payload AFTER masking. Never use
  // an observed response as the expectation. Compare in memory, emit booleans.
  maskedContent: readonly { requestId: string; input: string; output: string }[];
}) {
  const ids = new Set(requireCohort(options.requestIds));
  if (!Number.isInteger(options.port) || options.port < 0 || options.port > 65535 ||
      !options.syntheticSentinels.length || options.syntheticSentinels.some(s =>
        typeof s !== 'string' || !/^synthetic-[A-Za-z0-9-]{16,100}$/.test(s))) {
    throw new Error('OTLP_FIXTURE_BINDING_INVALID');
  }
  const sentinels = [...options.syntheticSentinels];
  requireCohort(options.maskedContent.map(row => row.requestId));
  if (!isDeepStrictEqual(options.maskedContent.map(row => row.requestId).sort(), [...ids].sort()) ||
      options.maskedContent.some(row => [row.input, row.output].some(value =>
        typeof value !== 'string' || !value.length || value.length > 1024 * 1024 ||
        sentinels.some(s => value.includes(s))))) throw new Error('OTLP_MASK_EXPECTATION_INVALID');
  const masked = new Map(options.maskedContent.map(row => [row.requestId, { input: row.input, output: row.output }]));
  const spans: SafeSpan[] = [];
  let acceptedBatches = 0; let rejectedBatches = 0; let pending = 0;
  const server = createServer({ maxHeaderSize: 8192, requestTimeout: 10_000, headersTimeout: 5000 }, async (req, res) => {
    const reject = (code: number) => { rejectedBatches++; res.writeHead(code, { 'connection': 'close' }); res.end(); };
    // Never reflect a path, header, body or exception in the response/log.
    if (req.method !== 'POST' || req.url !== '/v1/traces') return reject(404);
    if (req.headers['content-type'] !== 'application/x-protobuf' || req.headers['content-encoding'] !== undefined) return reject(415);
    if (pending >= 8) return reject(429);
    pending++;
    try {
      const chunks: Buffer[] = []; let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 8 * 1024 * 1024) { reject(413); return; }
        chunks.push(Buffer.from(chunk));
      }
      const decoded = decodeGatewayOtlp(Buffer.concat(chunks));
      const projected = decoded.map(span => {
        const value = span.attributes.get('agentcc.request_id');
        if (typeof value !== 'string' || !ids.has(value)) throw new Error('OTLP_UNBOUND_REQUEST');
        const requestId = requireId(value);
        const keys = [...span.attributes.keys(), ...span.resourceAttributes.keys()];
        return { requestId,
          inputPresent: span.attributes.has('input.value'), outputPresent: span.attributes.has('output.value'),
          inputMatches: span.attributes.get('input.value') === masked.get(requestId)!.input,
          outputMatches: span.attributes.get('output.value') === masked.get(requestId)!.output,
          contentAttributes: keys.filter(isContentKey).length,
          sentinelPresent: sentinels.some(s => contains(span.scannedValues, s)) };
      });
      // Atomic append; never silently drop overflow/unbound/malformed spans.
      if (spans.length + projected.length > 16_384) { reject(507); return; }
      spans.push(...projected); acceptedBatches++;
      // Empty protobuf ExportTraceServiceResponse (source contract success).
      res.writeHead(200, { 'content-type': 'application/x-protobuf' }); res.end();
    } catch { reject(400); }
    finally { pending--; }
  });
  // Native parser failures are sticky evidence too; no request bytes are logged.
  server.on('clientError', (_error, socket) => { rejectedBatches++; socket.destroy(); });
  await new Promise<void>((resolve, reject) => {
    const failed = () => reject(new Error('OTLP_FIXTURE_LISTEN_FAILED'));
    server.once('error', failed);
    server.listen(options.port, '127.0.0.1', () => { server.off('error', failed); resolve(); });
  });
  let runtimeError = false;
  server.on('error', () => { runtimeError = true; });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('OTLP_FIXTURE_ADDRESS_INVALID');
  return {
    url: `http://127.0.0.1:${address.port}/v1/traces`,
    read: () => ({ acceptedBatches, rejectedBatches, pending, runtimeError, spans: spans.map(s => ({ ...s })) }),
    close: () => new Promise<void>((resolve, reject) => {
      server.close(error => error ? reject(new Error('OTLP_FIXTURE_CLOSE_FAILED')) : resolve());
      server.closeAllConnections();
    }),
  };
}
