import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import YAML from 'yaml';
import os from 'node:os';
import path from 'node:path';
import { boundedRecords, collectDiagnostics, collectSnapshot, dockerEvent, redisInfo, startDiagnostics, stopDiagnostics, safeLog, stackServices } from './redis-diagnostics.mjs';

const timestamp = '2026-11-10T08:30:34.719Z';

test('retains early cache attribution, drops secrets and unrelated log text', () => {
  const line = `${timestamp} ${JSON.stringify({ event: 'api_cache_unavailable', error_class: 'ConnectionInterrupted',
    cause_class: 'TimeoutError', source_module: 'accounts.authentication', source_function: 'decode_token',
    source_line: 1036, password: 'secret', token: 'secret', message: 'redis://secret', request_id: 'raw-secret-header' })}`;
  const record = safeLog(line, 'backend');
  assert.equal(record.timestamp, timestamp);
  assert.equal(record.source_function, 'decode_token');
  assert.equal(record.cause_class, 'TimeoutError');
  assert.ok(!JSON.stringify(record).includes('secret'));
  assert.equal(safeLog(`${timestamp} Authorization: Bearer secret`, 'backend'), null);
  assert.equal(safeLog(`${timestamp} ${JSON.stringify({ event: 'unrelated', token: 'secret' })}`, 'backend'), null);
  const logs = [line, ...Array(250).fill(`${timestamp} unrelated log`)];
  assert.deepEqual(logs.slice(-200).map(line => safeLog(line, 'backend')).filter(Boolean), []);
  assert.deepEqual(logs.map(line => safeLog(line, 'backend')).filter(Boolean), [record]);
});

test('E2E local ConsoleRenderer retains typed cache attribution without raw fields', () => {
  const line = `${timestamp} 2026-11-10T08:30:34.719Z [\u001b[33mwarning  \u001b[0m] \u001b[1mapi_cache_unavailable\u001b[0m [accounts.authentication] cause_class=TimeoutError error_class=ConnectionInterrupted source_line=1036 source_function=decode_token token=secret-token message='redis://secret-password'`;
  assert.deepEqual(safeLog(line, 'backend'), { timestamp, service: 'backend', event: 'api_cache_unavailable',
    error_class: 'ConnectionInterrupted', cause_class: 'TimeoutError', source_function: 'decode_token', source_line: 1036 });
});

test('console records ending in a digit are not mistaken for JSON scalars', () => {
  assert.deepEqual(safeLog(`${timestamp} [warning  ] api_cache_unavailable source_line=123`, 'backend'),
    { timestamp, service: 'backend', event: 'api_cache_unavailable', source_line: 123 });
});

test('Redis evidence is an allowlist of numeric counters and lifecycle events', () => {
  assert.deepEqual(redisInfo('used_memory:123\r\nrejected_connections:2\r\nmaster_host:secret\r\nerrorstat_OOM:count=4\r\naof_delayed_fsync:5'),
    { used_memory: 123, rejected_connections: 2, errorstat_OOM: 4, aof_delayed_fsync: 5 });
  assert.deepEqual(safeLog(`${timestamp} Background saving terminated with success`, 'redis'),
    { timestamp, service: 'redis', event: 'save_complete' });
  assert.equal(safeLog(`${timestamp} secret-command secret-value`, 'redis'), null);
});

test('OOM and restart events retain only known-container event fields', () => {
  const ids = new Map([['abc', 'redis']]);
  const line = JSON.stringify({ Action: 'oom', Actor: { ID: 'abc', Attributes: { password: 'secret' } }, time: 42, timeNano: 43 });
  assert.deepEqual(dockerEvent(line, ids), { service: 'redis', event: 'oom', time: 42, timeNano: 43 });
  assert.equal(dockerEvent(line, new Map()), null);
});

test('bounded evidence retains first failure and counts discarded tail', () => {
  const records = boundedRecords(100);
  records.add({ event: 'early-failure' });
  for (let i = 0; i < 100; i++) records.add({ later: i });
  assert.equal(records.result().records[0].event, 'early-failure');
  assert.ok(records.result().bytes <= 100);
  assert.ok(records.result().dropped > 0);
});

