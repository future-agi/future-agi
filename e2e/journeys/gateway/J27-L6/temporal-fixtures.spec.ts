import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { attachSeededIds, journeyTitle, selection } from '../_shared/contract';
import { scheduleCatalog } from '../_fixtures/temporal/catalog';
import { parseScheduleList, parseSchedule, scheduleIssues } from '../_fixtures/temporal/schedules';
import { pollerIssues, parsePollers } from '../_fixtures/temporal/pollers';
import { registrationIssues, requireBinding } from '../_fixtures/temporal/consumer';
import { syntheticBinding, syntheticReceipt } from '../_fixtures/temporal/control-data';
import { parseWorkflowStatus, workerTransitionIssues } from '../_fixtures/temporal/recovery';
import { inventoryObservation } from '../_fixtures/temporal/consumer';
import { temporalFixture } from '../_fixtures/temporal/fixtures';

// Fixture checks only, NOT full packet rows or live C01/C02/C03. Source pin and
// wire provenance are in the imported readers. No product/fixture is executed
// in this authoring stage. The captured CLI fixture is data, never executable.
const captured = JSON.parse(readFileSync(new URL('../../../fixtures/temporal/schedule-describe.json', import.meta.url), 'utf8'));
const details = { tag: [selection('J27-L6').tag, '@fixture-contract'], annotation: [{
  type: 'journey-fixture-contract', description: 'F08: data-only; no complete product row satisfied',
}] };
const name = (id: string) => journeyTitle('J27-L6', `F08.${id}`, 'baseline');

function description(id = 'agentcc-log-retention') {
  const raw = structuredClone(captured);
  raw.schedule.action.startWorkflow.workflowId = `scheduled-${id}`;
  return raw;
}

for (const mode of ['peerdb', 'outbox'] as const) {
  test(name(`complete-inventory.${mode}`), details, async ({}, info) => {
    await attachSeededIds(info, 'J27-L6', { runIds: [randomUUID()] });
    const rows = scheduleCatalog(mode);
    expect(new Set(rows.map(r => r.id)).size).toBe(rows.length);
    expect(rows.filter(r => r.id.startsWith('outbox-cdc-')).map(r => r.id).sort())
      .toEqual(mode === 'outbox' ? ['outbox-cdc-drain', 'outbox-cdc-reconcile'] : []);
    expect(rows.filter(r => r.id.startsWith('agentcc-managed-')).map(r => r.id).sort())
      .toEqual(['agentcc-managed-gateway-reconcile', 'agentcc-managed-push-sweep', 'agentcc-managed-settlement-drain']);
    // Exact whole source inventory, not the three gateway-alert schedules alone.
    expect(rows.length).toBe(mode === 'outbox' ? 54 : 52);
  });
}

test(name('capture-and-wide-counter'), details, async () => {
  const raw = description();
  raw.info.actionCount = '9007199254740993';
  const got = parseSchedule('agentcc-log-retention', JSON.stringify(raw));
  expect(got).toEqual({ id: 'agentcc-log-retention', taskQueue: 'default',
    workflowType: 'TaskRunnerWorkflow', workflowId: 'scheduled-agentcc-log-retention',
    activityName: 'purge_expired_agentcc_request_logs_activity', paused: false,
    actionCount: '9007199254740993' });
});

for (const defect of ['duplicate', 'missing', 'extra', 'queue', 'activity', 'workflow-id'] as const) {
  test(name(`inventory-rejects.${defect}`), details, async () => {
    const expected = scheduleCatalog('peerdb');
    const rows = expected.map(row => ({ ...row, workflowId: `scheduled-${row.id}`,
      paused: false, actionCount: '0' }));
    const ids = expected.map(row => row.id);
    expect(scheduleIssues(expected, ids, rows)).toEqual([]);
    if (defect === 'duplicate') { ids.push(ids[0]); rows.push({ ...rows[0] }); }
    if (defect === 'missing') { ids.pop(); rows.pop(); }
    if (defect === 'extra') { ids.push('unexpected'); rows.push({ ...rows[0], id: 'unexpected' }); }
    if (defect === 'queue') rows[0].taskQueue = 'wrong-queue';
    if (defect === 'activity') rows[0].activityName = 'wrong-activity';
    if (defect === 'workflow-id') rows[0].workflowId = 'wrong-workflow-id';
    expect(scheduleIssues(expected, ids, rows)).not.toEqual([]);
  });
}

test(name('list-preserves-multiplicity'), details, async () => {
  expect(parseScheduleList('[{"scheduleId":"same"},{"scheduleId":"same"}]')).toEqual(['same', 'same']);
  expect(() => parseScheduleList('{"schedules":[]}')).toThrow('Temporal list');
  expect(() => parseScheduleList('[{}]')).toThrow();
});

