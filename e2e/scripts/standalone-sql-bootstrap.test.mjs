import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import YAML from 'yaml';

const root = fileURLToPath(new URL('../../', import.meta.url));
const bootstrapPath = path.join(root, 'e2e/stack/backend/bootstrap_sql.py');
const overlay = async name => YAML.parse(await readFile(path.join(root, 'e2e/stack', name), 'utf8'), {
  customTags: ['!reset', '!override'].map(tag => ({ tag, collection: 'seq', resolve: value => value })),
});

test('standalone API and embedded worker use the distributed read-only SQL fixture credentials', async () => {
  const standalone = (await overlay('docker-compose.standalone-e2e.yml')).services.app;
  const distributed = (await overlay('docker-compose.e2e.yml')).services;
  for (const key of ['SQL_WORKBENCH_CH_USER', 'SQL_WORKBENCH_CH_PASSWORD']) {
    const expected = distributed['sql-workbench-bootstrap'].environment[key];
    assert.ok(expected);
    assert.equal(standalone.environment[key], expected);
    assert.equal(distributed.backend.environment[key], expected);
    assert.equal(distributed['worker-sql-workbench'].environment[key], expected);
  }
  assert.deepEqual(standalone.env_file, []);
  assert.equal(standalone.environment.FUTURE_AGI_E2E_FIXTURE, undefined);
  assert.ok(!standalone.volumes.some(volume => volume.includes('bootstrap_sql.py')));
});

// Exercise the real CLI with fake external commands, never a Docker daemon or
// network. Successful probes are immediate; failures must stop setup, not retry.
async function runUp(t, { stack = 'standalone', failure = '' } = {}) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'standalone-sql-bootstrap-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const log = path.join(dir, 'calls.jsonl');
  await writeFile(log, '');
  const prelude = `#!${process.execPath}
const fs = require('node:fs');
const args = process.argv.slice(2);
const record = value => fs.appendFileSync(process.env.CALL_LOG, JSON.stringify(value) + '\\n');
`;
  await writeFile(path.join(dir, 'docker'), `${prelude}
if (args.includes('exec')) {
  record({ kind: 'sql', args, stdin: fs.readFileSync(0, 'utf8') });
  if (process.env.FAIL_AT === 'sql') process.exit(42);
  console.log('fixture bootstrap completed');
} else if (args.includes('up')) {
  record({ kind: 'up', args });
  if (process.env.FAIL_AT === 'up') process.exit(43);
} else {
  record({ kind: 'unexpected-docker', args });
  process.exit(99);
}
`, { mode: 0o755 });
  await writeFile(path.join(dir, 'curl'), `${prelude}
let kind;
if (args.some(arg => arg.includes('/health/'))) kind = 'backend';
else if (args.some(arg => arg.includes('FROM spans'))) kind = 'spans';
else if (args.some(arg => arg.includes('observed_attribute_keys'))) kind = 'indexes';
else if (args.includes('http://localhost:3100/')) kind = 'frontend';
else { record({ kind: 'unexpected-curl', args }); process.exit(99); }
record({ kind, args });
if (process.env.FAIL_AT === kind) process.exit(44);
`, { mode: 0o755 });
  const result = spawnSync('/bin/bash', [path.join(root, 'bin/e2e'), 'up'], {
    cwd: root,
    env: { ...process.env, PATH: `${dir}:${process.env.PATH}`, E2E_STACK: stack,
      E2E_HOST_UPDATE_STATUS_DIR: path.join(dir, 'host-update'), CALL_LOG: log, FAIL_AT: failure },
    encoding: 'utf8', timeout: 10_000,
  });
  assert.equal(result.error, undefined);
  const calls = (await readFile(log, 'utf8')).trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
  return { ...result, calls };
}

test('standalone up streams the guarded bootstrap once, after health/schema and before ready', async t => {
  const result = await runUp(t);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.calls.map(call => call.kind), ['up', 'backend', 'spans', 'indexes', 'sql', 'frontend']);
  const up = result.calls[0];
  assert.deepEqual(up.args.slice(up.args.indexOf('up')), ['up', '-d', '--wait', '--wait-timeout', '1200',
    'app', 'postgres', 'clickhouse', 'mock-llm', 'mock-google', 'code-executor', 'link-canary', 'link-worker-canary']);
  const sql = result.calls.find(call => call.kind === 'sql');
  assert.deepEqual(sql.args.slice(0, sql.args.indexOf('exec')), up.args.slice(0, up.args.indexOf('up')));
  assert.deepEqual(sql.args.slice(sql.args.indexOf('exec')), ['exec', '-T', '-w', '/app/backend',
    '-e', 'FUTURE_AGI_E2E_FIXTURE=sql-workbench', 'app', 'python', '-']);
  assert.equal(sql.stdin, await readFile(bootstrapPath, 'utf8'));
  assert.match(result.stdout, /fixture bootstrap completed[\s\S]*E2E stack ready:/);
});

for (const [failure, status, expectedCalls] of [
  ['up', 43, ['up']],
  ['spans', 1, ['up', 'backend', 'spans']],
  ['indexes', 1, ['up', 'backend', 'spans', 'indexes']],
  ['sql', 42, ['up', 'backend', 'spans', 'indexes', 'sql']],
]) {
  test(`standalone up fails closed at ${failure} without claiming readiness or retrying`, async t => {
    const result = await runUp(t, { failure });
    assert.equal(result.status, status, result.stderr);
    assert.deepEqual(result.calls.map(call => call.kind), expectedCalls);
    assert.doesNotMatch(result.stdout, /E2E stack ready:/);
  });
}

test('distributed up leaves SQL provisioning to its existing Compose dependency', async t => {
  const result = await runUp(t, { stack: 'distributed' });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.calls.map(call => call.kind), ['up', 'backend', 'spans', 'indexes', 'frontend']);
  assert.match(result.stdout, /E2E stack ready:/);
});