test('missing services and runner pressure stay diagnostic, not suite failures', async () => {
  const result = await collectSnapshot(async () => { throw new Error('secret transport'); }, async () => { throw new Error('secret'); },
    stackServices('distributed'));
  assert.equal(result.redis.stats_unavailable, true);
  assert.deepEqual(result.containers.map(row => row.unavailable), [true, true]);
  assert.ok(!JSON.stringify(result).includes('secret'));
});

test('each stack targets the services bin/e2e actually starts', async () => {
  assert.deepEqual(stackServices('distributed').containers, ['backend', 'redis']);
  assert.deepEqual(stackServices('').containers, ['backend', 'redis']);
  assert.deepEqual(stackServices('distributed').redisInfo('stats'), ['redis', 'redis-cli', 'INFO', 'stats']);
  // Standalone runs Redis inside `app`, authenticated from the container's own env.
  assert.deepEqual(stackServices('standalone').containers, ['app']);
  const args = stackServices('standalone').redisInfo('stats');
  assert.deepEqual([args[0], args.at(-1)], ['app', 'stats']);
  assert.match(args[3], /REDISCLI_AUTH="\$REDIS_PASSWORD"/);
  const calls = [];
  await collectSnapshot(async (file, argv) => { calls.push(argv.join(' ')); throw new Error('unavailable'); },
    async () => { throw new Error('unavailable'); }, stackServices('standalone'));
  assert.ok(calls.some(call => call.startsWith('compose exec -T app sh -c')));
  assert.ok(calls.includes('compose ps -q app'));
  assert.ok(!calls.some(call => / (backend|redis)$/.test(call) || call.includes('exec -T redis')));
});

test('standalone app logs yield both Redis and backend events', () => {
  assert.deepEqual(safeLog(`${timestamp} Background saving terminated with success`, 'app'),
    { timestamp, service: 'app', event: 'save_complete' });
  assert.deepEqual(safeLog(`${timestamp} [warning  ] api_cache_unavailable source_line=123`, 'app'),
    { timestamp, service: 'app', event: 'api_cache_unavailable', source_line: 123 });
  assert.equal(safeLog(`${timestamp} secret-command secret-value`, 'app'), null);
});

async function directory(t) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'redis-diagnostics-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  return dir;
}

function fakeDetached(file, args, options) {
  assert.equal(file, process.execPath);
  assert.equal(args[1], 'collect');
  assert.equal(options.detached, true);
  assert.equal(options.stdio, 'ignore');
  const child = new EventEmitter();
  child.pid = process.pid;
  child.unref = () => { child.unreferenced = true; };
  queueMicrotask(() => child.emit('spawn'));
  return child;
}

test('start persists PID/token, detaches and returns without waiting for a sample or suite', async t => {
  const dir = await directory(t);
  let child;
  const state = await startDiagnostics(dir, { spawn: (...args) => (child = fakeDetached(...args)) });
  assert.equal(child.unreferenced, true);
  assert.equal(state.pid, process.pid);
  assert.match(state.token, /^[0-9a-f-]{36}$/);
  assert.deepEqual(JSON.parse(await readFile(path.join(dir, 'state.json'), 'utf8')), state);
  await assert.rejects(readFile(path.join(dir, 'collector.json')), { code: 'ENOENT' });
  await assert.rejects(startDiagnostics(dir, { spawn: () => assert.fail('duplicate collector') }), { code: 'EEXIST' });
});

