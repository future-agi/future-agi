#!/usr/bin/env node
// Optional, bounded CI evidence. Never stores keys, command arguments, env,
// exception messages, raw log lines or Redis connection configuration.
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';

const exec = promisify(execFile);
const MAX_BYTES = 8 * 1024 * 1024;
const EVENTS = new Set(['api_cache_unavailable', 'access_token_cache_unavailable',
  'access_token_cache_fallback_result', 'api_database_unavailable']);
const INFO = new Set(['uptime_in_seconds', 'connected_clients', 'blocked_clients',
  'total_connections_received', 'rejected_connections', 'total_error_replies',
  'instantaneous_ops_per_sec', 'evicted_keys', 'expired_keys', 'used_memory',
  'used_memory_rss', 'used_memory_peak', 'maxmemory', 'mem_fragmentation_ratio',
  'latest_fork_usec', 'rdb_bgsave_in_progress', 'rdb_last_bgsave_time_sec',
  'rdb_changes_since_last_save', 'aof_enabled', 'aof_delayed_fsync',
  'aof_pending_bio_fsync', 'aof_rewrite_in_progress']);
const numeric = value => /^-?\d+(?:\.\d+)?$/.test(String(value)) ? Number(value) : undefined;

export function redisInfo(text) {
  return Object.fromEntries(text.split(/\r?\n/).flatMap(line => {
    const [key, value] = line.split(':');
    if (INFO.has(key) && numeric(value) !== undefined) return [[key, numeric(value)]];
    if (/^errorstat_(ERR|OOM|BUSY|NOAUTH|WRONGPASS|LOADING|READONLY)$/.test(key) && /^count=\d+$/.test(value))
      return [[key, Number(value.slice(6))]];
    return [];
  }));
}

