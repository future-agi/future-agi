import { randomBytes, randomUUID } from 'node:crypto';
import type { APIRequestContext } from '@playwright/test';
import { deliverOtlp } from './otlp-delivery';

/** `traceId` is the dashed-UUID form the collector writes to CH `spans.trace_id` /
 *  `traces.trace_id` (converter.go `traceIDToUUIDString`); the OTLP wire carries
 *  its undashed hex. `spanIds` are the 16-char hex CH `spans.id` values. */
export interface SeededTrace { traceId: string; spanIds: string[]; projectName: string }

/** Numbers keep the existing doubleValue encoding. No null/undefined, bigint or
 *  non-finite numbers. Arrays/objects recurse as data, not pre-encoded AnyValues. */
export type OtlpAttributeValue = string | number | boolean
  | readonly OtlpAttributeValue[] | { readonly [key: string]: OtlpAttributeValue };
export type OtlpAttributes = Readonly<Record<string, OtlpAttributeValue>>;

/** Extra members of the same trace. IDs and parents are explicit so a fixture
 * can describe nesting without relying on the order in which spans are sent. */
export interface AdditionalSpan extends OtlpSpanDetails {
  spanId: string;
  parentSpanId: string;
  name: string;
  attributes?: OtlpAttributes;
  startTimeUnixNano?: bigint | string;
  endTimeUnixNano?: bigint | string;
}

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
  /** Absent/empty preserves the original two-span payload byte for byte. Extra
   * spans have no inherited attributes and default to the trace's time range. */
  additionalSpans?: readonly AdditionalSpan[];
  /** Omitted keeps the original e2e-harness scope and OK statuses. */
  scope?: OtlpScope;
  rootStatus?: OtlpStatus;
  childStatus?: OtlpStatus;
  rootEvents?: readonly OtlpEvent[];
  childEvents?: readonly OtlpEvent[];
  rootLinks?: readonly OtlpLink[];
  childLinks?: readonly OtlpLink[];
}

/** Native fixture fields use OTLP JSON AnyValues, NOT OtlpAttributeValue's
 * convenience encoding. This preserves int64 strings from source fixtures;
 * sendTrace's ordinary numeric attributes still encode as doubleValue. */
export type OtlpJsonValue = { stringValue: string } | { boolValue: boolean }
  | { intValue: string } | { doubleValue: number } | { bytesValue: string }
  | { arrayValue: { values: readonly OtlpJsonValue[] } }
  | { kvlistValue: { values: readonly OtlpKeyValue[] } };
export interface OtlpKeyValue { key: string; value: OtlpJsonValue }
export interface OtlpScope { name: string; version?: string }
export interface OtlpStatus { code: 0 | 1 | 2; message?: string }
export interface OtlpEvent {
  name: string;
  timeUnixNano?: string;
  attributes?: readonly OtlpKeyValue[];
  droppedAttributesCount?: number;
}
export interface OtlpLink {
  traceId: string;
  spanId: string;
  traceState?: string;
  attributes?: readonly OtlpKeyValue[];
  droppedAttributesCount?: number;
  flags?: number;
}
export interface OtlpSpanDetails {
  status?: OtlpStatus;
  events?: readonly OtlpEvent[];
  links?: readonly OtlpLink[];
}
export interface OtlpSpanIdentity { traceId: string; spanId: string }
export interface OtlpSpan extends OtlpSpanIdentity, OtlpSpanDetails {
  parentSpanId?: string;
  name: string;
  kind?: 0 | 1 | 2 | 3 | 4 | 5;
  startTimeUnixNano: string;
  endTimeUnixNano: string;
  attributes?: readonly OtlpKeyValue[];
}
/** Bounded fixture schema, not a general OTLP SDK: positive timestamps,
 * explicit source IDs, project_name per resource, numeric enums, no transport
 * other than JSON/HTTP. Missing parents are intentional for split delivery. */