for (const suiteOutcome of ['success', 'failure', 'cancelled', 'skipped']) {
  test(`stop flushes bounded evidence and cleans followers, preserving ${suiteOutcome}`, async t => {
    const dir = await directory(t);
    const state = await startDiagnostics(dir, { spawn: fakeDetached });
    const killed = [], followed = [];
    let sampled;
    const ready = new Promise(resolve => { sampled = resolve; });
    const worker = collectDiagnostics(dir, state.token, {
      command: async () => 'a'.repeat(64), services: stackServices('distributed'), pollMs: 1,
      spawn: (file, args) => {
        followed.push([file, args]);
        const child = new EventEmitter();
        child.stdout = new PassThrough(); child.stderr = new PassThrough();
        child.kill = signal => { killed.push(signal); return true; };
        return child;
      },
      snapshot: async () => { sampled(); return { timestamp, redis: { rejected_connections: 2 } }; },
    });
    await ready;
    const result = await stopDiagnostics(dir, suiteOutcome, { pollMs: 1 });
    await worker;
    assert.equal(result.suiteOutcome, suiteOutcome);
    assert.equal(result.collectorStatus, 'stopped');
    assert.equal(result.records.length, 2);
    assert.deepEqual(killed, ['SIGKILL', 'SIGKILL', 'SIGKILL']);
    assert.ok(followed.every(([file]) => file === 'docker')); // Never wraps the suite.
    assert.deepEqual(JSON.parse(await readFile(path.join(dir, 'redis-diagnostics.json'), 'utf8')), result);
    assert.deepEqual(await stopDiagnostics(dir, 'unknown'), result); // Already stopped.
  });
}

test('stale PID recovers the matching checkpoint without signaling any process', async t => {
  const dir = await directory(t);
  await writeFile(path.join(dir, 'state.json'), JSON.stringify({ token: 'owned', pid: 99999999, since: timestamp }));
  await writeFile(path.join(dir, 'collector.json'), JSON.stringify({ token: 'owned', completed: false,
    records: [{ timestamp, redis: { rejected_connections: 3 } }], dropped: 7 }));
  const result = await stopDiagnostics(dir, 'failure', { isAlive: pid => { assert.equal(pid, 99999999); return false; } });
  assert.equal(result.collectorStatus, 'collector_stale');
  assert.equal(result.suiteOutcome, 'failure');
  assert.equal(result.records[0].redis.rejected_connections, 3);
  assert.equal(result.dropped, 7);
});

test('a recycled live PID has a bounded wait and mismatched checkpoints are ignored', async t => {
  const dir = await directory(t);
  await writeFile(path.join(dir, 'state.json'), JSON.stringify({ token: 'owned', pid: process.pid, since: timestamp }));
  await writeFile(path.join(dir, 'collector.json'), JSON.stringify({ token: 'old', completed: true, records: [{ wrong: true }] }));
  const result = await stopDiagnostics(dir, 'failure', { stopTimeoutMs: 0 });
  assert.equal(result.collectorStatus, 'collector_stop_timeout');
  assert.deepEqual(result.records, [{ event: 'collector_stop_timeout' }]);
  assert.equal(result.suiteOutcome, 'failure');
});

test('missing start state and launch errors remain diagnostic and redact exceptions', async t => {
  const dir = await directory(t);
  const result = await stopDiagnostics(dir, 'success');
  assert.equal(result.collectorStatus, 'collector_missing');
  assert.equal(result.suiteOutcome, 'success');
  const failed = await directory(t);
  await assert.rejects(startDiagnostics(failed, { spawn: () => {
    const child = new EventEmitter();
    queueMicrotask(() => child.emit('error', new Error('private transport')));
    return child;
  } }), /private transport/);
  const stopped = await stopDiagnostics(failed, 'failure');
  assert.equal(stopped.collectorStatus, 'collector_stale');
  assert.ok(!JSON.stringify(stopped).includes('private'));
});

