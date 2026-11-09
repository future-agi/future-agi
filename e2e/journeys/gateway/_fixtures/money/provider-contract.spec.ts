import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { compileAttempt, nativePath, nonStreamBody, matchesRequest, type Dialect, type AttemptPlan } from './wire';
import { createMoneyProvider } from './provider';

// Fixture qualification only; NOT selected as GW-JOURNEY or product row coverage.
// Written before implementation, NOT_RUN locally. Run later inside the accepted
// guard, at the pinned consumer lockfile, before any money journey admission.
const OUTPUT_WRITTEN = { timeout: 10_000, intervals: [10, 25, 50] };
const CLIENT_CLOSE_OBSERVED = { timeout: 10_000, intervals: [10, 25, 50] };
function plan(dialect: Dialect, changes: Partial<AttemptPlan> = {}): AttemptPlan {
  return { target: 'primary', dialect, model: `fixture-${randomUUID()}`, stream: true,
    usage: 'complete', inputTokens: 7, outputTokens: 7, outcome: 'finish', ...changes };
}
function body(p: AttemptPlan, marker: string) {
  return p.dialect === 'gemini' ? { contents: [{ role: 'user', parts: [{ text: marker }] }] }
    : { model: p.model, stream: p.stream, messages: [{ role: 'user', content: marker }],
      ...(p.dialect === 'anthropic' ? { max_tokens: 32 } : {}) };
}

for (const dialect of ['openai', 'anthropic', 'gemini'] as const) {
  test(`MONEY-FIXTURE.${dialect}.complete-absent-partial-duplicate`, async () => {
    const p = plan(dialect);
    const complete = compileAttempt(p, 'fixture-output');
    expect(complete.filter(e => e.kind === 'frame' && e.usage === 'complete')).toHaveLength(1);
    expect(complete.filter(e => e.kind === 'barrier').map(e => e.name))
      .toEqual(['after-output', 'after-usage']);
    const absent = compileAttempt({ ...p, usage: 'absent' }, 'fixture-output');
    expect(absent.filter(e => e.kind === 'frame' && e.usage !== 'none')).toEqual([]);
    const partial = compileAttempt({ ...p, usage: 'partial' }, 'fixture-output');
    expect(partial.filter(e => e.kind === 'frame' && e.usage === 'complete')).toEqual([]);
    expect(partial.filter(e => e.kind === 'frame' && e.usage === 'partial')).toHaveLength(1);
    const duplicate = compileAttempt({ ...p, usage: 'duplicate' }, 'fixture-output');
    const reports = duplicate.filter(e => e.kind === 'frame' && e.usage === 'complete');
    expect(reports).toHaveLength(2);
    expect(reports[0]).toEqual(reports[1]);
  });

  test(`MONEY-FIXTURE.${dialect}.real-stream-barriers`, async () => {
    const service = await createMoneyProvider({ barrierTimeoutMs: 15_000 });
    const p = plan(dialect, { usage: 'delayed' });
    const scenario = service.register({ requestId: randomUUID(), attempts: [p] });
    const abort = new AbortController();
    try {
      const response = await fetch(scenario.baseUrl('primary') + nativePath(p), {
        method: 'POST', body: JSON.stringify(body(p, scenario.marker)), signal: abort.signal,
        redirect: 'error', headers: { 'content-type': 'application/json' },
      });
      expect(response.status).toBe(200);
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let first = '';
      while (!first.includes(scenario.marker)) {
        const next = await reader.read();
        expect(next.done, 'client receives the minted output before terminal').toBe(false);
        first += decoder.decode(next.value, { stream: true });
      }
      await expect.poll(() => scenario.snapshot().attempts[0]?.waiting, OUTPUT_WRITTEN).toBe('after-output');
      expect(scenario.snapshot().attempts[0].completeUsageFrames).toBe(0);
      expect(() => scenario.release(0, 'after-usage')).toThrow('BARRIER_NOT_WAITING');
      scenario.release(0, 'after-output');
      await expect.poll(() => scenario.snapshot().attempts[0].waiting, OUTPUT_WRITTEN).toBe('after-usage');
      expect(scenario.snapshot().attempts[0].completeUsageFrames).toBe(1);
      scenario.release(0, 'after-usage');
      let tail = '';
      for (;;) { const next = await reader.read(); if (next.done) break; tail += decoder.decode(next.value, { stream: true }); }
      expect(tail.length).toBeGreaterThan(0);
      await expect.poll(() => scenario.snapshot().attempts[0].terminal, OUTPUT_WRITTEN).toBe('finished');
      expect(scenario.snapshot().violations).toEqual([]);
      service.verifyComplete();
    } finally { abort.abort(); await service.close(); }
  });
}

