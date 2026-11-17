import { test, expect, type APIRequestContext } from '@playwright/test';
import { assertOtlpSuccess, sendTrace, sendOtlpExport } from '../lib/otlp';
import { deliverOtlp, retryAfterMs, type OtlpDeliveryRuntime, type OtlpRetryDiagnostic } from '../lib/otlp-delivery';
import { syntheticCopilotDetailsFixture } from './native-otlp-fixtures';

const cfg = { collectorUrl: 'http://localhost:0', apiKey: 'sentinel-api', secretKey: 'sentinel-secret', projectName: 'delivery' };
const wireRequest = { url: `${cfg.collectorUrl}/v1/traces`, data: '{"synthetic":"sentinel-payload"}',
  headers: { 'X-Api-Key': cfg.apiKey, 'X-Secret-Key': cfg.secretKey, 'Content-Type': 'application/json' } };
type Reply = { status?: number; hint?: string; body?: string; elapsed?: number; error?: string;
  textError?: boolean; disposeError?: boolean; disposeElapsed?: number };
function harness(replies: Reply[]) {
  const state = { mono: 0 };
  const calls: { url: string; options: Parameters<APIRequestContext['post']>[1]; time: number }[] = [];
  const disposed: number[] = [], sleeps: number[] = [], diagnostics: OtlpRetryDiagnostic[] = [];
  const clock: OtlpDeliveryRuntime = {
    monotonicNow: () => state.mono,
    sleep: async ms => { sleeps.push(ms); state.mono += ms; },
    diagnostic: entry => diagnostics.push(entry),
  };
  const req = { post: async (url: string, options: Parameters<APIRequestContext['post']>[1]) => {
    const index = calls.length;
    calls.push({ url, options: structuredClone(options), time: state.mono });
    const reply = replies[index];
    if (!reply) throw new Error('unexpected extra POST');
    state.mono += reply.elapsed ?? 0;
    if (reply.error) throw new Error(reply.error);
    return { status: () => reply.status ?? 200,
      headers: () => reply.hint === undefined ? {} : { 'retry-after': reply.hint },
      text: async () => { if (reply.textError) throw new Error('sentinel-body'); return reply.body ?? '{}'; },
      dispose: async () => {
        disposed.push(index); state.mono += reply.disposeElapsed ?? 0;
        if (reply.disposeError) throw new Error('sentinel-cleanup');
      },
    };
  } } as unknown as APIRequestContext;
  return { state, calls, disposed, sleeps, diagnostics, clock, req,
    deliver: (request = wireRequest) => deliverOtlp(req, request, assertOtlpSuccess, clock) };
}

for (const [header, expected] of [
  ['1', 1000], ['5', 5000], ['0', 0], ['  1  ', 1000],
  // HTTP-dates are deliberately not hints: the collector never sends them, and
  // parsing them invited timezone (asctime) and impossible-date (31 Feb) bugs.
  ['Tue, 10 Nov 2026 12:00:05 GMT', undefined], ['Tuesday, 10-Nov-26 12:00:05 GMT', undefined],
  ['Tue Nov 10 12:00:05 2026', undefined], ['Tue, 31 Feb 2026 12:00:05 GMT', undefined],
  [undefined, undefined], ['', undefined], ['-1', undefined], ['1.5', undefined],
  ['+1', undefined], ['1e3', undefined], ['tomorrow', undefined], ['2026', 2026000],
  ['Tue, 99 Nov 2026 12:00:05 GMT', undefined], ['9007199254740991', undefined],
  ['9'.repeat(400), undefined],
] as const) {
  test(`H-OTLP-DELIVERY Retry-After parser ${String(header).slice(0, 50)}`, () => {
    expect(retryAfterMs(header)).toBe(expected);
  });
}