for (const defect of ['unsafe-counter', 'negative-counter', 'missing-action', 'bad-payload', 'bad-paused'] as const) {
  test(name(`capture-rejects.${defect}`), details, async () => {
    const raw = description();
    if (defect === 'unsafe-counter') raw.info.actionCount = 9007199254740992;
    if (defect === 'negative-counter') raw.info.actionCount = '-1';
    if (defect === 'missing-action') delete raw.schedule.action;
    if (defect === 'bad-payload') raw.schedule.action.startWorkflow.input.payloads[0].data = '%%%';
    if (defect === 'bad-paused') raw.schedule.state.paused = 'false';
    expect(() => parseSchedule('agentcc-log-retention', JSON.stringify(raw))).toThrow();
  });
}

for (const topology of ['standalone', 'distributed'] as const) {
  test(name(`registration.${topology}`), details, async ({}, info) => {
    const binding = syntheticBinding(randomUUID(), topology);
    await attachSeededIds(info, 'J27-L6', { runIds: [binding.runId] });
    const original = syntheticReceipt(binding);
    expect(registrationIssues(binding, original)).toEqual([]);
    for (const mutation of [
      { rawExitCode: 1 }, { cwd: '/tmp' }, { containerId: 'b'.repeat(64) }, { invocationId: 'other-invocation' },
      { argv: [...original.argv, '--model-hub-only'] }, { namespace: 'other-namespace' },
      { endedAtMs: original.startedAtMs - 1 }, { pid: 0 }, { imageId: 'sha256:' + 'c'.repeat(64) },
    ]) expect(registrationIssues(binding, { ...original, ...mutation })).not.toEqual([]);
    expect(registrationIssues(binding, original)).toEqual([]);
  });
}

test(name('fixture-no-default-or-reregistration'), details, async () => {
  expect(() => temporalFixture(undefined)).toThrow('F08-ABI OWED');
  const binding = syntheticBinding(randomUUID(), 'standalone');
  let calls = 0;
  const fixture = temporalFixture({ binding,
    async registerSchedules() { calls++; return syntheticReceipt(binding); },
    async readSchedules() { throw new Error('fixture read not requested'); },
  });
  await fixture.register();
  await expect(fixture.register()).rejects.toThrow('one-shot');
  expect(calls).toBe(1);
});

test(name('binding-refuses-unadmitted-and-remote'), details, async () => {
  const good = syntheticBinding(randomUUID(), 'distributed');
  expect(() => requireBinding(good)).not.toThrow();
  expect(() => requireBinding({ ...good, sourceRef: 'floating-head' })).toThrow();
  expect(() => requireBinding({ ...good, endpoint: 'temporal:7233' })).toThrow();
  expect(() => requireBinding({ ...good, runtimeAdmissionId: '' })).toThrow();
  expect(() => requireBinding({ ...good, exclusiveStackId: '' })).toThrow();
});

for (const defect of ['missing-type', 'duplicate', 'foreign', 'stale', 'future', 'pre-resume'] as const) {
  test(name(`pollers.${defect}`), details, async () => {
    const expected = [1, 2].map(taskQueueType => ({ taskQueue: 'default', taskQueueType: taskQueueType as 1 | 2,
      identity: '13@fixture-worker' }));
    const snapshot = { observedAtSeconds: 1000, rows: expected.map(row => ({ ...row, lastAccessSeconds: 999 })) };
    expect(pollerIssues(expected, snapshot, 990)).toEqual([]);
    if (defect === 'missing-type') snapshot.rows.pop();
    if (defect === 'duplicate') snapshot.rows.push({ ...snapshot.rows[0] });
    if (defect === 'foreign') snapshot.rows[0].identity = '14@foreign';
    if (defect === 'stale') snapshot.rows[0].lastAccessSeconds = 879;
    if (defect === 'future') snapshot.rows[0].lastAccessSeconds = 1006;
    if (defect === 'pre-resume') snapshot.rows[0].lastAccessSeconds = 989;
    expect(pollerIssues(expected, snapshot, 990)).not.toEqual([]);
  });
}