test('MONEY-FIXTURE.cancel-before-after-usage-and-midstream-disconnect', async () => {
  for (const outcome of ['cancel-before-usage', 'cancel-after-usage', 'disconnect-after-output'] as const) {
    const service = await createMoneyProvider({ barrierTimeoutMs: 15_000 });
    const p = plan('openai', { outcome, usage: outcome === 'cancel-after-usage' ? 'complete' : 'absent' });
    const scenario = service.register({ requestId: randomUUID(), attempts: [p] });
    const abort = new AbortController();
    try {
      const response = await fetch(scenario.baseUrl('primary') + nativePath(p), {
        method: 'POST', body: JSON.stringify(body(p, scenario.marker)), signal: abort.signal, redirect: 'error',
      });
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let received = '';
      while (!received.includes(scenario.marker)) {
        const next = await reader.read();
        expect(next.done, 'client observes output before cancellation').toBe(false);
        received += decoder.decode(next.value, { stream: true });
      }
      await expect.poll(() => scenario.snapshot().attempts[0]?.waiting, OUTPUT_WRITTEN).toBe('after-output');
      if (outcome === 'cancel-after-usage') {
        scenario.release(0, 'after-output');
        await expect.poll(() => scenario.snapshot().attempts[0].waiting, OUTPUT_WRITTEN).toBe('after-usage');
        expect(() => scenario.release(0, 'after-usage')).toThrow('CLIENT_CANCEL_REQUIRED');
        while (!received.includes('"completion_tokens":7')) {
          const next = await reader.read();
          expect(next.done, 'client observes authoritative fixture usage before cancellation').toBe(false);
          received += decoder.decode(next.value, { stream: true });
        }
        abort.abort();
      } else if (outcome === 'cancel-before-usage') {
        expect(() => scenario.release(0, 'after-output')).toThrow('CLIENT_CANCEL_REQUIRED');
        abort.abort();
      } else scenario.release(0, 'after-output');
      await expect.poll(() => scenario.snapshot().attempts[0].terminal, CLIENT_CLOSE_OBSERVED)
        .toBe(outcome === 'disconnect-after-output' ? 'provider-disconnected' : 'client-cancelled');
      expect(scenario.snapshot().attempts[0].completeUsageFrames).toBe(outcome === 'cancel-after-usage' ? 1 : 0);
      expect(scenario.snapshot().attempts[0].cancelledAt).toBe(outcome === 'disconnect-after-output'
        ? null : outcome === 'cancel-after-usage' ? 'after-usage' : 'after-output');
      expect(scenario.snapshot().violations).toEqual([]);
      service.verifyComplete();
    } finally { abort.abort(); await service.close(); }
  }
});

test('MONEY-FIXTURE.retry-fallback-exact-order-and-no-extra-call', async () => {
  const service = await createMoneyProvider({ barrierTimeoutMs: 15_000 });
  const primary = plan('openai', { stream: false, outcome: 'http-503' });
  const fallback = { ...primary, target: 'fallback', outcome: 'finish' as const };
  const scenario = service.register({ requestId: randomUUID(), attempts: [primary, primary, fallback] });
  try {
    for (const [i, p] of [primary, primary, fallback].entries()) {
      const response = await fetch(scenario.baseUrl(p.target) + nativePath(p), {
        method: 'POST', body: JSON.stringify(body(p, scenario.marker)), redirect: 'error',
      });
      expect(response.status).toBe(i === 2 ? 200 : 503);
      await response.arrayBuffer();
    }
    expect(scenario.snapshot().attempts.map(a => [a.index, a.target, a.status]))
      .toEqual([[0, 'primary', 503], [1, 'primary', 503], [2, 'fallback', 200]]);
    const extra = await fetch(scenario.baseUrl('fallback') + nativePath(fallback), {
      method: 'POST', body: JSON.stringify(body(fallback, scenario.marker)), redirect: 'error',
    });
    expect(extra.status).toBe(409);
    await extra.arrayBuffer();
    expect(scenario.snapshot().violations).toEqual(['UNEXPECTED_ATTEMPT']);
    expect(() => service.verifyComplete()).toThrow('FIXTURE_ATTEMPT_INVENTORY_INCOMPLETE');
  } finally { await service.close(); }
});

