import { randomBytes, randomUUID } from 'node:crypto';
import type { APIRequestContext } from '@playwright/test';

/** `traceId` is the dashed-UUID form the collector writes to CH `spans.trace_id` /
 *  `traces.trace_id` (converter.go `traceIDToUUIDString`); the OTLP wire carries
 *  its undashed hex. `spanIds` are the 16-char hex CH `spans.id` values. */
export interface SeededTrace { traceId: string; spanIds: string[]; projectName: string }

/** Numbers keep the existing doubleValue encoding. No null/undefined, bigint or
 *  non-finite numbers. Arrays/objects recurse as data, not pre-encoded AnyValues. */
export type OtlpAttributeValue = string | number | boolean
  | readonly OtlpAttributeValue[] | { readonly [key: string]: OtlpAttributeValue };
export type OtlpAttributes = Readonly<Record<string, OtlpAttributeValue>>;

export interface SendTraceConfig {
  collectorUrl: string; apiKey: string; secretKey: string; projectName: string;
  rootName?: string;
  childName?: string;
  /** UUID or 32 hex digits; returned as a lowercase dashed UUID. */
  traceId?: string;
  /** Nonzero 16-hex IDs. Reuse IDs AND timestamps for overlapping/replayed facts. */
  rootSpanId?: string;
  childSpanId?: string;
  /** Shared by both spans. Positive epoch nanoseconds, as bigint or decimal text
   *  (never a lossy JS number). Defaults: now through start + 50 ms.
   *  The collector's DateTime64(6) source columns retain microsecond precision. */
  startTimeUnixNano?: bigint | string;
  endTimeUnixNano?: bigint | string;
  rootAttributes?: OtlpAttributes;
  /** Does not inherit root attributes. Overrides the default fi.span.kind=llm. */
  childAttributes?: OtlpAttributes;
  /** e.g. project_type: 'observe' for converter user identity. projectName owns
   *  project_name/service.name; caller attributes cannot override those two. */
  resourceAttributes?: OtlpAttributes;
}

type AnyValue = { stringValue: string } | { doubleValue: number } | { boolValue: boolean }
  | { arrayValue: { values: AnyValue[] } }
  | { kvlistValue: { values: { key: string; value: AnyValue }[] } };

function anyValue(value: OtlpAttributeValue): AnyValue {
  if (typeof value === 'string') return { stringValue: value };
  if (typeof value === 'boolean') return { boolValue: value };
  if (typeof value === 'number' && Number.isFinite(value)) return { doubleValue: value };
  if (Array.isArray(value)) return { arrayValue: { values: value.map(anyValue) } };
  if (value && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype) {
    return { kvlistValue: { values: Object.entries(value).map(([key, item]) => attr(key, item)) } };
  }
  throw new Error('OTLP attributes require finite scalars, arrays or plain objects');
}
const attr = (key: string, value: OtlpAttributeValue) => ({ key, value: anyValue(value) });
const nsNow = () => (BigInt(Date.now()) * 1_000_000n);

function hexId(value: string, length: number): string {
  if (!new RegExp(`^[0-9a-f]{${length}}$`, 'i').test(value) || /^0+$/.test(value)) {
    throw new Error(`OTLP ID must be ${length} nonzero hex digits`);
  }
  return value.toLowerCase();
}

function timestamp(value: bigint | string): bigint {
  if (!['bigint', 'string'].includes(typeof value) || !/^\d+$/.test(String(value))) {
    throw new Error('OTLP timestamp must be unsigned decimal nanoseconds (bigint or string)');
  }
  const nanos = BigInt(value);
  if (nanos <= 0n || nanos > 18_446_744_073_709_551_615n) {
    throw new Error('OTLP timestamp must be a positive uint64');
  }
  return nanos;
}

export async function sendTrace(req: APIRequestContext, cfg: SendTraceConfig): Promise<SeededTrace> {
  const suppliedTraceId = cfg.traceId ?? randomUUID();
  const wireTraceId = hexId(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(suppliedTraceId)
    ? suppliedTraceId.replaceAll('-', '') : suppliedTraceId, 32);
  const traceId = [wireTraceId.slice(0, 8), wireTraceId.slice(8, 12), wireTraceId.slice(12, 16),
    wireTraceId.slice(16, 20), wireTraceId.slice(20)].join('-');
  const rootId = hexId(cfg.rootSpanId ?? randomBytes(8).toString('hex'), 16);
  const childId = hexId(cfg.childSpanId ?? randomBytes(8).toString('hex'), 16);
  if (rootId === childId) throw new Error('OTLP root and child IDs must differ');
  const start = timestamp(cfg.startTimeUnixNano ?? nsNow());
  const end = timestamp(cfg.endTimeUnixNano ?? start + 50_000_000n);
  if (end < start) throw new Error('OTLP end timestamp must not precede start');
  const rootName = cfg.rootName ?? 'e2e.root';

  const span = (spanId: string, parentSpanId: string | undefined, name: string,
                extraAttrs: ReturnType<typeof attr>[] = []) => ({
    traceId: wireTraceId, spanId, parentSpanId, name, kind: 1,
    startTimeUnixNano: String(start), endTimeUnixNano: String(end),
    attributes: extraAttrs, status: { code: 1 },
  });

  const payload = {
    resourceSpans: [{
      resource: { attributes: Object.entries({ ...cfg.resourceAttributes,
        project_name: cfg.projectName, 'service.name': cfg.projectName,
      }).map(([key, value]) => attr(key, value)) },
      scopeSpans: [{
        scope: { name: 'e2e-harness' },
        spans: [
          span(rootId, undefined, rootName,
            Object.entries(cfg.rootAttributes ?? {}).map(([key, value]) => attr(key, value))),
          span(childId, rootId, cfg.childName ?? 'e2e.llm-call',
            Object.entries({ 'fi.span.kind': 'llm', ...cfg.childAttributes })
              .map(([key, value]) => attr(key, value))),
        ],
      }],
    }],
  };

  const res = await req.post(`${cfg.collectorUrl}/v1/traces`, {
    headers: { 'X-Api-Key': cfg.apiKey, 'X-Secret-Key': cfg.secretKey, 'Content-Type': 'application/json' },
    data: payload,
  });
  if (res.status() >= 300) throw new Error(`collector ${res.status()}: ${await res.text()}`);
  return { traceId, spanIds: [rootId, childId], projectName: cfg.projectName };
}