export interface OtlpExport {
  resourceSpans: readonly {
    resource: { attributes: readonly OtlpKeyValue[] };
    scopeSpans: readonly { scope: OtlpScope; spans: readonly OtlpSpan[] }[];
  }[];
}
export interface SendOtlpConfig { collectorUrl: string; apiKey: string; secretKey: string }
export interface SeededOtlpExport {
  /** Acknowledged identities with dashed storage-form traceId; selectors accept
   * both this form and wire hex. NOT evidence of persistence/normalization. */
  spans: (OtlpSpanIdentity & {
    projectName: string; parentSpanId?: string; scope: OtlpScope;
    resourceIndex: number; scopeIndex: number;
  })[];
}

type AnyValue = { stringValue: string } | { doubleValue: number } | { boolValue: boolean }
  | { arrayValue: { values: AnyValue[] } }
  | { kvlistValue: { values: { key: string; value: AnyValue }[] } };

function anyValue(value: OtlpAttributeValue): AnyValue {
  if (typeof value === 'string') return { stringValue: value };
  if (typeof value === 'boolean') return { boolValue: value };
  if (typeof value === 'number' && Number.isFinite(value)) return { doubleValue: value };
  if (Array.isArray(value)) return { arrayValue: { values: denseArray(value).map(anyValue) } };
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

  const extras = denseArray(cfg.additionalSpans ?? []).map((item) => {
    const spanStart = timestamp(item.startTimeUnixNano ?? start);
    const spanEnd = timestamp(item.endTimeUnixNano ?? end);
    if (spanEnd < spanStart) throw new Error('OTLP end timestamp must not precede start');
    return { ...item, spanId: hexId(item.spanId, 16),
      parentSpanId: hexId(item.parentSpanId, 16), start: spanStart, end: spanEnd };
  });
  const parents = new Map<string, string | undefined>([[rootId, undefined], [childId, rootId]]);
  for (const item of extras) {
    if (parents.has(item.spanId)) throw new Error('OTLP span IDs must be unique');
    parents.set(item.spanId, item.parentSpanId);
  }
  for (const item of extras) {
    if (!parents.has(item.parentSpanId)) throw new Error('OTLP parent must belong to this trace');
    const seen = new Set<string>();
    let current: string | undefined = item.spanId;
    while (current !== undefined) {
      if (seen.has(current)) throw new Error('OTLP parent cycle (including self-parent)');
      seen.add(current);
      current = parents.get(current);
    }
  }

  const span = (spanId: string, parentSpanId: string | undefined, name: string,
                extraAttrs: ReturnType<typeof attr>[] = [], spanStart = start, spanEnd = end,
                details: OtlpSpanDetails = {}): OtlpSpan => ({
    traceId: wireTraceId, spanId, parentSpanId, name, kind: 1,
    startTimeUnixNano: String(spanStart), endTimeUnixNano: String(spanEnd),
    attributes: extraAttrs, status: details.status ?? { code: 1 },
    ...(details.events === undefined ? {} : { events: details.events }),
    ...(details.links === undefined ? {} : { links: details.links }),
  });

  const payload = {
    resourceSpans: [{
      resource: { attributes: Object.entries({ ...cfg.resourceAttributes,
        project_name: cfg.projectName, 'service.name': cfg.projectName,
      }).map(([key, value]) => attr(key, value)) },
      scopeSpans: [{
        scope: cfg.scope ?? { name: 'e2e-harness' },
        spans: [
          span(rootId, undefined, rootName,
            Object.entries(cfg.rootAttributes ?? {}).map(([key, value]) => attr(key, value)), start, end,
            { status: cfg.rootStatus, events: cfg.rootEvents, links: cfg.rootLinks }),
          span(childId, rootId, cfg.childName ?? 'e2e.llm-call',
            Object.entries({ 'fi.span.kind': 'llm', ...cfg.childAttributes })
              .map(([key, value]) => attr(key, value)), start, end,
            { status: cfg.childStatus, events: cfg.childEvents, links: cfg.childLinks }),
          ...extras.map((item) => span(item.spanId, item.parentSpanId, item.name,
            Object.entries(item.attributes ?? {}).map(([key, value]) => attr(key, value)),
            item.start, item.end, item)),
        ],
      }],
    }],
  };

  validateScope(payload.resourceSpans[0].scopeSpans[0].scope);
  for (const item of payload.resourceSpans[0].scopeSpans[0].spans) validateDetails(item);
  await postOtlp(req, cfg, payload);
  return { traceId, spanIds: [rootId, childId, ...extras.map((item) => item.spanId)], projectName: cfg.projectName };
}

