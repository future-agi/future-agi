import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readdir, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {recordKafkaBatch, processClaim, publishSavedReport, runCoordinator} from './coordinator.mjs';

function claim() {
  return {organization_id: randomUUID(), workspace_id: null, project_id: randomUUID(), trace_id: randomUUID(),
    job_id: randomUUID(), attempt_id: randomUUID(), generation: 1, feature_enabled: true, lease_token: 'fixture',
    contract_version: 'omega-investigation/v1', read_cutoff: new Date().toISOString(),
    limits: {deadline_seconds: 60, max_model_calls: 12, max_input_tokens_total: 100000,
      max_output_tokens_total: 8000, max_evidence_bytes: 65536, max_tool_result_bytes: 4096}};
}
test('Kafka acknowledgments follow durable receipt; failed persistence never resolves the offset', async () => {
  const events = [];
  const batch = {batch: {topic: 'roots', partition: 0, messages: [{value: Buffer.from('{}'), offset: '9007199254740993'}]},
    resolveOffset: id => events.push(id), heartbeat: async () => events.push('heartbeat'), isRunning: () => true, isStale: () => false};
  await recordKafkaBatch(batch, async (_path, payload) => {
    assert.equal(payload.deliveries[0].offset, '9007199254740993');
    events.push('persisted'); return {accepted_events: 1, duplicate_events: 0};
  });
  assert.deepEqual(events, ['persisted', '9007199254740993', 'heartbeat']);
  events.length = 0;
  await assert.rejects(recordKafkaBatch(batch, async () => { throw new Error('DB down'); }));
  assert.deepEqual(events, []);
});
test('publication recovery uses the saved report, never a second model run', async () => {
  const spool = await mkdtemp(join(tmpdir(), 'omega-spool-test-'));
  try {
    const c = claim(); let runs = 0;
    const result = {attempt_id: c.attempt_id, usage: {cost_usd: 0.12}};
    await assert.rejects(processClaim(c, {spool, investigate: async () => { runs++; return result; },
      control: async () => { throw new Error('Django unavailable'); }}));
    assert.equal(runs, 1);
    assert.deepEqual(await readdir(spool), [c.attempt_id + '.json']);
    await publishSavedReport(join(spool, c.attempt_id + '.json'), async (_path, body) => {
      assert.deepEqual(body.result, result); assert.equal(body.idempotency_key, c.attempt_id);
      return {status: 'duplicate'};
    });
    assert.equal(runs, 1); assert.deepEqual(await readdir(spool), []);
  } finally { await rm(spool, {recursive: true, force: true}); }
});
test('revoked lease cancels model work and preserves failed report for accounting', async () => {
  const spool = await mkdtemp(join(tmpdir(), 'omega-spool-test-'));
  try {
    const c = claim();
    await assert.rejects(processClaim(c, {spool, heartbeatMs: 1,
      control: async () => ({status: 'cancelled', cancellation_requested: true}),
      investigate: async (_claim, {signal}) => {
        await new Promise(resolve => signal.addEventListener('abort', resolve, {once: true}));
        return {execution_status: 'failed', outcome: 'unknown'};
      }}), /lease revoked/);
    assert.equal((await readdir(spool)).length, 1);
  } finally { await rm(spool, {recursive: true, force: true}); }
});
test('coordinator never claims beyond available slots and drains on shutdown', async () => {
  const spool = await mkdtemp(join(tmpdir(), 'omega-spool-test-'));
  const stop = new AbortController(); let running = 0, peak = 0, claimed = false;
  try {
    await runCoordinator({spool, signal: stop.signal, workerId: 'test', engineVersion: 'test', concurrency: 2, pollMs: 1,
      control: async (path, payload) => {
        if (path === '/reports/') return {status: 'accepted'};
        assert.equal(payload.limit, 2);
        assert.equal(claimed, false); claimed = true;
        return {claims: [claim(), claim()]};
      },
      investigate: async (_claim, {signal}) => {
        running++; peak = Math.max(peak, running);
        if (running === 2) stop.abort();
        if (!signal.aborted) await new Promise(resolve => signal.addEventListener('abort', resolve, {once: true}));
        running--; return {execution_status: 'failed'};
      }});
    assert.equal(peak, 2); assert.equal(running, 0);
  } finally { await rm(spool, {recursive: true, force: true}); }
});

test('terminally rejected saved report is quarantined without blocking later reports or claims', async () => {
  const spool = await mkdtemp(join(tmpdir(), 'omega-spool-test-'));
  const stop = new AbortController();
  const poisoned = '00000000-0000-4000-8000-000000000001';
  const healthy = 'ffffffff-ffff-4fff-8fff-ffffffffffff';
  const published = [];
  let claims = 0;
  try {
    for (const attemptId of [poisoned, healthy]) {
      await writeFile(join(spool, attemptId + '.json'), JSON.stringify({idempotency_key: attemptId}));
    }
    await runCoordinator({spool, signal: stop.signal, workerId: 'test', engineVersion: 'test', pollMs: 1,
      control: async (path, payload) => {
        if (path === '/reports/') {
          published.push(payload.idempotency_key);
          if (payload.idempotency_key === poisoned) {
            const error = new Error('Control request failed');
            error.status = 400;
            throw error;
          }
          return {status: 'accepted'};
        }
        claims++;
        stop.abort();
        return {claims: []};
      },
      investigate: async () => { throw new Error('Unexpected investigation'); }});
    assert.deepEqual(published, [poisoned, healthy]);
    assert.equal(claims, 1);
    assert.deepEqual(await readdir(join(spool, 'rejected')), [poisoned + '.json']);
    assert.deepEqual((await readdir(spool)).filter(name => name.endsWith('.json')), []);
  } finally { await rm(spool, {recursive: true, force: true}); }
});

test('corrupt saved report is quarantined without blocking claims', async () => {
  const spool = await mkdtemp(join(tmpdir(), 'omega-spool-test-'));
  const stop = new AbortController();
  const attemptId = '00000000-0000-4000-8000-000000000002';
  try {
    await writeFile(join(spool, attemptId + '.json'), '{broken');
    await runCoordinator({spool, signal: stop.signal, workerId: 'test', engineVersion: 'test', pollMs: 1,
      control: async path => {
        assert.equal(path, '/claims/');
        stop.abort();
        return {claims: []};
      },
      investigate: async () => { throw new Error('Unexpected investigation'); }});
    assert.deepEqual(await readdir(join(spool, 'rejected')), [attemptId + '.json']);
  } finally { await rm(spool, {recursive: true, force: true}); }
});