for (const status of [503, 429]) {
  test(`H-OTLP-DELIVERY ${status} obeys not-before and resends identical bytes and credentials`, async () => {
    const h = harness([{ status, hint: '5', elapsed: 250 }, {}]);
    await h.deliver();
    expect(h.calls.map(c => c.time)).toEqual([0, 5250]);
    expect(h.sleeps).toEqual([5000]);
    expect(h.disposed).toEqual([0, 1]);
    for (const call of h.calls) {
      expect(call.url).toBe(wireRequest.url);
      expect(call.options).toMatchObject({ data: wireRequest.data, headers: wireRequest.headers,
        maxRetries: 0, maxRedirects: 0, failOnStatusCode: false });
    }
    expect(h.calls.map(c => c.options!.timeout)).toEqual([30000, 24750]);
    expect(h.diagnostics).toEqual([{ event: 'otlp-delivery-retry', payloadDigest: expect.stringMatching(/^[a-f0-9]{64}$/),
      attempt: 1, nextAttempt: 2, status, delayMs: 5000, elapsedMs: 250, remainingMs: 29750 }]);
  });
}

test('H-OTLP-DELIVERY public export freezes caller fixture and credentials before resend', async () => {
  const fixture = structuredClone(syntheticCopilotDetailsFixture({ projectName: cfg.projectName }));
  const original = JSON.stringify(fixture), config = { ...cfg };
  const h = harness([{ status: 429, hint: '0' }, {}]);
  const post = h.req.post.bind(h.req);
  h.req.post = async (url, options) => {
    const res = await post(url, options);
    fixture.resourceSpans[0].scopeSpans[0].spans[0].name = 'mutated';
    config.apiKey = 'mutated'; config.collectorUrl = 'http://wrong';
    return res;
  };
  const receipt = await sendOtlpExport(h.req, config, fixture);
  expect(h.calls.map(c => c.options!.data)).toEqual([original, original]);
  expect(h.calls.map(c => c.url)).toEqual([wireRequest.url, wireRequest.url]);
  expect(h.calls.map(c => c.options!.headers)).toEqual([wireRequest.headers, wireRequest.headers]);
  expect(receipt.spans[0]).toMatchObject({ traceId: '01234567-89ab-cdef-0123-456789abcdef',
    spanId: '0000000000000001', projectName: cfg.projectName, scope: { name: 'github.copilot', version: '1.0.98' } });
  expect(h.disposed).toEqual([0, 1]);
});

test('H-OTLP-DELIVERY mixed refusals exhaust exactly four total attempts', async () => {
  const h = harness([... [503, 429, 503, 429].map(status => ({ status, hint: '0' })), {}]);
  await expect(h.deliver()).rejects.toThrow(/budget exhausted: attempts=4 elapsedMs=0 lastStatus=429/);
  expect(h.calls).toHaveLength(4);
  expect(h.diagnostics.map(d => [d.attempt, d.nextAttempt])).toEqual([[1, 2], [2, 3], [3, 4]]);
  expect(h.disposed).toEqual([0, 1, 2, 3]);
  expect(h.sleeps).toEqual([]);
});

test('H-OTLP-DELIVERY request time consumes one absolute deadline', async () => {
  const h = harness([{ status: 503, hint: '1', elapsed: 10000 }, { status: 503, hint: '10', elapsed: 10000 }, {}]);
  await expect(h.deliver()).rejects.toThrow(/attempts=2 elapsedMs=21000 lastStatus=503/);
  expect(h.calls.map(c => c.options!.timeout)).toEqual([30000, 19000]);
  expect(h.sleeps).toEqual([1000]);
  expect(h.diagnostics).toHaveLength(1);
  expect(h.disposed).toEqual([0, 1]);
});

for (const hint of ['30', '31', '9007199254740']) {
  test(`H-OTLP-DELIVERY refuses a delay outside remaining budget ${hint}`, async () => {
    const h = harness([{ status: 503, hint }, {}]);
    await expect(h.deliver()).rejects.toThrow(/budget exhausted/);
    expect(h.calls).toHaveLength(1); expect(h.sleeps).toEqual([]); expect(h.diagnostics).toEqual([]);
    expect(h.disposed).toEqual([0]);
  });
}

test('H-OTLP-DELIVERY early timer wake never sends early', async () => {
  const h = harness([{ status: 503, hint: '5' }, {}]);
  h.clock.sleep = async ms => {
    h.sleeps.push(ms); h.state.mono += h.sleeps.length === 1 ? 1000 : ms;
  };
  await h.deliver();
  expect(h.sleeps).toEqual([5000, 4000]);
  expect(h.calls.map(c => c.time)).toEqual([0, 5000]);
  expect(h.calls[1].options!.timeout).toBe(25000);
});

