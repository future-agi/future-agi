import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { attachSeededIds } from '../../_shared/contract';
import { createMoneyProvider } from './provider';
import { compileAttempt, nativePath, type AttemptPlan, type WireEvent } from './wire';

// SOL-MONEY-02/03. NOT_RUN fixture qualification, not actual Go adapter execution
// or product-row coverage. Exact adapter predicates are pinned below; the real
// canonical/native adapter qualification remains an explicit runtime obligation.
const OUTPUT_WRITTEN = { timeout: 10_000, intervals: [10, 25, 50] };
const USAGE_WRITTEN = { timeout: 10_000, intervals: [10, 25, 50] };
const RESPONSE_CLOSE_OBSERVED = { timeout: 10_000, intervals: [10, 25, 50] };
type GeminiFrame = {
  candidates: { content: { role: string; parts: { text?: string }[] }; finishReason?: string }[];
  usageMetadata?: { promptTokenCount?: number; candidatesTokenCount?: number; totalTokenCount?: number };
};
function plan(changes: Partial<AttemptPlan> = {}): AttemptPlan {
  return { target: 'primary', dialect: 'gemini', model: `fixture-${randomUUID()}`, stream: true,
    usage: 'complete', inputTokens: 7, outputTokens: 7, outcome: 'finish', ...changes };
}
function decodeFrame(bytes: string): GeminiFrame {
  expect(bytes.startsWith('data: ')).toBe(true);
  return JSON.parse(bytes.slice(6).trim());
}
function wireFrames(events: WireEvent[]): GeminiFrame[] {
  return events.flatMap(e => e.kind === 'frame' || (e.kind === 'end' && e.bytes !== undefined)
    ? [decodeFrame(e.bytes!)] : []);
}
function streamReader(response: Response) {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  return async (): Promise<GeminiFrame> => {
    for (;;) {
      const boundary = buffer.indexOf('\n\n');
      if (boundary >= 0) {
        const bytes = buffer.slice(0, boundary + 2); buffer = buffer.slice(boundary + 2);
        return decodeFrame(bytes);
      }
      const chunk = await reader.read();
      expect(chunk.done, 'client must consume the next exact Gemini frame').toBe(false);
      buffer += decoder.decode(chunk.value, { stream: true });
      expect(buffer.length, 'bounded synthetic SSE frame').toBeLessThan(32_768);
    }
  };
}

for (const usage of ['complete', 'delayed', 'duplicate'] as const) {
  for (const phase of ['terminal-consumed', 'premature-after-output', 'premature-after-usage'] as const) {
    test(`MONEY-FIXTURE.gemini-adapter.${usage}.${phase}`, async ({}, info) => {
      const service = await createMoneyProvider({ barrierTimeoutMs: 15_000 });
      const p = plan({ usage });
      const scenario = service.register({ requestId: randomUUID(), attempts: [p] });
      const abort = new AbortController();
      try {
        await attachSeededIds(info, 'J11', { requestIds: [scenario.requestId] });
        const response = await fetch(scenario.baseUrl(p.target) + nativePath(p), {
          method: 'POST', redirect: 'error', signal: abort.signal,
          body: JSON.stringify({ contents: [{ role: 'user', parts: [{ text: scenario.marker }] }] }),
        });
        expect(response.status).toBe(200);
        const next = streamReader(response);
        expect(await next()).toEqual({ candidates: [{ content: { role: 'model', parts: [{ text: scenario.marker }] } }] });
        await expect.poll(() => scenario.snapshot().attempts[0]?.waiting, OUTPUT_WRITTEN).toBe('after-output');
        const reports = usage === 'duplicate' ? 2 : 1;
        if (phase !== 'premature-after-output') {
          scenario.release(0, 'after-output');
          for (let i = 0; i < reports; i++) {
            // da4c81cc62 gemini/stream.go:parseStreamData emits a Choice for this
            // candidate. gemini.go:343-362 forwards it and does NOT cancel yet.
            expect(await next()).toEqual({ candidates: [{ content: { role: 'model', parts: [] } }],
              usageMetadata: { promptTokenCount: 7, candidatesTokenCount: 7, totalTokenCount: 14 } });
          }
          await expect.poll(() => scenario.snapshot().attempts[0].waiting, USAGE_WRITTEN).toBe('after-usage');
          expect(scenario.snapshot().attempts[0].completeUsageFrames).toBe(reports);
        }
        if (phase === 'terminal-consumed') {
          scenario.release(0, 'after-usage');
          expect(await next()).toEqual({ candidates: [{ content: { role: 'model', parts: [] }, finishReason: 'STOP' }] });
          // Same terminal-consumption close policy as gemini.go:343-362; do NOT
          // wait for EOF or another fixture barrier before closing the upstream.
          abort.abort();
          await expect.poll(() => scenario.snapshot().attempts[0].terminal, RESPONSE_CLOSE_OBSERVED).toBe('finished');
          const row = scenario.snapshot().attempts[0];
          expect([row.frames, row.contentFrames, row.partialUsageFrames, row.completeUsageFrames])
            .toEqual([reports + 2, 1, 0, reports]);
          expect([row.waiting, row.reached, row.cancelledAt]).toEqual([null, 'after-usage', null]);
          service.verifyComplete();
        } else {
          abort.abort();
          await expect.poll(() => scenario.snapshot().attempts[0].terminal, RESPONSE_CLOSE_OBSERVED).toBe('client-cancelled');
          const boundary = phase === 'premature-after-output' ? 'after-output' : 'after-usage';
          const row = scenario.snapshot().attempts[0];
          expect([row.waiting, row.reached, row.cancelledAt]).toEqual([null, boundary, boundary]);
          expect(() => service.verifyComplete()).toThrow('FIXTURE_TERMINAL_MISMATCH');
        }
        expect(scenario.snapshot().violations).toEqual([]);
        await info.attach('money-gemini-observation', { contentType: 'application/json',
          body: JSON.stringify(scenario.snapshot()) });
      } finally { abort.abort(); await service.close(); }
    });
  }
}

