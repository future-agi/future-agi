import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { attachSeededIds } from '../_shared/contract';
import { fragmentAnnotation } from './fragments';
import { assertGuardrailLogPair, assertNoGuardrailMetadata,
  type GuardrailRequestFacts } from './guardrail-state';

// These data-only tests exercise safe PG observation detectors, not a provider,
// a stream, the actual 7C UI, a null-enabled product check, or live controls.
// W18.R1 and W18.A02/A03 remain OWED until those independent paths are bound.
// No real/synthetic credential, request content or header belongs in these data.
function requestFacts(requestId: string, blocked = false): GuardrailRequestFacts {
  return { id: randomUUID(), request_id: requestId, status_code: blocked ? 403 : 200,
    is_stream: true, guardrail_triggered: blocked, action_present: blocked,
    blocked_action: blocked, guardrail_metadata_count: blocked ? 1 : 0, deleted: false };
}

const defects = [
  { id: 'wrong-status', failure: 'W18.A02 BLOCKED_STREAM_LOG' },
  { id: 'wrong-action', failure: 'W18.A02 BLOCKED_STREAM_LOG' },
  { id: 'missing-trigger', failure: 'W18.A02 BLOCKED_STREAM_LOG' },
  { id: 'missing-request', failure: 'W18.A02 EXACT_REQUEST_COHORT' },
  { id: 'duplicate-request', failure: 'W18.A02 EXACT_REQUEST_COHORT' },
] as const;

for (const defect of defects) {
  test(`J15-DETECTOR W18.R1 ${defect.id}: guardrail log projection`, {
    tag: ['@journey-J15-oracle'],
    annotation: [fragmentAnnotation('W18.A02', `W18.R1/${defect.id}`, true), {
      type: 'journey-oracle-control', description: JSON.stringify({ control: 'W18.R1',
        row: 'W18.A02', defect: defect.id, liveControl: false, walkedStatus: 'NOT_WALKED' }),
    }],
  }, async ({}, info) => {
    const passing = randomUUID();
    const blocked = randomUUID();
    const good = [requestFacts(passing), requestFacts(blocked, true)];
    const bad = structuredClone(good);
    switch (defect.id) {
      case 'wrong-status': bad[1].status_code = 200; break;
      case 'wrong-action': bad[1].blocked_action = false; break;
      case 'missing-trigger': bad[1].guardrail_triggered = false; break;
      case 'missing-request': bad.pop(); break;
      case 'duplicate-request': bad.push({ ...bad[1], id: randomUUID() }); break;
    }
    await attachSeededIds(info, 'J15', { requestIds: [passing, blocked] });
    await test.step('oracle baseline: exact passing and blocked logs', async () => {
      assertGuardrailLogPair(good, passing, blocked);
    });
    await test.step(`oracle negative: ${defect.id} kills its intended assertion`, async () => {
      expect(() => assertGuardrailLogPair(bad, passing, blocked)).toThrow(defect.failure);
    });
    await test.step('oracle restored: original pair, identical detector', async () => {
      assertGuardrailLogPair(good, passing, blocked);
    });
  });
}

test('J15-DETECTOR W18.R1 stored-only-executed: no guardrail metadata', {
  tag: ['@journey-J15-oracle'],
  annotation: [fragmentAnnotation('W18.A03', 'W18.R1/stored-only-executed', true), {
    type: 'journey-oracle-control', description: JSON.stringify({ control: 'W18.R1',
      row: 'W18.A03', defect: 'stored-only-executed', liveControl: false, walkedStatus: 'NOT_WALKED' }),
  }],
}, async ({}, info) => {
  const requestId = randomUUID();
  const good = [requestFacts(requestId)];
  const bad = [{ ...good[0], guardrail_metadata_count: 1 }];
  await attachSeededIds(info, 'J15', { requestIds: [requestId] });
  await test.step('oracle baseline: no guardrail metadata on stored-only call', async () => {
    assertNoGuardrailMetadata(good, [requestId]);
  });
  await test.step('oracle negative: a stored-only execution witness fails', async () => {
    expect(() => assertNoGuardrailMetadata(bad, [requestId])).toThrow('W18.A03 NO_GUARDRAIL_METADATA');
  });
  await test.step('oracle restored: no metadata again, identical detector', async () => {
    assertNoGuardrailMetadata(good, [requestId]);
  });
});