test('MONEY-FIXTURE.synthetic-BYOK-credential-equality-never-emits-value', async () => {
  const service = await createMoneyProvider({ barrierTimeoutMs: 15_000 });
  const p = plan('openai', { stream: false });
  const scenario = service.register({ requestId: randomUUID(), attempts: [p], syntheticCredential: true });
  try {
    const response = await fetch(scenario.baseUrl('primary') + nativePath(p), {
      method: 'POST', body: JSON.stringify(body(p, scenario.marker)), redirect: 'error',
      headers: { authorization: `Bearer ${scenario.upstreamCredential}` },
    });
    expect(response.status).toBe(200); await response.arrayBuffer();
    await expect.poll(() => scenario.snapshot().attempts[0].terminal, OUTPUT_WRITTEN).toBe('finished');
    expect(scenario.snapshot().attempts.map(a => a.credentialMatches)).toEqual([true]);
    // Boolean assertion only: Playwright must not display credential bytes on a failure.
    expect(JSON.stringify(scenario.snapshot()).includes(scenario.upstreamCredential!), 'safe projection excludes synthetic credential').toBe(false);
    service.verifyComplete();
  } finally { await service.close(); }
});

test('MONEY-FIXTURE.native-wire-fields-are-exact', () => {
  const openai = plan('openai', { stream: false, model: 'fixture-model' });
  expect(nonStreamBody(openai, 'minted-output')).toEqual({ id: 'minted-output', object: 'chat.completion',
    model: 'fixture-model', choices: [{ index: 0, message: { role: 'assistant', content: 'minted-output' }, finish_reason: 'stop' }],
    usage: { prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 } });
  expect(nonStreamBody({ ...openai, dialect: 'anthropic' }, 'minted-output')).toEqual({ id: 'minted-output',
    type: 'message', role: 'assistant', model: 'fixture-model', content: [{ type: 'text', text: 'minted-output' }],
    stop_reason: 'end_turn', usage: { input_tokens: 7, output_tokens: 7 } });
  expect(nonStreamBody({ ...openai, dialect: 'gemini' }, 'minted-output')).toEqual({
    candidates: [{ content: { role: 'model', parts: [{ text: 'minted-output' }] }, finishReason: 'STOP' }],
    modelVersion: 'fixture-model', usageMetadata: { promptTokenCount: 7, candidatesTokenCount: 7, totalTokenCount: 14 } });
  for (const dialect of ['openai', 'anthropic', 'gemini'] as const) {
    const p = { ...openai, dialect };
    const missing = nonStreamBody({ ...p, usage: 'absent' }, 'minted-output') as Record<string, unknown>;
    expect(Object.keys(missing).filter(k => k === 'usage' || k === 'usageMetadata')).toEqual([]);
    const partial = nonStreamBody({ ...p, usage: 'partial' }, 'minted-output') as Record<string, unknown>;
    expect(partial[dialect === 'gemini' ? 'usageMetadata' : 'usage']).toEqual(
      dialect === 'openai' ? { prompt_tokens: 7 } : dialect === 'anthropic' ? { input_tokens: 7 } : { promptTokenCount: 7 });
  }
});

test('MONEY-FIXTURE.native-and-translated-message-anchor', () => {
  const p = plan('anthropic');
  expect(matchesRequest(p, body(p, 'minted-output'), 'minted-output')).toBe(true);
  const translated = { ...body(p, 'minted-output'), messages: [{ role: 'user',
    content: [{ type: 'text', text: 'minted-output' }] }] };
  expect(matchesRequest(p, translated, 'minted-output')).toBe(true);
  expect(matchesRequest(p, translated, 'wrong-output')).toBe(false);
  expect(matchesRequest(p, { ...translated, stream: 'true' }, 'minted-output')).toBe(false);
  expect(matchesRequest(p, { ...translated, messages: [...translated.messages, ...translated.messages] }, 'minted-output')).toBe(false);
});

test('MONEY-FIXTURE.closed-plans-refuse-invalid-cells', async () => {
  expect(() => compileAttempt(plan('openai', { inputTokens: -1 }), 'fixture')).toThrow();
  expect(() => compileAttempt(plan('openai', { stream: false, usage: 'delayed' }), 'fixture')).toThrow();
  expect(() => compileAttempt(plan('openai', { usage: 'complete', outcome: 'cancel-before-usage' }), 'fixture')).toThrow();
  expect(() => compileAttempt(plan('openai', { target: '../escape' }), 'fixture')).toThrow();
});