test('collector sample errors still stop and flush without exposing exception text', async t => {
  const dir = await directory(t);
  const state = await startDiagnostics(dir, { spawn: fakeDetached });
  await writeFile(path.join(dir, 'stop.json'), JSON.stringify({ token: state.token }));
  const killed = [];
  await collectDiagnostics(dir, state.token, {
    command: async () => { throw new Error('private'); }, services: stackServices('standalone'),
    spawn: () => { const child = new EventEmitter(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
      child.kill = signal => killed.push(signal); return child; },
    snapshot: async () => { throw new Error('private'); },
  });
  const result = await stopDiagnostics(dir, 'failure');
  assert.equal(result.collectorStatus, 'stopped');
  assert.equal(result.records.filter(row => row.event === 'snapshot_unavailable').length, 2);
  assert.deepEqual(killed, ['SIGKILL']);
  assert.ok(!JSON.stringify(result).includes('private'));
});

test('CLI start exits while detached collector lives; CLI stop finalizes its snapshots', async t => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'redis-diagnostics-cli-'));
  const script = fileURLToPath(new URL('./redis-diagnostics.mjs', import.meta.url));
  // No Docker or stack in this test. An empty PATH/cwd makes external probes fail
  // deterministically while exercising the real cross-process start/stop protocol.
  const options = { cwd: dir, env: { ...process.env, PATH: dir }, timeout: 10_000 };
  const cli = (...args) => promisify(execFile)(process.execPath, [script, ...args], options);
  t.after(async () => {
    try { await cli('stop', '--output-dir', dir, '--suite-outcome', 'unknown'); }
    finally { await rm(dir, { recursive: true, force: true }); }
  });
  await cli('start', '--output-dir', dir);
  const state = JSON.parse(await readFile(path.join(dir, 'state.json'), 'utf8'));
  assert.notEqual(state.pid, process.pid);
  process.kill(state.pid, 0);
  const deadline = Date.now() + 5000;
  while (true) {
    try { if (JSON.parse(await readFile(path.join(dir, 'collector.json'), 'utf8')).records.length) break; } catch { /* first checkpoint pending */ }
    assert.ok(Date.now() < deadline, 'detached collector writes an initial snapshot');
    await delay(20);
  }
  await cli('stop', '--output-dir', dir, '--suite-outcome', 'failure');
  const result = JSON.parse(await readFile(path.join(dir, 'redis-diagnostics.json'), 'utf8'));
  assert.equal(result.suiteOutcome, 'failure');
  assert.equal(result.collectorStatus, 'stopped');
  assert.ok(result.records.filter(row => row.redis).length >= 2);
  assert.ok(result.bytes <= 8 * 1024 * 1024);
  const before = await readFile(path.join(dir, 'redis-diagnostics.json'), 'utf8');
  await cli('stop', '--output-dir', dir, '--suite-outcome', 'success');
  assert.equal(await readFile(path.join(dir, 'redis-diagnostics.json'), 'utf8'), before);
});

test('workflow leaves suite and host proof adjacent; optional diagnostics cannot change their outcome', async () => {
  const workflow = YAML.parse(await readFile(new URL('../../.github/workflows/e2e-ci.yml', import.meta.url), 'utf8'));
  const steps = workflow.jobs.e2e.steps;
  const suite = steps.findIndex(step => step.name === 'Run E2E suite');
  assert.equal(steps[suite].run, 'bin/e2e test');
  assert.equal(steps[suite].id, 'suite');
  assert.equal(steps[suite]['continue-on-error'], undefined);
  assert.equal(steps[suite - 1].name, 'Start Redis diagnostics');
  assert.equal(steps[suite - 2].id, 'boot');
  assert.equal(steps[suite + 1].name, 'Prove host update assertion fallibility');
  assert.equal(steps[suite + 2].name, 'Upload host update fallibility evidence');
  assert.equal(steps[suite + 3].name, 'Stop Redis diagnostics');
  assert.equal(steps[suite + 4].name, 'Upload Redis diagnostics');
  for (const index of [suite - 1, suite + 3, suite + 4]) assert.equal(steps[index]['continue-on-error'], true);
  for (const index of [suite + 3, suite + 4]) assert.equal(steps[index].if, "${{ !cancelled() && steps.boot.outcome == 'success' }}");
  assert.equal(steps[suite + 3].env.SUITE_OUTCOME, '${{ steps.suite.outcome }}');
  assert.match(steps[suite - 1].run, / start --output-dir /);
  assert.match(steps[suite + 3].run, / stop --output-dir .* --suite-outcome "\$SUITE_OUTCOME"$/);
  assert.match(steps[suite + 4].with.path, /\/redis-diagnostics\/redis-diagnostics\.json$/);
});