test('H-OTLP-DELIVERY late scheduler wake and cleanup cannot send beyond deadline', async () => {
  const h = harness([{ status: 503, hint: '1' }, {}]);
  h.clock.sleep = async ms => { h.sleeps.push(ms); h.state.mono = 31000; };
  await expect(h.deliver()).rejects.toThrow(/attempts=1 elapsedMs=31000/);
  expect(h.calls).toHaveLength(1); expect(h.sleeps).toEqual([1000]);
  const cleanup = harness([{ status: 503, hint: '1', disposeElapsed: 30000 }, {}]);
  await expect(cleanup.deliver()).rejects.toThrow(/budget exhausted/);
  expect(cleanup.calls).toHaveLength(1); expect(cleanup.sleeps).toEqual([]);
});

test('H-OTLP-DELIVERY stalled request gets only remaining timeout and never retries', async () => {
  const h = harness([{ status: 503, hint: '1', elapsed: 28000 }, { error: 'timeout sentinel-secret', elapsed: 1000 }, {}]);
  await expect(h.deliver()).rejects.toThrow(/^OTLP transport failed: attempt=2$/);
  expect(h.calls.map(c => c.options!.timeout)).toEqual([30000, 1000]);
  expect(h.sleeps).toEqual([1000]); expect(h.disposed).toEqual([0]);
});

test('H-OTLP-DELIVERY response arriving at the deadline is terminal and disposed', async () => {
  const h = harness([{ elapsed: 30000 }, {}]);
  await expect(h.deliver()).rejects.toThrow(/attempts=1 elapsedMs=30000 lastStatus=200/);
  expect(h.calls).toHaveLength(1); expect(h.disposed).toEqual([0]);
});

for (const [status, hint] of [
  [400, '1'], [401, '1'], [403, '1'], [413, '1'], [415, '1'], [500, '1'], [302, '1'], [307, '1'],
  [429, undefined], [503, undefined], [503, '-1'], [429, '1.5'], [503, 'invalid'],
] as const) {
  test(`H-OTLP-DELIVERY terminal HTTP ${status} hint ${hint}`, async () => {
    const h = harness([{ status, hint, body: 'hostile sentinel-secret server text' }, {}]);
    await expect(h.deliver()).rejects.toThrow(new RegExp(`^collector HTTP ${status}$`));
    expect(h.calls).toHaveLength(1); expect(h.sleeps).toEqual([]); expect(h.diagnostics).toEqual([]);
    expect(h.disposed).toEqual([0]);
  });
}

test('H-OTLP-DELIVERY permanent response after a retry stops immediately', async () => {
  const h = harness([{ status: 503, hint: '1' }, { status: 401 }, {}]);
  await expect(h.deliver()).rejects.toThrow(/^collector HTTP 401$/);
  expect(h.calls).toHaveLength(2); expect(h.diagnostics).toHaveLength(1); expect(h.disposed).toEqual([0, 1]);
});

for (const error of ['ECONNRESET', 'timeout', 'context disposed']) {
  test(`H-OTLP-DELIVERY ambiguous transport ${error} is not replayed`, async () => {
    const h = harness([{ error: `${error} sentinel-secret` }, {}]);
    await expect(h.deliver()).rejects.toThrow(/^OTLP transport failed: attempt=1$/);
    expect(h.calls).toHaveLength(1); expect(h.sleeps).toEqual([]); expect(h.diagnostics).toEqual([]);
  });
}