function check(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`OTLP ${message}`);
}
function denseArray<T>(values: readonly T[]): readonly T[] {
  for (let index = 0; index < values.length; index++) {
    check(Object.prototype.hasOwnProperty.call(values, index), 'arrays must not contain holes');
  }
  return values;
}
function object(value: unknown): asserts value is Record<string, unknown> {
  check(value !== null && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype,
    'field must be a plain object');
}
function fields(value: unknown, allowed: readonly string[]): asserts value is Record<string, unknown> {
  object(value);
  check(Object.keys(value).every(key => allowed.includes(key)), 'unsupported fixture field');
}
function uint32(value: unknown) {
  check(typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 0xffffffff,
    'count/flags must be uint32');
}
function text(value: unknown): asserts value is string { check(typeof value === 'string', 'field must be a string'); }
function wireId(value: unknown, length: number): asserts value is string {
  check(typeof value === 'string' && hexId(value, length) === value, 'source IDs must be lowercase hex');
}
function validateAttributes(value: unknown, depth = 0): asserts value is readonly OtlpKeyValue[] {
  check(Array.isArray(value), 'attributes must be an array of key/value entries');
  const keys = new Set<string>();
  for (const entry of value) {
    fields(entry, ['key', 'value']);
    check(typeof entry.key === 'string' && entry.key.length > 0 && !keys.has(entry.key),
      'attribute keys must be nonempty and unique');
    keys.add(entry.key);
    validateValue(entry.value, depth);
  }
}
function validateValue(value: unknown, depth: number): void {
  check(depth <= 32, 'attribute nesting exceeds 32');
  object(value);
  const keys = Object.keys(value);
  check(keys.length === 1, 'AnyValue must have exactly one type');
  const key = keys[0], item = value[key];
  switch (key) {
    case 'stringValue': text(item); break;
    case 'boolValue': check(typeof item === 'boolean', 'boolValue must be boolean'); break;
    case 'doubleValue': check(typeof item === 'number' && Number.isFinite(item), 'doubleValue must be finite'); break;
    case 'intValue':
      check(typeof item === 'string' && /^-?\d+$/.test(item), 'intValue must be decimal text');
      check(BigInt(item) >= -9_223_372_036_854_775_808n && BigInt(item) <= 9_223_372_036_854_775_807n,
        'intValue must fit int64');
      break;
    case 'bytesValue':
      check(typeof item === 'string' && /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(item),
        'bytesValue must be base64');
      break;
    case 'arrayValue':
      fields(item, ['values']); check(Array.isArray(item.values), 'arrayValue requires values');
      denseArray(item.values).forEach(v => validateValue(v, depth + 1)); break;
    case 'kvlistValue':
      fields(item, ['values']); validateAttributes(item.values, depth + 1); break;
    default: throw new Error('OTLP unsupported AnyValue type');
  }
}
function validateScope(value: unknown): asserts value is OtlpScope {
  fields(value, ['name', 'version']); text(value.name);
  if (value.version !== undefined) text(value.version);
}
function validateDetails(span: OtlpSpanDetails): void {
  if (span.status !== undefined) {
    fields(span.status, ['code', 'message']);
    check([0, 1, 2].includes(span.status.code), 'status code must be 0, 1 or 2');
    if (span.status.message !== undefined) text(span.status.message);
  }
  if (span.events !== undefined) {
    check(Array.isArray(span.events), 'events must be an array');
    for (const event of span.events) {
      fields(event, ['name', 'timeUnixNano', 'attributes', 'droppedAttributesCount']); text(event.name);
      if (event.timeUnixNano !== undefined) { text(event.timeUnixNano); timestamp(event.timeUnixNano); }
      if (event.attributes !== undefined) validateAttributes(event.attributes);
      if (event.droppedAttributesCount !== undefined) uint32(event.droppedAttributesCount);
    }
  }
  if (span.links !== undefined) {
    check(Array.isArray(span.links), 'links must be an array');
    for (const link of span.links) {
      fields(link, ['traceId', 'spanId', 'traceState', 'attributes', 'droppedAttributesCount', 'flags']);
      wireId(link.traceId, 32); wireId(link.spanId, 16);
      if (link.traceState !== undefined) text(link.traceState);
      if (link.attributes !== undefined) validateAttributes(link.attributes);
      if (link.droppedAttributesCount !== undefined) uint32(link.droppedAttributesCount);
      if (link.flags !== undefined) uint32(link.flags);
    }
  }
}
const identity = (span: OtlpSpanIdentity) => `${span.traceId}:${span.spanId}`;
const dashed = (id: string) => id.replace(/(.{8})(.{4})(.{4})(.{4})(.{12})/, '$1-$2-$3-$4-$5');

