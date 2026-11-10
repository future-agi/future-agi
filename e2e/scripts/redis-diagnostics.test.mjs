import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { boundedRecords, collectSnapshot, dockerEvent, redisInfo, runWithDiagnostics, safeLog } from './redis-diagnostics.mjs';

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
  const result = await collectSnapshot(async () => { throw new Error('secret transport'); }, async () => { throw new Error('secret'); });
  assert.equal(result.redis.stats_unavailable, true);
  assert.deepEqual(result.containers.map(row => row.unavailable), [true, true]);
  assert.ok(!JSON.stringify(result).includes('secret'));
});

for (const [status, signal, expected] of [[7, null, 7], [null, 'SIGKILL', 137]])
  test(`collector starts before suite, cleans up and preserves exit ${expected}`, async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'redis-diagnostics-'));
  const sequence = [], streams = [];
  const spawn = (file, args, options) => {
    const child = new EventEmitter();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.kill = signal => { sequence.push(signal); return true; };
    if (options.stdio === 'inherit') {
      sequence.push('suite');
      queueMicrotask(() => child.emit('close', status, signal));
    } else { sequence.push('follow'); streams.push(child); }
    return child;
  };
  try {
    const code = await runWithDiagnostics(['fake-suite'], dir, {
      spawn, command: async () => 'a'.repeat(64),
      snapshot: async () => { sequence.push('snapshot'); return { timestamp, rejected_connections: 2 }; },
    });
    assert.equal(code, expected);
    assert.ok(sequence.indexOf('follow') < sequence.indexOf('suite'));
    assert.ok(sequence.indexOf('snapshot') < sequence.indexOf('suite'));
    assert.equal(sequence.filter(value => value === 'SIGKILL').length, streams.length);
    const result = JSON.parse(await readFile(path.join(dir, 'redis-diagnostics.json'), 'utf8'));
    assert.equal(result.suiteExitCode, expected);
    assert.equal(result.records.length, 2);
  } finally { await rm(dir, { recursive: true, force: true }); }
});
