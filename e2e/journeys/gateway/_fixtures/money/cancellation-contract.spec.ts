import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { attachSeededIds } from '../../_shared/contract';
import { assertAttemptComplete, createMoneyProvider, type AttemptObservation } from './provider';
import { compileAttempt, nativePath, type AttemptPlan } from './wire';

// SOL-MONEY-01 regression controls. Source-only qualification, NOT product rows.
// These controls are NOT_RUN here; actual client reads require the accepted guard.
const OUTPUT_WRITTEN = { timeout: 10_000, intervals: [10, 25, 50] };
const USAGE_WRITTEN = { timeout: 10_000, intervals: [10, 25, 50] };
const CLIENT_CLOSE_OBSERVED = { timeout: 10_000, intervals: [10, 25, 50] };

for (const dialect of ['openai', 'anthropic', 'gemini'] as const) {
  for (const usage of ['complete', 'delayed', 'duplicate'] as const) {
    for (const phase of ['wrong-after-output', 'restored-after-usage'] as const) {
      test(`MONEY-FIXTURE.cancel-boundary.${dialect}.${usage}.${phase}`, async ({}, info) => {
        const p: AttemptPlan = { target: 'primary', dialect, model: `fixture-${randomUUID()}`,
          stream: true, usage, inputTokens: 7, outputTokens: 7, outcome: 'cancel-after-usage' };
        const service = await createMoneyProvider({ barrierTimeoutMs: 15_000 });
        const scenario = service.register({ requestId: randomUUID(), attempts: [p] });
        const abort = new AbortController();
        try {
          await attachSeededIds(info, 'J12', { requestIds: [scenario.requestId] });
          // Request serializer provenance: wire.ts cites the three native adapters
          // at da4c81cc62. No guessed headers or product request IDs are used.
          const body = dialect === 'gemini'
            ? { contents: [{ role: 'user', parts: [{ text: scenario.marker }] }] }
            : { model: p.model, stream: true, messages: [{ role: 'user', content: scenario.marker }],
              ...(dialect === 'anthropic' ? { max_tokens: 32 } : {}) };
          const response = await fetch(scenario.baseUrl(p.target) + nativePath(p), {
            method: 'POST', body: JSON.stringify(body), redirect: 'error', signal: abort.signal,
          });
          expect(response.status).toBe(200);
          const reader = response.body!.getReader();
          const decoder = new TextDecoder();
          let received = '';
          while (!received.includes(scenario.marker)) {
            const next = await reader.read();
            expect(next.done, 'real client must receive the minted output').toBe(false);
            received += decoder.decode(next.value, { stream: true });
          }
          await expect.poll(() => scenario.snapshot().attempts[0]?.waiting, OUTPUT_WRITTEN).toBe('after-output');
          expect(scenario.snapshot().attempts[0].completeUsageFrames).toBe(0);
          const expectedReports = usage === 'duplicate' ? 2 : 1;
          if (phase === 'restored-after-usage') {
            scenario.release(0, 'after-output');
            await expect.poll(() => scenario.snapshot().attempts[0].waiting, USAGE_WRITTEN).toBe('after-usage');
            const field = dialect === 'openai' ? 'completion_tokens'
              : dialect === 'anthropic' ? 'output_tokens' : 'candidatesTokenCount';
            // Count client-consumed reports, not merely server-side write callbacks.
            while (received.split(`"${field}":7`).length - 1 < expectedReports) {
              const next = await reader.read();
              expect(next.done, 'client must consume the exact complete usage inventory').toBe(false);
              received += decoder.decode(next.value, { stream: true });
            }
            expect(received.split(`"${field}":7`).length - 1).toBe(expectedReports);
            expect(() => scenario.release(0, 'after-usage')).toThrow('CLIENT_CANCEL_REQUIRED');
          }
          abort.abort();
          await expect.poll(() => scenario.snapshot().attempts[0].terminal, CLIENT_CLOSE_OBSERVED).toBe('client-cancelled');
          const snapshot = scenario.snapshot();
          expect(snapshot.violations).toEqual([]);
          expect(snapshot.attempts).toHaveLength(1);
          const row = snapshot.attempts[0];
          expect(row.waiting).toBeNull();
          expect(row.partialUsageFrames).toBe(0);
          if (phase === 'wrong-after-output') {
            expect([row.reached, row.cancelledAt, row.completeUsageFrames]).toEqual(['after-output', 'after-output', 0]);
            expect(() => service.verifyComplete()).toThrow('FIXTURE_CANCEL_BOUNDARY_MISMATCH');
          } else {
            expect([row.reached, row.cancelledAt, row.completeUsageFrames]).toEqual(['after-usage', 'after-usage', expectedReports]);
            service.verifyComplete();
            // Oracle-only multiplicity controls; do not mutate the live observation.
            for (const completeUsageFrames of expectedReports === 2 ? [0, 1, 3] : [0, 2]) {
              expect(() => assertAttemptComplete(p, { ...row, completeUsageFrames }))
                .toThrow('FIXTURE_CANCEL_FRAME_MISMATCH');
            }
            assertAttemptComplete(p, row);
          }
          await info.attach('money-cancel-observation', { contentType: 'application/json',
            body: JSON.stringify(snapshot) });
        } finally { abort.abort(); await service.close(); }
      });
    }

    test(`MONEY-FIXTURE.cancel-boundary.${dialect}.${usage}.before-output-oracle`, async ({}, info) => {
      // Deterministic data-only pre-output close control. No claim this executes
      // a native transport abort: native adapter/early-close qualification is owed.
      const requestId = randomUUID();
      await attachSeededIds(info, 'J12', { requestIds: [requestId] });
      const p: AttemptPlan = { target: 'primary', dialect, model: `fixture-${randomUUID()}`,
        stream: true, usage, inputTokens: 7, outputTokens: 7, outcome: 'cancel-after-usage' };
      const frames = compileAttempt(p, `fixture-${randomUUID()}`).filter(e => e.kind === 'frame');
      const restored: AttemptObservation = { index: 0, target: p.target, dialect, status: 200,
        credentialMatches: null, frames: frames.length, contentFrames: 1, partialUsageFrames: 0,
        completeUsageFrames: usage === 'duplicate' ? 2 : 1, waiting: null,
        reached: 'after-usage', cancelledAt: 'after-usage', terminal: 'client-cancelled' };
      assertAttemptComplete(p, restored);
      const premature: AttemptObservation = { ...restored, status: 0, frames: 0,
        contentFrames: 0, completeUsageFrames: 0, reached: null, cancelledAt: 'before-output' };
      expect(() => assertAttemptComplete(p, premature)).toThrow('FIXTURE_CANCEL_BOUNDARY_MISMATCH');
      assertAttemptComplete(p, restored);
      // Before-usage plans require output, no partial/complete report and the
      // after-output close, even when their terminal label looks right.
      const beforePlan: AttemptPlan = { ...p, usage: 'absent', outcome: 'cancel-before-usage' };
      const beforeFrames = compileAttempt(beforePlan, 'fixture-control').filter(e => e.kind === 'frame');
      const before: AttemptObservation = { ...restored, frames: beforeFrames.length,
        completeUsageFrames: 0, reached: 'after-output', cancelledAt: 'after-output' };
      assertAttemptComplete(beforePlan, before);
      expect(() => assertAttemptComplete(beforePlan, { ...before, completeUsageFrames: 1 }))
        .toThrow('FIXTURE_CANCEL_FRAME_MISMATCH');
      expect(() => assertAttemptComplete(beforePlan, { ...before, partialUsageFrames: 1 }))
        .toThrow('FIXTURE_CANCEL_FRAME_MISMATCH');
      expect(() => assertAttemptComplete(beforePlan, { ...before, contentFrames: 0 }))
        .toThrow('FIXTURE_CANCEL_FRAME_MISMATCH');
      expect(() => assertAttemptComplete(beforePlan, premature)).toThrow('FIXTURE_CANCEL_BOUNDARY_MISMATCH');
      assertAttemptComplete(beforePlan, before);
    });
  }
}