/** Validate the complete export before any I/O, including cross-scope cycles.
 * A missing parent is allowed, a parent in this export must not form a cycle. */
function inspectExport(fixture: OtlpExport): SeededOtlpExport {
  fields(fixture, ['resourceSpans']);
  check(Array.isArray(fixture.resourceSpans) && fixture.resourceSpans.length > 0, 'resourceSpans must be nonempty');
  const members = new Map<string, string | undefined>();
  const receipt: SeededOtlpExport = { spans: [] };
  denseArray(fixture.resourceSpans).forEach((resource, resourceIndex) => {
    fields(resource, ['resource', 'scopeSpans']); fields(resource.resource, ['attributes']);
    validateAttributes(resource.resource.attributes);
    const project = resource.resource.attributes.find(a => a.key === 'project_name')?.value;
    check(project && 'stringValue' in project && project.stringValue.length > 0,
      'each resource requires a nonempty project_name string');
    check(Array.isArray(resource.scopeSpans) && resource.scopeSpans.length > 0, 'scopeSpans must be nonempty');
    denseArray(resource.scopeSpans).forEach((scope, scopeIndex) => {
      fields(scope, ['scope', 'spans']); validateScope(scope.scope);
      check(Array.isArray(scope.spans) && scope.spans.length > 0, 'spans must be nonempty');
      for (const span of scope.spans) {
        fields(span, ['traceId', 'spanId', 'parentSpanId', 'name', 'kind', 'startTimeUnixNano',
          'endTimeUnixNano', 'attributes', 'status', 'events', 'links']);
        wireId(span.traceId, 32); wireId(span.spanId, 16); text(span.name);
        if (span.parentSpanId !== undefined) wireId(span.parentSpanId, 16);
        if (span.kind !== undefined) check(typeof span.kind === 'number' && [0, 1, 2, 3, 4, 5].includes(span.kind), 'kind must be 0 through 5');
        text(span.startTimeUnixNano); text(span.endTimeUnixNano);
        check(timestamp(span.endTimeUnixNano) >= timestamp(span.startTimeUnixNano), 'end timestamp must not precede start');
        if (span.attributes !== undefined) validateAttributes(span.attributes);
        validateDetails(span);
        const key = identity({ traceId: span.traceId, spanId: span.spanId });
        check(!members.has(key), 'source span identities must be unique across the export');
        members.set(key, span.parentSpanId === undefined ? undefined : `${span.traceId}:${span.parentSpanId}`);
        receipt.spans.push({ traceId: dashed(span.traceId), spanId: span.spanId,
          parentSpanId: span.parentSpanId, projectName: project.stringValue,
          scope: { ...scope.scope }, resourceIndex, scopeIndex });
      }
    });
  });
  for (const key of members.keys()) {
    const visited = new Set<string>();
    let current: string | undefined = key;
    while (current !== undefined && members.has(current)) {
      check(!visited.has(current), 'parent cycle (including self-parent)');
      visited.add(current); current = members.get(current);
    }
  }
  return receipt;
}
function freeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze); Object.freeze(value);
  }
  return value;
}
/** Detached, frozen fixture; no generated IDs/times and no caller mutation. */
export function createOtlpFixture(input: OtlpExport): OtlpExport {
  inspectExport(input);
  return freeze(structuredClone(input));
}
/** Pick explicit (traceId, spanId) pairs, preserving resource/scope order and
 * every source field. Select children first or parents first without rewriting
 * their ancestry. Replaying a selection sends the identical wire facts. */
