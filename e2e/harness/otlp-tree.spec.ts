import { test as unit, expect, type APIRequestContext } from '@playwright/test';
import { test } from '../lib/fixtures';
import { sendTrace, type SendTraceConfig } from '../lib/otlp';
import { E2E } from '../lib/env';
import { POLL } from '../lib/state-probe';

const root = '0000000000000001';
const child = '0000000000000002';
const grandchild = '0000000000000003';
const sibling = '0000000000000004';
const config: SendTraceConfig = {
  collectorUrl: 'http://localhost:0', apiKey: 'unused', secretKey: 'unused', projectName: 'tree',
  traceId: '11111111-1111-1111-1111-111111111111', rootSpanId: root, childSpanId: child,
  startTimeUnixNano: '1000000', endTimeUnixNano: '2000000',
};

unit('OTLP tree extension preserves the default two-span wire and typed extra members', async () => {
  const payloads: any[] = [];
  const req = { post: async (_url: string, options: { data: unknown }) => {
    payloads.push(options.data);
    return { status: () => 200 };
  } } as unknown as APIRequestContext;
  const defaults = await sendTrace(req, config);
  expect(defaults.spanIds).toEqual([root, child]);
  const defaultSpans = payloads[0].resourceSpans[0].scopeSpans[0].spans;
  expect(defaultSpans).toEqual([
    { traceId: config.traceId!.replaceAll('-', ''), spanId: root, parentSpanId: undefined,
      name: 'e2e.root', kind: 1, startTimeUnixNano: '1000000', endTimeUnixNano: '2000000',
      attributes: [], status: { code: 1 } },
    { traceId: config.traceId!.replaceAll('-', ''), spanId: child, parentSpanId: root,
      name: 'e2e.llm-call', kind: 1, startTimeUnixNano: '1000000', endTimeUnixNano: '2000000',
      attributes: [{ key: 'fi.span.kind', value: { stringValue: 'llm' } }], status: { code: 1 } },
  ]);
  await sendTrace(req, { ...config, additionalSpans: [] });
  expect(payloads[1]).toEqual(payloads[0]);
  const tree = await sendTrace(req, { ...config, additionalSpans: [
    { spanId: grandchild, parentSpanId: child, name: 'attempt', startTimeUnixNano: '1100000',
      endTimeUnixNano: '1900000', attributes: { array: [1, true, '01', { key: 'value' }] } },
    { spanId: sibling, parentSpanId: root, name: 'second-call', attributes: { 'fi.span.kind': 'llm' } },
  ] });
  expect(tree.spanIds).toEqual([root, child, grandchild, sibling]);
  const spans = payloads[2].resourceSpans[0].scopeSpans[0].spans;
  expect(spans.slice(0, 2)).toEqual(defaultSpans);
  expect(spans[2]).toMatchObject({ parentSpanId: child, startTimeUnixNano: '1100000', endTimeUnixNano: '1900000',
    attributes: [{ key: 'array', value: { arrayValue: { values: [
      { doubleValue: 1 }, { boolValue: true }, { stringValue: '01' },
      { kvlistValue: { values: [{ key: 'key', value: { stringValue: 'value' } }] } },
    ] } } }] });
  expect(spans[3].parentSpanId).toBe(root);
});

unit('OTLP tree rejects duplicate, zero, foreign and cyclic IDs and invalid timestamps before I/O', async () => {
  let sends = 0;
  const req = { post: async () => { sends++; throw new Error('must not send'); } } as unknown as APIRequestContext;
  const cases: [SendTraceConfig['additionalSpans'], RegExp][] = [
    [[{ spanId: root, parentSpanId: child, name: 'duplicate' }], /unique/],
    [[{ spanId: '0'.repeat(16), parentSpanId: root, name: 'zero' }], /nonzero hex/],
    [[{ spanId: grandchild, parentSpanId: sibling, name: 'foreign' }], /belong to this trace/],
    [[{ spanId: grandchild, parentSpanId: grandchild, name: 'self' }], /cycle/],
    [[{ spanId: grandchild, parentSpanId: sibling, name: 'a' },
      { spanId: sibling, parentSpanId: grandchild, name: 'b' }], /cycle/],
    [[{ spanId: grandchild, parentSpanId: root, name: 'time', startTimeUnixNano: '0' }], /positive uint64/],
    [[{ spanId: grandchild, parentSpanId: root, name: 'time', endTimeUnixNano: '1' }], /must not precede/],
    [[{ spanId: grandchild, parentSpanId: root, name: 'typed', attributes: { invalid: Infinity } }], /finite scalars/],
  ];
  for (const [additionalSpans, error] of cases) {
    await expect(sendTrace(req, { ...config, additionalSpans })).rejects.toThrow(error);
  }
  expect(sends).toBe(0);
});

test('OTLP nested generation and sibling calls retain hierarchy and independent usage in real storage',
  async ({ request, actor, probe }, testInfo) => {
    const projectName = `e2e-otlp-tree-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const seeded = await sendTrace(request, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName,
      rootSpanId: root, childSpanId: child, rootAttributes: { 'fi.span.kind': 'chain' },
      childAttributes: { 'llm.token_count.total': 18 }, additionalSpans: [
        { spanId: grandchild, parentSpanId: child, name: 'attempt', attributes: { 'fi.span.kind': 'chain', typed: true } },
        { spanId: sibling, parentSpanId: root, name: 'independent', attributes: { 'fi.span.kind': 'llm', 'llm.token_count.total': 18 } },
      ],
    });
    await testInfo.attach('seeded-tree', { body: JSON.stringify(seeded), contentType: 'application/json' });
    const read = () => probe.ch<{ id: string; parent_span_id: string; observation_type: string; total_tokens: number }>(
      'SELECT id, parent_span_id, observation_type, total_tokens FROM spans FINAL WHERE trace_id = {t:String} ORDER BY id',
      { t: seeded.traceId });
    await expect.poll(read, POLL.SPAN_VISIBLE).toEqual([
      { id: root, parent_span_id: '', observation_type: 'chain', total_tokens: 0 },
      { id: child, parent_span_id: root, observation_type: 'llm', total_tokens: 18 },
      { id: grandchild, parent_span_id: child, observation_type: 'chain', total_tokens: 0 },
      { id: sibling, parent_span_id: root, observation_type: 'llm', total_tokens: 18 },
    ]);
  });