for (const cachedTypes of [[1], [2], [1, 2]] as const) {
  test(name(`pollers.same-resume-second.${cachedTypes.join('-')}`), details, async ({}, info) => {
    const runId = randomUUID();
    await attachSeededIds(info, 'J27-L6', { runIds: [runId] });
    const identity = `13@fixture-${runId}`;
    const expected = ([1, 2] as const).map(taskQueueType => ({ taskQueue: 'default', taskQueueType, identity }));
    const resumedAtSeconds = 1000;
    const snapshot = { observedAtSeconds: 1001, rows: expected.flatMap(row =>
      parsePollers(row.taskQueue, row.taskQueueType, { pollers: [{ identity,
        last_access_time: { seconds: 1001 } }] })) };
    expect(pollerIssues(expected, snapshot, resumedAtSeconds)).toEqual([]);
    // F08-SOL-01: a poll at 1000.1 before an in-place resume at 1000.9 is
    // projected as second 1000 for both. Exact identities and age=1 cannot
    // establish post-resume polling, for WORKFLOW, ACTIVITY or both together.
    for (const row of snapshot.rows) {
      if (cachedTypes.some(type => type === row.taskQueueType)) row.lastAccessSeconds = resumedAtSeconds;
    }
    expect(pollerIssues(expected, snapshot, resumedAtSeconds))
      .toEqual(cachedTypes.map(() => 'A03:poller-before-resume'));
    // Restored data-only observation: unchanged identities, but actual poll
    // seconds strictly later than resume. This is not a live recovery receipt.
    const restored = { observedAtSeconds: 1002,
      rows: snapshot.rows.map(row => ({ ...row, lastAccessSeconds: 1001 })) };
    expect(pollerIssues(expected, restored, resumedAtSeconds)).toEqual([]);
  });
}

test(name('recovery-keeps-worker-lineage'), details, async () => {
  const binding = syntheticBinding(randomUUID(), 'distributed');
  const paused = [{ ...binding.registrationTarget, pid: 13, processStartTicks: '100',
    pollerIdentity: '13@fixture-worker', namespace: binding.namespace, taskQueues: ['default'], state: 'quiesced' as const }];
  const recovered = [{ ...paused[0], pid: 14, processStartTicks: '200', pollerIdentity: '14@fixture-worker', state: 'running' as const }];
  const links = [{ serviceId: paused[0].serviceId, predecessorPid: 13, predecessorStartTicks: '100',
    successorPid: 14, successorStartTicks: '200' }];
  expect(workerTransitionIssues(paused, recovered, links)).toEqual([]);
  expect(workerTransitionIssues(paused, [], links)).toEqual(['A03:worker-service-set']);
  expect(workerTransitionIssues(paused, recovered, [])).toEqual(['A07:worker-lineage-set']);
  expect(workerTransitionIssues(paused, [{ ...recovered[0], state: 'quiesced' }], links)).toEqual(['A03:worker-not-running']);
  expect(workerTransitionIssues(paused, [{ ...recovered[0], taskQueues: ['other'] }], links)).toEqual(['A07:worker-topology-drift']);
});

test(name('workflow-status-is-not-history'), details, async () => {
  const workflowId = `scheduled-fixture-${randomUUID()}`;
  const runId = randomUUID();
  expect(parseWorkflowStatus(workflowId, runId, { workflow_id: workflowId, run_id: runId,
    status: '2', status_name: 'COMPLETED', start_time: '2026-10-30T00:00:00+00:00', close_time: '2026-10-30T00:00:01+00:00' }))
    .toEqual({ workflowId, runId, statusName: 'COMPLETED', startedAtMs: 1793318400000, closedAtMs: 1793318401000 });
  expect(() => parseWorkflowStatus(workflowId, runId, null)).toThrow();
  expect(() => parseWorkflowStatus(workflowId, runId, { workflow_id: workflowId, run_id: 'different-run' })).toThrow();
});

test(name('pagination-and-fresh-read-required'), details, async () => {
  const binding = syntheticBinding(randomUUID(), 'distributed');
  const readId = randomUUID();
  const value = { receipt: { ...binding.temporalTarget, runId: binding.runId, invocationId: binding.invocationId,
    namespace: binding.namespace, containerPolicyId: binding.containerPolicyId, readId, nativeReceiptId: 'synthetic-read',
    connectionId: 'synthetic-fresh-connection', startedAtMs: 1001001, endedAtMs: 1001002 },
    listJson: '[]', descriptions: [], pages: [{ readId: 'page-one', rowCount: 0, terminal: true }] };
  expect(inventoryObservation(binding, value, readId, 1001000).issues).toEqual(['A02:schedule-id-set', 'A02:description-id-set']);
  expect(() => inventoryObservation(binding, { ...value, pages: [] }, readId, 1001000)).toThrow();
  expect(() => inventoryObservation(binding, { ...value, pages: [{ ...value.pages[0], terminal: false }] }, readId, 1001000)).toThrow();
  expect(() => inventoryObservation(binding, value, readId, 1001003)).toThrow('stale');
  expect(() => inventoryObservation(binding, value, randomUUID(), 1001000)).toThrow('identity');
});

test(name('poller-projection-fails-closed'), details, async () => {
  expect(parsePollers('default', 1, { pollers: [{ identity: '13@fixture-worker', last_access_time: { seconds: 999 } }] }))
    .toEqual([{ taskQueue: 'default', taskQueueType: 1, identity: '13@fixture-worker', lastAccessSeconds: 999 }]);
  expect(() => parsePollers('default', 1, {})).toThrow();
  expect(() => parsePollers('default', 1, { pollers: [{ identity: '13@fixture-worker' }] })).toThrow();
});