export function selectOtlpSpans(fixture: OtlpExport, selection: readonly OtlpSpanIdentity[]): OtlpExport {
  inspectExport(fixture);
  check(Array.isArray(selection) && selection.length > 0, 'selection must be nonempty');
  const pending = new Set<string>();
  for (const member of selection) {
    fields(member, ['traceId', 'spanId']); text(member.traceId); wireId(member.spanId, 16);
    // A receipt uses storage UUID form, while the source fixture stays wire hex.
    // Normalize only the selector; never rewrite the fixture's immutable IDs.
    const traceId = hexId(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(member.traceId)
      ? member.traceId.replaceAll('-', '') : member.traceId, 32);
    const key = identity({ traceId, spanId: member.spanId });
    check(!pending.has(key), 'selection identities must be unique'); pending.add(key);
  }
  const resourceSpans = fixture.resourceSpans.map(resource => ({ ...resource,
    scopeSpans: resource.scopeSpans.map(scope => ({ ...scope,
      spans: scope.spans.filter(span => pending.delete(identity(span))),
    })).filter(scope => scope.spans.length > 0),
  })).filter(resource => resource.scopeSpans.length > 0);
  check(pending.size === 0, 'selection contains unknown source identities');
  return createOtlpFixture({ resourceSpans });
}

/** Fail closed on partial_success, including snake_case protobuf field names.
 * Strict harness policy: even a zero-rejection warning is not a clean receipt.
 * No server text is reflected into errors (it could echo input or credentials).
 * Empty bodies remain accepted for compatibility with legacy collector replies. */
export function assertOtlpSuccess(body: string): void {
  if (body.trim() === '') return;
  let parsed: unknown;
  try { parsed = JSON.parse(body); } catch { throw new Error('collector returned invalid OTLP JSON'); }
  fields(parsed, ['partialSuccess', 'partial_success']);
  for (const key of ['partialSuccess', 'partial_success']) {
    if (!(key in parsed)) continue;
    const partial = parsed[key];
    fields(partial, ['rejectedSpans', 'rejected_spans', 'errorMessage', 'error_message']);
    for (const countKey of ['rejectedSpans', 'rejected_spans']) {
      if (!(countKey in partial)) continue;
      const count = partial[countKey];
      check((typeof count === 'string' && /^\d+$/.test(count))
        || (typeof count === 'number' && Number.isSafeInteger(count) && count >= 0),
      'partial_success rejected count must be nonnegative integer text or a safe integer');
      check(BigInt(count as string | number) <= 9_223_372_036_854_775_807n, 'partial_success rejected count must fit int64');
      if (BigInt(count as string | number) !== 0n) throw new Error('collector partial_success rejected spans');
    }
    for (const messageKey of ['errorMessage', 'error_message']) {
      if (!(messageKey in partial)) continue;
      text(partial[messageKey]);
      if (partial[messageKey] !== '') throw new Error('collector partial_success reported a warning/error');
    }
  }
}
async function postOtlp(req: APIRequestContext, cfg: SendOtlpConfig, payload: unknown): Promise<void> {
  // Freeze wire bytes and credentials before the first await. A refused export
  // is replayed verbatim, never regenerated with fresh IDs or timestamps.
  await deliverOtlp(req, {
    url: `${cfg.collectorUrl}/v1/traces`,
    headers: { 'X-Api-Key': cfg.apiKey, 'X-Secret-Key': cfg.secretKey, 'Content-Type': 'application/json' },
    data: JSON.stringify(payload),
  }, assertOtlpSuccess);
}
/** Only JSON/HTTP to the caller's collector base + /v1/traces. This does not
 * activate disabled Python routes or qualify protobuf/gRPC transports. */
export async function sendOtlpExport(req: APIRequestContext, cfg: SendOtlpConfig,
  fixture: OtlpExport): Promise<SeededOtlpExport> {
  const snapshot = createOtlpFixture(fixture);
  const receipt = inspectExport(snapshot);
  await postOtlp(req, cfg, snapshot);
  return receipt;
}