export function safeLog(line, service) {
  const timestamp = line.match(/\b\d{4}-\d{2}-\d{2}T[\d:.]+Z\b/)?.[0];
  if (!timestamp) return null;
  // Standalone's `app` container runs Redis next to the backend, so its lines
  // are checked for Redis events first and then for backend events.
  if (service === 'redis' || service === 'app') {
    const events = [/Ready to accept connections/, /Background saving started/,
      /Background saving terminated with success/, /DB saved on disk/, /overcommit_memory/];
    const index = events.findIndex(pattern => pattern.test(line));
    if (index >= 0) return { timestamp, service, event: ['ready', 'save_started', 'save_complete', 'db_saved', 'overcommit_warning'][index] };
    if (service === 'redis') return null;
  }
  // Prod uses JSONRenderer; the E2E overlay uses ENV_TYPE=local and therefore
  // ConsoleRenderer. In either case retain only known events and typed fields.
  const start = line.indexOf('{');
  let value;
  try { if (start >= 0) value = JSON.parse(line.slice(start)); } catch { /* Try the local renderer below. */ }
  if (!value) {
    const plain = line.replace(/\x1b\[[0-9;]*m/g, '');
    const event = plain.match(/\[(?:warning|info|error)\s*\]\s+([a-z_]+)(?:\s|$)/)?.[1];
    if (!EVENTS.has(event)) return null;
    value = { event };
    for (const match of plain.matchAll(/\b([a-z_]+)=(?:'([^']*)'|([^\s]+))/g)) {
      const text = match[2] ?? match[3];
      value[match[1]] = numeric(text) ?? text;
    }
  }
  if (!EVENTS.has(value.event)) return null;
  const result = { timestamp, service, event: value.event };
  const enums = { operation: ['get', 'set', 'delete'], transport_category: ['timeout', 'connection', 'unknown'],
    transport_phase: ['unknown'], fallback: ['validate_database', 'database_validated'],
    outcome: ['authenticated', 'credential_rejected', 'database_unavailable'] };
  for (const [key, allowed] of Object.entries(enums)) if (allowed.includes(value[key])) result[key] = value[key];
  for (const key of ['error_class', 'cause_class', 'source_module', 'source_function'])
    if (typeof value[key] === 'string' && /^[A-Za-z_][A-Za-z0-9_.]{0,127}$/.test(value[key])) result[key] = value[key];
  for (const key of ['elapsed_ms', 'source_line', 'socket_timeout', 'socket_connect_timeout'])
    if (typeof value[key] === 'number' && Number.isFinite(value[key])) result[key] = value[key];
  if (typeof value.request_id === 'string' && /^[0-9a-f-]{36}$/.test(value.request_id)) result.request_id = value.request_id;
  return result;
}

export function dockerEvent(line, ids) {
  let value;
  try { value = JSON.parse(line); } catch { return null; }
  const service = ids.get(value.Actor?.ID);
  if (!service || !['start', 'die', 'restart', 'oom', 'kill', 'stop', 'destroy', 'health_status: unhealthy'].includes(value.Action)) return null;
  return { service, event: value.Action, time: numeric(value.time), timeNano: numeric(value.timeNano) };
}

export function boundedRecords(maxBytes = MAX_BYTES) {
  const records = [];
  let bytes = 0, dropped = 0;
  return {
    add(record) {
      if (!record) return;
      const text = JSON.stringify(record);
      if (bytes + Buffer.byteLength(text) > maxBytes) { dropped++; return; }
      bytes += Buffer.byteLength(text);
      records.push(record); // Keep early failures, not just the final log tail.
    },
    result: () => ({ records, bytes, dropped }),
  };
}

async function command(file, args) {
  const { stdout } = await exec(file, args, { timeout: 5000, killSignal: 'SIGKILL', maxBuffer: 256 * 1024 });
  return stdout;
}

// Mirrors bin/e2e: distributed runs `backend` and `redis` as separate services;
// standalone (E2E_STACK=standalone) runs both inside one `app` service, where
// Redis needs the stack password. REDISCLI_AUTH is read from the container's
// own environment, so the password never appears in argv or in these records.
export function stackServices(stack = process.env.E2E_STACK) {
  return stack === 'standalone'
    ? { containers: ['app'], redisInfo: section => ['app', 'sh', '-c',
      'REDISCLI_AUTH="$REDIS_PASSWORD" exec redis-cli -h 127.0.0.1 INFO "$0"', section] }
    : { containers: ['backend', 'redis'], redisInfo: section => ['redis', 'redis-cli', 'INFO', section] };
}

export async function collectSnapshot(run = command, read = readFile, services = stackServices()) {
  const snapshot = { timestamp: new Date().toISOString(), redis: {}, containers: [], pressure: {}, cgroup: {} };
  await Promise.all(['server', 'clients', 'stats', 'memory', 'persistence', 'errorstats'].map(async section => {
    try { Object.assign(snapshot.redis, redisInfo(await run('bin/e2e', ['compose', 'exec', '-T', ...services.redisInfo(section)]))); }
    catch { snapshot.redis[`${section}_unavailable`] = true; }
  }));
  await Promise.all(services.containers.map(async service => {
    try {
      const id = (await run('bin/e2e', ['compose', 'ps', '-q', service])).trim();
      if (!/^[a-f0-9]{12,64}$/.test(id)) return;
      // Explicit projection, never full inspect (which contains secrets).
      const state = JSON.parse(await run('docker', ['inspect', '--format',
        '{"running":{{.State.Running}},"oom":{{.State.OOMKilled}},"restarts":{{.RestartCount}},"exit":{{.State.ExitCode}}}', id]));
      snapshot.containers.push({ service, running: state.running === true, oom: state.oom === true,
        restarts: numeric(state.restarts), exit: numeric(state.exit) });
      for (const file of ['memory.events', 'cpu.stat']) {
        try {
          const text = await run('docker', ['exec', id, 'cat', `/sys/fs/cgroup/${file}`]);
          snapshot.cgroup[`${service}.${file}`] = Object.fromEntries(text.trim().split('\n').flatMap(line => {
            const [key, value] = line.split(/\s+/);
            return /^[a-z_]+$/.test(key) && numeric(value) !== undefined ? [[key, numeric(value)]] : [];
          }));
        } catch { snapshot.cgroup[`${service}.${file}`] = { unavailable: true }; }
      }
      const stats = JSON.parse(await run('docker', ['stats', '--no-stream', '--format', '{{json .}}', id]));
      snapshot.containers.find(row => row.service === service).usage = Object.fromEntries(
        ['CPUPerc', 'MemUsage', 'MemPerc', 'BlockIO', 'NetIO', 'PIDs'].flatMap(key =>
          typeof stats[key] === 'string' && /^[\d. %/a-zA-Z]+$/.test(stats[key]) ? [[key, stats[key].slice(0, 80)]] : []));
    } catch { snapshot.containers.push({ service, unavailable: true }); }
  }));
  await Promise.all(['cpu', 'memory', 'io'].map(async resource => {
    try {
      const text = await read(`/proc/pressure/${resource}`, 'utf8');
      snapshot.pressure[resource] = text.split('\n').filter(line => /^(some|full) avg10=[\d.]+ avg60=[\d.]+ avg300=[\d.]+ total=\d+$/.test(line));
    } catch { snapshot.pressure[resource] = ['unavailable']; }
  }));
  return snapshot;
}

async function readJson(file) {
  try { return JSON.parse(await readFile(file, 'utf8')); } catch { return null; }
}

async function saveJson(file, value) {
  // Stop can read checkpoints while the collector is sampling. Never expose a
  // partially written JSON document, and keep the final artifact compact/bounded.
  await writeFile(`${file}.tmp`, JSON.stringify(value));
  await rename(`${file}.tmp`, file);
}

function isAlive(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 1) return false;
  try { process.kill(pid, 0); return true; } catch (error) { return error.code !== 'ESRCH'; }
}

