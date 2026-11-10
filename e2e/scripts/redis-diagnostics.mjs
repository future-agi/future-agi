#!/usr/bin/env node
// Optional, bounded CI evidence. Never stores keys, command arguments, env,
// exception messages, raw log lines or Redis connection configuration.
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

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
  if (service === 'redis') {
    const events = [/Ready to accept connections/, /Background saving started/,
      /Background saving terminated with success/, /DB saved on disk/, /overcommit_memory/];
    const index = events.findIndex(pattern => pattern.test(line));
    return index < 0 ? null : { timestamp, service, event: ['ready', 'save_started', 'save_complete', 'db_saved', 'overcommit_warning'][index] };
  }
  // Only JSON structlog records, not a regex across arbitrary exception text.
  const start = line.indexOf('{');
  if (start < 0) return null;
  let value;
  try { value = JSON.parse(line.slice(start)); } catch { return null; }
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

export async function collectSnapshot(run = command, read = readFile) {
  const snapshot = { timestamp: new Date().toISOString(), redis: {}, containers: [], pressure: {}, cgroup: {} };
  await Promise.all(['server', 'clients', 'stats', 'memory', 'persistence', 'errorstats'].map(async section => {
    try { Object.assign(snapshot.redis, redisInfo(await run('bin/e2e', ['compose', 'exec', '-T', 'redis', 'redis-cli', 'INFO', section]))); }
    catch { snapshot.redis[`${section}_unavailable`] = true; }
  }));
  await Promise.all(['backend', 'redis'].map(async service => {
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

export async function runWithDiagnostics(argv, outputDir, deps = {}) {
  const run = deps.command ?? command;
  const start = deps.spawn ?? spawn;
  const sample = deps.snapshot ?? (() => collectSnapshot(run));
  try { await mkdir(outputDir, { recursive: true }); }
  catch { console.error('Optional Redis diagnostic directory unavailable'); }
  const evidence = boundedRecords();
  const streams = [];
  const ids = new Map();
  const since = new Date().toISOString();
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
  for (const service of ['backend', 'redis']) {
    try {
      const id = (await run('bin/e2e', ['compose', 'ps', '-q', service])).trim();
      if (!/^[a-f0-9]{12,64}$/.test(id)) continue;
      ids.set(id, service);
      follow('docker', ['logs', '--follow', '--timestamps', '--since', since, id], line => safeLog(line, service));
    } catch { evidence.add({ service, event: 'collector_unavailable' }); }
  }
  follow('docker', ['events', '--since', since, '--filter', 'type=container', '--format', '{{json .}}'], line => dockerEvent(line, ids));
  let inFlight;
  const capture = async () => {
    try { evidence.add(await sample()); } catch { evidence.add({ event: 'snapshot_unavailable' }); }
  };
  await capture(); // Collect before starting the suite, not only after failure.
  const timer = setInterval(() => {
    if (!inFlight) inFlight = capture().finally(() => { inFlight = undefined; });
  }, 15_000);
  let code = 1;
  try {
    code = await new Promise(resolve => {
      const child = start(argv[0], argv.slice(1), { stdio: 'inherit' });
      child.once('error', () => resolve(127));
      child.once('close', (status, signal) => resolve(status ?? (signal === 'SIGINT' ? 130 : 143)));
    });
  } finally {
    clearInterval(timer);
    // Kill followers promptly even if the final sample fails. No detached children.
    for (const child of streams) child.kill('SIGKILL');
    await inFlight;
    await capture();
    try { await writeFile(path.join(outputDir, 'redis-diagnostics.json'), JSON.stringify({ since, suiteExitCode: code, ...evidence.result() }, null, 2)); }
    catch { console.error('Could not save optional Redis diagnostics'); }
  }
  return code; // Diagnostic failures never turn a failing suite green.
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2), split = args.indexOf('--');
  if (args[0] !== '--output-dir' || split !== 2 || !args[3]) throw new Error('Expected --output-dir DIR -- COMMAND [ARGS]');
  process.exitCode = await runWithDiagnostics(args.slice(3), path.resolve(args[1]));
}
