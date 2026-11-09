import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { attachSeededIds } from '../_shared/contract';
import { fragmentAnnotation } from './fragments';
import { decodeGatewayOtlp } from '../_fixtures/sinks-catalogue/otlp-wire';
import { createOtlpSink, assertPrivacyProjection } from '../_fixtures/sinks-catalogue/otlp-sink';
import { projectWebhookRecords, projectStorageRecords } from '../_fixtures/sinks-catalogue/export-readers';
import { readAllSyncPages } from '../_fixtures/sinks-catalogue/export-sync-api';
import type { ApiClient } from '../../../lib/api-client';

// Tests FIRST; authoring-stage NOT_RUN. No product-row annotations: these qualify
// the consumer fixtures, not W13/W14/W17. The runner must execute them guarded.
// Encoder independent of decoder; tags pinned to otlp v1.5.0 *.pb.go (go.mod).
function varint(value: bigint): Buffer {
  const bytes: number[] = [];
  do { bytes.push(Number(value & 127n) | (value > 127n ? 128 : 0)); value >>= 7n; } while (value);
  return Buffer.from(bytes);
}
const bytes = (field: number, data: Buffer | string) => {
  const body = typeof data === 'string' ? Buffer.from(data) : data;
  return Buffer.concat([varint(BigInt(field * 8 + 2)), varint(BigInt(body.length)), body]);
};
const attribute = (key: string, value: string) => bytes(9,
  Buffer.concat([bytes(1, key), bytes(2, bytes(1, value))]));
function wire(requestId: string, content?: string, extra = Buffer.alloc(0)): Buffer {
  const start = Buffer.alloc(9); start[0] = 57; start.writeBigUInt64LE(10n, 1);
  const end = Buffer.alloc(9); end[0] = 65; end.writeBigUInt64LE(20n, 1);
  const span = Buffer.concat([bytes(1, Buffer.alloc(16, 1)), bytes(2, Buffer.alloc(8, 2)),
    bytes(5, 'chat_completion'), Buffer.from([48, 3]), start, end,
    attribute('agentcc.request_id', requestId),
    ...(content === undefined ? [] : [attribute('input.value', content), attribute('output.value', content)]), extra]);
  const scope = bytes(1, bytes(1, 'github.com/futureagi/agentcc-gateway'));
  return bytes(1, Buffer.concat([bytes(1, Buffer.alloc(0)), bytes(2, Buffer.concat([scope, bytes(2, span)]))]));
}

test('F13 fixture OTLP rejects malformed protobuf and duplicate attributes', {
  tag: '@journey-J15-fixture', annotation: fragmentAnnotation('W13.A04', 'OTLP-wire-controls', true),
}, async ({}, info) => {
  const id = randomUUID();
  await attachSeededIds(info, 'J15', { requestIds: [id] });
  expect(decodeGatewayOtlp(wire(id))).toHaveLength(1);
  for (const bad of [Buffer.from('{"resourceSpans":[]}'), wire(id).subarray(0, -1),
    wire(id, undefined, attribute('agentcc.request_id', id)), Buffer.from([0]), Buffer.from([10, 128])]) {
    expect(() => decodeGatewayOtlp(bad)).toThrow('OTLP_WIRE_INVALID');
  }
  expect(decodeGatewayOtlp(wire(id))).toHaveLength(1); // restored original
});