for (const body of ['', '{}', '{"partialSuccess":{"rejectedSpans":"0"}}']) {
  test(`H-OTLP-DELIVERY clean receipt ${body}`, async () => {
    const h = harness([{ body }]);
    await h.deliver(); expect(h.calls).toHaveLength(1); expect(h.disposed).toEqual([0]);
  });
}
for (const body of ['not-json', '{"partialSuccess":{"rejectedSpans":"1"}}',
  '{"partial_success":{"rejected_spans":1}}', '{"partialSuccess":{"rejectedSpans":0,"errorMessage":"sentinel-secret"}}',
  '{"partial_success":{"rejected_spans":0,"error_message":"sentinel-secret"}}']) {
  test(`H-OTLP-DELIVERY invalid receipt ${body}`, async () => {
    const h = harness([{ body, disposeError: true }, {}]);
    await expect(h.deliver()).rejects.toThrow(/invalid OTLP JSON|partial_success/);
    expect(h.calls).toHaveLength(1); expect(h.disposed).toEqual([0]); expect(h.diagnostics).toEqual([]);
  });
}

test('H-OTLP-DELIVERY cleanup never masks primary errors; unreadable receipts fail safely', async () => {
  const refused = harness([{ status: 403, disposeError: true }]);
  await expect(refused.deliver()).rejects.toThrow(/^collector HTTP 403$/);
  const unreadable = harness([{ textError: true, disposeError: true }]);
  await expect(unreadable.deliver()).rejects.toThrow(/^OTLP receipt read failed$/);
  const success = harness([{ disposeError: true }]);
  await success.deliver(); expect(success.disposed).toEqual([0]);
});

test('H-OTLP-DELIVERY concurrent operations have independent budgets and safe attributable diagnostics', async () => {
  const a = harness([{ status: 503, hint: '1', body: 'hostile sentinel-body' }, {}]);
  const b = harness([{ status: 429, hint: '5' }, { status: 503, hint: '1' }, {}]);
  await Promise.all([a.deliver(), b.deliver({ ...wireRequest, data: 'different-payload' })]);
  expect(a.calls.map(c => c.time)).toEqual([0, 1000]);
  expect(b.calls.map(c => c.time)).toEqual([0, 5000, 6000]);
  expect(a.diagnostics.map(d => d.attempt)).toEqual([1]);
  expect(b.diagnostics.map(d => d.attempt)).toEqual([1, 2]);
  expect(b.diagnostics[1].payloadDigest).toBe(b.diagnostics[0].payloadDigest);
  expect(b.diagnostics[0].payloadDigest).not.toBe(a.diagnostics[0].payloadDigest);
  expect(a.calls.every(c => c.options!.data === wireRequest.data)).toBe(true);
  expect(b.calls.every(c => c.options!.data === 'different-payload')).toBe(true);
  const logged = JSON.stringify([...a.diagnostics, ...b.diagnostics]);
  for (const secret of ['sentinel-api', 'sentinel-secret', 'sentinel-payload', 'sentinel-body', 'hostile', 'X-Api-Key']) {
    expect(logged).not.toContain(secret);
  }
});

// This regression uses only the existing public helper. It was also run in
// isolation against the pre-fix implementation (red on collector HTTP 503).
// This one integration check exercises the real server-directed timer; all
// delivery state-machine cases above use a deterministic clock, without sleeps.
test('H-OTLP-DELIVERY public sendTrace recovers from an explicit one-second refusal', async () => {
  const calls: { wire: string; time: number }[] = [];
  const req = { post: async (_url: string, options: { data: unknown }) => {
    calls.push({ wire: typeof options.data === 'string' ? options.data : JSON.stringify(options.data), time: performance.now() });
    return { status: () => calls.length === 1 ? 503 : 200,
      headers: () => ({ 'retry-after': '1' }), text: async () => '{}', dispose: async () => {} };
  } } as unknown as APIRequestContext;
  const receipt = await sendTrace(req, { collectorUrl: 'http://localhost:0', apiKey: 'unused', secretKey: 'unused',
    projectName: 'delivery-regression', traceId: '11111111111141118111111111111111',
    rootSpanId: '0000000000000001', childSpanId: '0000000000000002',
    startTimeUnixNano: '1000000', endTimeUnixNano: '2000000' });
  expect(calls).toHaveLength(2);
  expect(calls[1].time - calls[0].time).toBeGreaterThanOrEqual(1000);
  expect(calls[1].wire).toBe(calls[0].wire);
  expect(receipt).toEqual({ traceId: '11111111-1111-4111-8111-111111111111',
    spanIds: ['0000000000000001', '0000000000000002'], projectName: 'delivery-regression' });
});