export async function startDiagnostics(outputDir, deps = {}) {
  await mkdir(outputDir, { recursive: true });
  const statePath = path.join(outputDir, 'state.json');
  const state = { token: randomUUID(), since: new Date().toISOString(), pid: null };
  // Each run owns a fresh directory. Refuse to overwrite a previous collector,
  // including stale state: stop remains safe and can recover its last checkpoint.
  await writeFile(statePath, JSON.stringify(state), { flag: 'wx' });
  const child = (deps.spawn ?? spawn)(process.execPath,
    [fileURLToPath(import.meta.url), 'collect', '--output-dir', outputDir, '--token', state.token],
    { detached: true, stdio: 'ignore', env: process.env });
  await new Promise((resolve, reject) => {
    child.once('spawn', resolve);
    child.once('error', reject);
  });
  state.pid = child.pid;
  // Unref before filesystem work so even an optional state-write failure cannot
  // hold the start step open. No suite process is ever launched by this script.
  child.unref();
  await saveJson(statePath, state);
  return state;
}

export async function collectDiagnostics(outputDir, token, deps = {}) {
  const state = await readJson(path.join(outputDir, 'state.json'));
  if (!state || state.token !== token) return;
  const run = deps.command ?? command;
  const start = deps.spawn ?? spawn;
  const services = deps.services ?? stackServices();
  const sample = deps.snapshot ?? (() => collectSnapshot(run, readFile, services));
  const evidence = boundedRecords();
  const streams = [];
  const ids = new Map();
  const since = state.since;
  const checkpoint = completed => saveJson(path.join(outputDir, 'collector.json'),
    { token, since, completed, ...evidence.result() });
  let stopping = false;
  const stop = () => { stopping = true; };
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
  function follow(file, args, parse) {
    const child = start(file, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    streams.push(child);
    child.on('error', () => evidence.add({ event: 'collector_unavailable' }));
    for (const stream of [child.stdout, child.stderr]) {
      let pending = '';
      stream.on('data', chunk => {
        // Bound even a single unterminated/malformed line.
        pending = (pending + chunk.toString()).slice(-64 * 1024);
        const lines = pending.split('\n');
        pending = lines.pop();
        for (const line of lines) evidence.add(parse(line));
      });
    }
  }
  const capture = async () => {
    try { evidence.add(await sample()); } catch { evidence.add({ event: 'snapshot_unavailable' }); }
    await checkpoint(false);
  };
  try {
    for (const service of services.containers) {
      try {
        const id = (await run('bin/e2e', ['compose', 'ps', '-q', service])).trim();
        if (!/^[a-f0-9]{12,64}$/.test(id)) continue;
        ids.set(id, service);
        follow('docker', ['logs', '--follow', '--timestamps', '--since', since, id], line => safeLog(line, service));
      } catch { evidence.add({ service, event: 'collector_unavailable' }); }
    }
    follow('docker', ['events', '--since', since, '--filter', 'type=container', '--format', '{{json .}}'], line => dockerEvent(line, ids));
    await capture(); // Initial checkpoint survives a collector crash later on.
    let nextSample = Date.now() + (deps.intervalMs ?? 15_000);
    const deadline = Date.now() + (deps.maxDurationMs ?? 110 * 60_000);
    while (!stopping && Date.now() < deadline) {
      const request = await readJson(path.join(outputDir, 'stop.json'));
      if (request?.token === token) break;
      if (Date.now() >= nextSample) {
        await capture(); // Serial sampling: no overlap or unbounded in-flight work.
        nextSample = Date.now() + (deps.intervalMs ?? 15_000);
      }
      await delay(deps.pollMs ?? 250);
    }
  } finally {
    // Only kill child objects this process spawned, never a PID read from disk.
    // A recycled/stale PID in state must not signal an unrelated process.
    for (const child of streams) child.kill('SIGKILL');
    try { await capture(); await checkpoint(true); }
    finally { process.off('SIGTERM', stop); process.off('SIGINT', stop); }
  }
}

export async function stopDiagnostics(outputDir, suiteOutcome, deps = {}) {
  if (!['success', 'failure', 'cancelled', 'skipped', 'unknown'].includes(suiteOutcome))
    throw new Error('Invalid suite outcome');
  await mkdir(outputDir, { recursive: true });
  const resultPath = path.join(outputDir, 'redis-diagnostics.json');
  const existing = await readJson(resultPath);
  if (existing) return existing; // Repeated stop is idempotent, preserving evidence.
  const state = await readJson(path.join(outputDir, 'state.json'));
  let snapshot, status = 'collector_missing';
  if (state?.token) {
    // Token-addressed cooperative shutdown avoids trusting a possibly recycled
    // PID. A live unrelated process can at worst consume the bounded wait below.
    await saveJson(path.join(outputDir, 'stop.json'), { token: state.token });
    const deadline = Date.now() + (deps.stopTimeoutMs ?? 60_000);
    while (true) {
      const saved = await readJson(path.join(outputDir, 'collector.json'));
      snapshot = saved?.token === state.token ? saved : null;
      if (snapshot?.completed) { status = 'stopped'; break; }
      if (!(deps.isAlive ?? isAlive)(state.pid)) { status = 'collector_stale'; break; }
      if (Date.now() >= deadline) { status = 'collector_stop_timeout'; break; }
      await delay(deps.pollMs ?? 250);
    }
  }
  const evidence = boundedRecords();
  for (const record of snapshot?.records ?? []) evidence.add(record);
  if (status !== 'stopped') evidence.add({ event: status });
  const result = { since: state?.since ?? null, suiteOutcome, collectorStatus: status,
    ...evidence.result(), dropped: evidence.result().dropped + (snapshot?.dropped ?? 0) };
  await saveJson(resultPath, result);
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  // Diagnostics are optional. Workflow continue-on-error is a second boundary;
  // neither launch, stop nor artifact failures may replace the suite's outcome.
  try {
    const [mode, flag, dir, extra, value, ...rest] = process.argv.slice(2);
    if (flag !== '--output-dir' || !dir || rest.length) throw new Error('Invalid diagnostic arguments');
    const outputDir = path.resolve(dir);
    if (mode === 'start' && extra === undefined) await startDiagnostics(outputDir);
    else if (mode === 'stop' && extra === '--suite-outcome') await stopDiagnostics(outputDir, value);
    else if (mode === 'collect' && extra === '--token') await collectDiagnostics(outputDir, value);
    else throw new Error('Invalid diagnostic mode');
  } catch { console.error('Optional Redis diagnostics unavailable'); }
}