test('F13 fixture loopback OTLP rejects JSON and reports privacy violations without bodies', {
  tag: '@journey-J15-fixture', annotation: fragmentAnnotation('W13.A04', 'native-loopback-sink', true),
}, async ({}, info) => {
  const id = randomUUID(); const sentinel = `synthetic-${randomUUID()}`;
  await attachSeededIds(info, 'J15', { requestIds: [id] });
  const sink = await createOtlpSink({ requestIds: [id], syntheticSentinels: [sentinel], port: 0,
    maskedContent: [{ requestId: id, input: '[REDACTED]', output: '[REDACTED]' }] });
  try {
    const wrong = await fetch(sink.url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
    expect(wrong.status).toBe(415); await wrong.arrayBuffer();
    for (const body of [wire(id, '[REDACTED]'), wire(id, sentinel)]) {
      const response = await fetch(sink.url, { method: 'POST', headers: { 'content-type': 'application/x-protobuf' }, body });
      expect(response.status).toBe(200); await response.arrayBuffer();
    }
    const state = sink.read();
    expect(state.acceptedBatches).toBe(2);
    expect(state.rejectedBatches).toBe(1);
    expect(state.spans.map(s => s.sentinelPresent)).toEqual([false, true]);
    expect(JSON.stringify(state).includes(sentinel)).toBe(false);
    expect(state.spans.map(s => [s.inputMatches, s.outputMatches])).toEqual([[true, true], [false, false]]);
    expect(Object.keys(state.spans[0]).sort()).toEqual(['contentAttributes', 'inputMatches', 'inputPresent',
      'outputMatches', 'outputPresent', 'requestId', 'sentinelPresent']);
  } finally { await sink.close(); }
});

for (const consent of [false, true]) for (const operatorBodies of [false, true]) {
  test(`F13 privacy oracle conjunction consent=${consent} operator=${operatorBodies}`, {
    tag: '@journey-J15-fixture', annotation: fragmentAnnotation('W13.A04', 'privacy-conjunction-controls', true),
  }, async ({}, info) => {
    const id = randomUUID(); const foreign = randomUUID();
    await attachSeededIds(info, 'J15', { requestIds: [id, foreign] });
    const allowed = consent && operatorBodies;
    const good = { requestId: id, inputPresent: allowed, outputPresent: allowed,
      inputMatches: allowed, outputMatches: allowed,
      contentAttributes: allowed ? 2 : 0, sentinelPresent: false };
    const plan = [{ requestId: id, consent, operatorBodies }];
    expect(() => assertPrivacyProjection([good], plan)).not.toThrow();
    expect(() => assertPrivacyProjection([{ ...good, sentinelPresent: true }], plan)).toThrow('PRIVACY_SENTINEL');
    expect(() => assertPrivacyProjection([{ ...good, requestId: foreign }], plan)).toThrow('PRIVACY_REQUEST_SET');
    expect(() => assertPrivacyProjection([{ ...good, inputPresent: !allowed, outputPresent: !allowed,
      contentAttributes: allowed ? 0 : 2 }], plan)).toThrow('PRIVACY_CONJUNCTION');
    expect(() => assertPrivacyProjection([good, good], plan)).toThrow('PRIVACY_REQUEST_SET');
    if (allowed) expect(() => assertPrivacyProjection([{ ...good, inputMatches: false }], plan)).toThrow('PRIVACY_CONJUNCTION');
    expect(() => assertPrivacyProjection([good], plan)).not.toThrow();
  });
}

for (const vendor of ['datadog', 'mixpanel', 'posthog'] as const) {
  test(`F15 ${vendor} projection preserves extra IDs, multiplicity and failures`, {
    tag: '@journey-J15-fixture', annotation: fragmentAnnotation('W17.A03', `${vendor}-projection-controls`, true),
  }, async ({}, info) => {
    const a = randomUUID(); const b = randomUUID(); const credential = `e2e-${randomUUID()}`;
    await attachSeededIds(info, 'J15', { requestIds: [a, b] });
    const channel = `fixture-${randomUUID()}`;
    const event = (id: string) => ({ event: 'agentcc_request', properties: { token: credential, request_id: id } });
    const body = vendor === 'datadog' ? [a, a, b].map(id => ({ message: 'synthetic', attributes: { request_id: id } }))
      : vendor === 'mixpanel' ? [a, a, b].map(event) : { api_key: credential, batch: [a, a, b].map(event) };
    const route = vendor === 'datadog' ? '/api/v2/logs' : vendor === 'mixpanel' ? '/import?strict=1' : '/batch/';
    const record = { at: '2026-10-30T00:00:00Z', method: 'POST', path: `/r/${channel}/as/${vendor}${route}`,
      status: vendor === 'datadog' ? 202 : 200, headers: { 'dd-api-key': credential }, body };
    const binding = { vendor, channel, credential };
    const good = projectWebhookRecords([record], binding);
    expect(good.deliveredIds).toEqual([a, a, b]);
    expect(JSON.stringify(good).includes(credential)).toBe(false);
    expect(projectWebhookRecords([{ ...record, status: 401 }], binding).failedBatches).toBe(1);
    expect(projectWebhookRecords([{ ...record, status: 401 }], binding).deliveredIds).toEqual([]);
    const malformed = vendor === 'posthog' ? { api_key: credential, batch: [{}] }
      : vendor === 'mixpanel' ? [{ event: 'agentcc_request', properties: { token: credential } }] : [{ attributes: {} }];
    expect(() => projectWebhookRecords([{ ...record, body: malformed }], binding)).toThrow('EXPORT_SHAPE');
    if (vendor === 'posthog') expect(() => projectWebhookRecords([{ ...record,
      body: { api_key: `e2e-${randomUUID()}`, batch: [event(b)] } }], binding)).toThrow('EXPORT_SHAPE');
    expect(projectWebhookRecords([record], binding).deliveredIds).toEqual([a, a, b]);
  });
}

test('F15 PostHog complete history distinguishes exact validation from deliveries', {
  tag: '@journey-J15-fixture', annotation: fragmentAnnotation('W17.A03', 'posthog-validation-history-controls', true),
}, async ({}, info) => {
  const a = randomUUID(); const b = randomUUID(); const credential = `e2e-${randomUUID()}`;
  await attachSeededIds(info, 'J15', { requestIds: [a, b] });
  const channel = `fixture-${randomUUID()}`; const prefix = `/r/${channel}/as/posthog`;
  const binding = { vendor: 'posthog' as const, channel, credential };
  // posthog_service.py:validate_credentials/export_events at da4c81cc62;
  // webhook-sink/server.mjs:handleRequest retains the complete req.url query.
  const validation = { at: '2026-10-30T00:00:00Z', method: 'POST', path: `${prefix}/decide/?v=3`,
    status: 200, headers: {}, body: { api_key: credential, distinct_id: 'agentcc_connection_test' } };
  const event = (id: string) => ({ event: 'agentcc_request', distinct_id: 'agentcc-gateway',
    timestamp: validation.at, properties: { token: credential, request_id: id } });
  const batch = { ...validation, path: `${prefix}/batch/`,
    body: { api_key: credential, batch: [a, a, b].map(event) } };
  const failed = { ...batch, status: 401, body: { api_key: credential, batch: [event(b)] } };
  const history = [validation, batch, failed];
  const expected = { deliveredIds: [a, a, b], attemptedIds: [a, a, b, b], successfulBatches: 1, failedBatches: 1 };
  expect(projectWebhookRecords(history, binding)).toEqual(expected);
  expect(JSON.stringify(projectWebhookRecords(history, binding)).includes(credential)).toBe(false);
  for (const suffix of ['/decide/', '/decide/?v=2', '/decide/?v=3&extra=1', '/decide?v=3', '/unknown/?v=3']) {
    expect(() => projectWebhookRecords([{ ...validation, path: `${prefix}${suffix}` }, batch, failed], binding))
      .toThrow('EXPORT_SHAPE');
  }
  expect(() => projectWebhookRecords([{ ...validation, method: 'GET' }, batch, failed], binding)).toThrow('EXPORT_SHAPE');
  expect(() => projectWebhookRecords([{ ...validation,
    body: { ...validation.body, distinct_id: a } }, batch, failed], binding)).toThrow('EXPORT_SHAPE');
  const foreign = `e2e-${randomUUID()}`;
  // Preserve credential partitioning for metadata, but fail on a foreign delivery
  // in this dedicated PostHog channel; never filter by expected request IDs.
  expect(projectWebhookRecords([{ ...validation, body: { ...validation.body, api_key: foreign } }, ...history], binding))
    .toEqual(expected);
  expect(() => projectWebhookRecords([...history, { ...batch, body: { ...batch.body, api_key: foreign } }], binding))
    .toThrow('EXPORT_SHAPE');
  expect(projectWebhookRecords(history, binding)).toEqual(expected); // restored original history
});

test('F15 storage projection rejects prefix escapes and keeps duplicate deliveries', {
  tag: '@journey-J15-fixture', annotation: fragmentAnnotation('W17.A03', 'storage-projection-controls', true),
}, async ({}, info) => {
  const id = randomUUID(); const prefix = `fixture-${randomUUID()}`;
  await attachSeededIds(info, 'J15', { requestIds: [id] });
  const object = { key: `${prefix}/a.jsonl.gz`, size: 42, contentType: 'application/x-ndjson',
    contentEncoding: 'gzip', records: [{ request_id: id }, { request_id: id }] };
  expect(projectStorageRecords([object], prefix).deliveredIds).toEqual([id, id]);
  expect(() => projectStorageRecords([{ ...object, key: `other/a.jsonl.gz` }], prefix)).toThrow('EXPORT_STORAGE_SCOPE');
  expect(() => projectStorageRecords([{ ...object, records: [{}] }], prefix)).toThrow('EXPORT_SHAPE');
  expect(projectStorageRecords([object], prefix).deliveredIds).toEqual([id, id]);
});

test('F15 sync API walks every page and refuses drift and duplicates', {
  tag: '@journey-J15-fixture', annotation: fragmentAnnotation('W17.A02', 'full-pagination-controls', true),
}, async ({}, info) => {
  const connectionId = randomUUID();
  const ids = Array.from({ length: 21 }, () => randomUUID());
  await attachSeededIds(info, 'J15', { connectionIds: [connectionId], runIds: ids });
  const calls: number[] = [];
  const reader = (defect?: 'duplicate' | 'drift'): Pick<ApiClient, 'baseURL' | 'get'> => ({
    baseURL: 'http://127.0.0.1:8100',
    get: async <T>(path: string, params?: Record<string, string | number | boolean>): Promise<T> => {
      expect(path).toBe('/integrations/sync-logs/');
      expect(params?.connection_id).toBe(connectionId);
      expect(params?.page_size).toBe(20);
      const page = Number(params?.page_number); calls.push(page);
      return { status: true, result: {
        metadata: { total_count: defect === 'drift' && page === 1 ? 22 : 21,
          current_page: page, page_size: 20, total_pages: 2, next_page: page === 0 ? 1 : null },
        sync_logs: ids.slice(page * 20, page * 20 + 20).map(id => ({
          id: defect === 'duplicate' && page === 1 ? ids[0] : id,
          connection: connectionId, status: 'success', traces_fetched: 3,
        })),
      } } as T;
    },
  });
  expect((await readAllSyncPages(reader(), connectionId)).map(r => r.id)).toEqual(ids);
  expect(calls).toEqual([0, 1]);
  await expect(readAllSyncPages(reader('duplicate'), connectionId)).rejects.toThrow('EXPORT_SYNC_API_DUPLICATE');
  await expect(readAllSyncPages(reader('drift'), connectionId)).rejects.toThrow('EXPORT_SYNC_API_CHANGED');
  expect((await readAllSyncPages(reader(), connectionId)).map(r => r.id)).toEqual(ids);
});