test('MONEY-FIXTURE.gemini-partial-forwarding-baseline-dropped-restored', async ({}, info) => {
  const marker = `fixture-${randomUUID()}`;
  await attachSeededIds(info, 'J12', { requestIds: [randomUUID()] });
  const p = plan({ usage: 'partial' });
  const partial = wireFrames(compileAttempt(p, marker));
  expect(partial).toEqual([
    { candidates: [{ content: { role: 'model', parts: [{ text: marker }] } }] },
    { candidates: [{ content: { role: 'model', parts: [] } }], usageMetadata: { promptTokenCount: 7 } },
    { candidates: [{ content: { role: 'model', parts: [] }, finishReason: 'STOP' }] },
  ]);
  // Narrow predicate model, NOT a replacement adapter: da4c81cc62
  // gemini/stream.go creates Choices only from candidates (or prompt blocks);
  // gemini.go:343-362 forwards Choices then stops on finishReason. None of these
  // fixture frames is a prompt block. Token presence is pinned to gemini/usage.go
  // and genaifmt/token_usage.go: absent candidates count stays unmeasured here.
  const forwardedUsage = (frames: GeminiFrame[]) => {
    const result: NonNullable<GeminiFrame['usageMetadata']>[] = [];
    for (const frame of frames) {
      if (!frame.candidates.length) continue;
      if (frame.usageMetadata !== undefined) result.push(frame.usageMetadata);
      if (frame.candidates.some(c => c.finishReason)) break;
    }
    return result;
  };
  const assertPartial = (frames: GeminiFrame[]) => {
    expect(forwardedUsage(frames), 'GEMINI_PARTIAL_PROMPT_EVIDENCE').toEqual([{ promptTokenCount: 7 }]);
  };
  assertPartial(partial);
  const dropped = structuredClone(partial);
  dropped[1].candidates = []; // Exact former defect: raw usage exists but Choices do not.
  expect(forwardedUsage(dropped)).toEqual([]);
  expect(() => assertPartial(dropped)).toThrow('GEMINI_PARTIAL_PROMPT_EVIDENCE');
  assertPartial(partial);
  const absent = wireFrames(compileAttempt({ ...p, usage: 'absent' }, marker));
  expect(absent).toEqual([partial[0], partial[2]]);
  expect(forwardedUsage(absent)).toEqual([]);
  expect(() => assertPartial(absent)).toThrow('GEMINI_PARTIAL_PROMPT_EVIDENCE');
});

test('MONEY-FIXTURE.gemini-no-terminal-before-usage-barrier', () => {
  for (const usage of ['complete', 'delayed', 'duplicate'] as const) {
    const p = plan({ usage });
    const events = compileAttempt(p, `fixture-${randomUUID()}`);
    const boundary = events.findIndex(e => e.kind === 'barrier' && e.name === 'after-usage');
    expect(boundary).toBeGreaterThan(0);
    const before = wireFrames(events.slice(0, boundary));
    expect(before.flatMap(f => f.candidates.filter(c => c.finishReason))).toEqual([]);
    expect(events.slice(boundary).map(e => e.kind)).toEqual(['barrier', 'end']);
    expect(wireFrames(events.slice(boundary))).toEqual([
      { candidates: [{ content: { role: 'model', parts: [] }, finishReason: 'STOP' }] },
    ]);
    const cancel = compileAttempt({ ...p, outcome: 'cancel-after-usage' }, 'fixture-control');
    expect(cancel.filter(e => e.kind === 'end')).toEqual([]);
    expect(wireFrames(cancel).flatMap(f => f.candidates.filter(c => c.finishReason))).toEqual([]);
    expect(cancel.at(-1)).toEqual({ kind: 'barrier', name: 'after-usage', cancelOnly: true });
  }
});
