import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { lstat, readFile, realpath } from 'node:fs/promises';
import { resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:net';
import pg from 'pg';
import { test as fixtures } from './fixtures';
import { E2E } from './env';

// H5 only. Local allowlist; machine-specific pins live in an external run manifest.
export const H5_PIN = {
  context: 'colima-observed-catalog-release',
  project: 'futureagi-e2e',
  network: 'futureagi-e2e_default',
  broker: 'property-catalog-kafka:9092',
  topic: 'futureagi.observed-attributes.v1',
  group: 'futureagi.observed-attributes.consumer.v1',
  sourceDb: 'default',
  catalogDb: 'property_catalog',
} as const;
export interface RuntimePins {
  socket: string; networkId: string; image: string; auditImage: string;
  sourcePorts: { http: number; admin: number };
  partitions: number;
  containers: Record<string, { id: string; image: string }>;
}

const SERVICES = ['fi-collector', 'fi-property-catalog-consumer', 'clickhouse',
  'property-catalog-kafka', 'postgres', 'redis', 'backend', 'frontend'] as const;
const LABEL = 'com.futureagi.e2e.h5';
const BOUNDED_STOP = 'observed catalog backfill: page budget reached; scan incomplete, resume the same checkpoint';
export interface Container {
  Id: string; Name: string; Image: string;
  Config: { Env: string[]; Labels: Record<string, string>; User: string };
  State: { Running: boolean; ExitCode: number; OOMKilled: boolean };
  HostConfig: { NetworkMode: string };
  NetworkSettings: { Networks: Record<string, { NetworkID: string }>;
    Ports: Record<string, { HostIp: string; HostPort: string }[] | null> };
  Mounts: { Type: string; Name?: string; Source: string; Destination: string; RW: boolean }[];
}
type Grant = { access_type: string; database: string | null; table: string | null;
  is_partial_revoke: number; grant_option: number };
export interface Selection {
  organization: string; workspace: string; project: string; since: string; until: string;
}
export interface Progress {
  binding: string; hour: string; after: { Observation: string; Service: string; Trace: string; Span: string };
  published_pages: number; source_rows: number; scan_complete: boolean;
}
interface CommandResult { code: number; stdout: string; stderr: string; uncertain: boolean }
type Receipt = { phase: string; at: string; [key: string]: unknown };
const envOf = (c: Container) => Object.fromEntries(c.Config.Env.map(v => {
  const n = v.indexOf('='); return [v.slice(0, n), v.slice(n + 1)];
}));
function invariant(ok: unknown, message: string): asserts ok {
  if (!ok) throw new Error('H5 refused: ' + message);
}

async function readRuntimePins(): Promise<RuntimePins> {
  const path = process.env.E2E_H5_RUNTIME_MANIFEST;
  invariant(path && resolve(path) === path, 'explicit absolute external runtime manifest required');
  const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
  const info = await lstat(path), canonical = await realpath(path);
  invariant(info.isFile() && !info.isSymbolicLink() && info.size <= 65536 && canonical === path
    && !canonical.startsWith(root + sep), 'runtime manifest must be a small regular file outside the checkout');
  const text = await readFile(path, 'utf8');
  const match = /<!-- H5_RUNTIME_BEGIN -->\s*([\s\S]*?)\s*<!-- H5_RUNTIME_END -->/.exec(text);
  invariant(match, 'external evidence lacks an H5 runtime manifest');
  const pins = JSON.parse(match[1]) as RuntimePins;
  invariant(/^unix:\/\/\/.*\/\.colima\/observed-catalog-release\/docker\.sock$/.test(pins.socket),
    'manifest socket is not the task Colima Unix socket');
  invariant(/^[0-9a-f]{64}$/.test(pins.networkId)
    && [pins.image, pins.auditImage].every(id => /^sha256:[0-9a-f]{64}$/.test(id)),
  'manifest requires immutable local image IDs and network ID');
  invariant(pins.sourcePorts && [pins.sourcePorts.http, pins.sourcePorts.admin].every(p =>
    Number.isInteger(p) && p > 30000 && p <= 65535) && pins.sourcePorts.http !== pins.sourcePorts.admin,
  'manifest requires distinct bounded source ports');
  invariant(Number.isInteger(pins.partitions) && pins.partitions > 0 && pins.partitions <= 32,
    'manifest requires bounded Kafka partition count');
  return pins;
}

export function validateH5Environment(env: NodeJS.ProcessEnv, endpoints = E2E) {
  invariant(env.E2E_H5_LIVE === '1', 'local live run requires explicit coordination opt-in');
  invariant(env.DOCKER_CONTEXT === H5_PIN.context && !env.DOCKER_HOST && !env.DOCKER_TLS_VERIFY,
    'Docker context/host is not the approved local runtime');
  const urls = { appUrl: 'http://localhost:3100', apiUrl: 'http://localhost:8100',
    collectorUrl: 'http://localhost:24318', chUrl: 'http://localhost:28123' };
  for (const [key, value] of Object.entries(urls))
    invariant(endpoints[key as keyof typeof urls] === value, 'nonlocal or unknown ' + key);
  const pg = new URL(endpoints.pgUrl);
  invariant(pg.protocol === 'postgresql:' && pg.hostname === 'localhost' && pg.port === '25432'
    && pg.pathname === '/futureagi' && !pg.search && !pg.hash, 'unknown PostgreSQL attach target');
  invariant(endpoints.chDatabase === H5_PIN.sourceDb, 'source database changed');
  invariant(/^[a-z][a-z0-9_]{2,62}$/.test(env.E2E_H5_CH_USER ?? '')
    && !['default', 'admin', 'observed_catalog_writer'].includes(env.E2E_H5_CH_USER!),
  'explicit SELECT-only source username required; no default/admin fallback');
  invariant(Boolean(env.E2E_H5_CH_PASSWORD), 'explicit source credential required');
}

export function validateSourceGrants(grants: Grant[], roleCount: number) {
  invariant(roleCount === 0, 'source identity has roles; effective privileges require separate review');
  invariant(grants.length > 0 && grants.every(g => g.access_type === 'SELECT'
    && g.is_partial_revoke === 0 && g.grant_option === 0), 'source identity is not SELECT-only');
  invariant(grants.some(g => g.database === H5_PIN.sourceDb && g.table === 'spans'),
    'missing SELECT ON default.spans for a SELECT-only source identity');
}

export function validateSelection(s: Selection) {
  for (const id of [s.organization, s.workspace, s.project])
    invariant(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id)
      && id !== '00000000-0000-0000-0000-000000000000', 'noncanonical scope UUID');
  const start = Date.parse(s.since), end = Date.parse(s.until);
  invariant(Number.isFinite(start) && Number.isFinite(end) && end - start === 3000
    && Math.floor(start / 3_600_000) === Math.floor(end / 3_600_000),
  'H5 selection must be exactly three seconds within one hour');
  invariant(start < Date.now() - 86_400_000 && start > Date.now() - 3 * 86_400_000,
    'historical window is outside the bounded two-day fixture');
}

// main.go scanBinding: same documented inputs, independently serialized here.
export function selectionBinding(s: Selection) {
  const goTime = (t: string) => new Date(t).toISOString().replace('.000Z', 'Z');
  return createHash('sha256').update(JSON.stringify([1, 'spans', 'http://clickhouse:8123',
    H5_PIN.sourceDb, { organization_id: s.organization, workspace_id: s.workspace, project_id: s.project },
    goTime(s.since), goTime(s.until), [H5_PIN.broker], H5_PIN.topic, 0, 0, '',
    { MaxKeysPerSpan: 128, MaxArrayMembersPerSpan: 256 }])).digest('hex');
}

export function validateProgress(p: Progress, s: Selection, previous?: Progress) {
  invariant(p.binding === selectionBinding(s), 'checkpoint scope/destination binding mismatch');
  const hour = Math.floor(Date.parse(s.since) / 3_600_000) * 3_600_000;
  invariant([hour, hour + 3_600_000].includes(Date.parse(p.hour)), 'checkpoint hour outside scan');
  invariant(Number.isInteger(p.published_pages) && p.published_pages >= 1 && p.published_pages <= 8
    && Number.isInteger(p.source_rows) && p.source_rows >= 0 && p.source_rows <= 14,
  'checkpoint exceeds H5 row/page ceiling');
  invariant(typeof p.scan_complete === 'boolean'
    && p.scan_complete === (Date.parse(p.hour) === hour + 3_600_000), 'invalid completion checkpoint');
  invariant(p.after && ['Observation', 'Service', 'Trace', 'Span'].every(k =>
    typeof p.after[k as keyof Progress['after']] === 'string'), 'invalid physical cursor');
  if (previous) invariant(p.published_pages >= previous.published_pages
    && p.source_rows >= previous.source_rows, 'checkpoint regressed');
}

export function validateCLIResult(result: CommandResult, preview: boolean, progress?: Progress,
  previous?: Progress) {
  invariant(!result.uncertain, 'CLI outcome uncertain; retain receipt, do not retry');
  const lines = result.stdout.trim() ? result.stdout.trim().split('\n').map(s => JSON.parse(s)) : [];
  if (previous?.scan_complete) {
    invariant(result.code === 0 && lines.length === 0 && JSON.stringify(progress) === JSON.stringify(previous),
      'completed resume changed progress or emitted publication');
    return lines;
  }
  invariant(lines.length >= 1 && lines.length <= (preview ? 8 : 2), 'missing/excess CLI page receipts');
  invariant(lines.every(r => r.preview === preview && r.consumer_visibility_verified === false
    && Number.isInteger(r.source_rows) && r.source_rows >= 0 && r.source_rows <= 2
    && Number.isInteger(r.keys) && r.keys >= 0 && Number.isInteger(r.values) && r.values >= 0),
  'malformed CLI page receipt');
  const last = lines.at(-1)!;
  invariant((result.code === 0 && last.scan_complete === true)
    || (!preview && result.code === 1 && result.stderr.trim() === BOUNDED_STOP
      && last.scan_complete === false && progress?.scan_complete === false),
  'unexpected CLI failure or incomplete preview');
  if (!preview) invariant(progress && progress.published_pages === last.pages_read
    && progress.source_rows === (previous?.source_rows ?? 0)
      + lines.reduce((sum, row) => sum + row.source_rows, 0), 'receipt/checkpoint disagreement');
  return lines;
}

export function validateRuntime(containers: Container[], socket: string, pins: RuntimePins) {
  invariant(socket === pins.socket && containers.length === SERVICES.length, 'unknown runtime/socket');
  for (const service of SERVICES) {
    const matches = containers.filter(c => c.Name === '/futureagi-e2e-' + service + '-1');
    invariant(matches.length === 1, 'ambiguous/missing service ' + service);
    const c = matches[0];
    invariant(c.Id === pins.containers[service]?.id && c.Image === pins.containers[service]?.image,
      'container differs from approved runtime manifest: ' + service);
    invariant(c.State.Running && /^[0-9a-f]{64}$/.test(c.Id)
      && c.Config.Labels['com.docker.compose.project'] === H5_PIN.project
      && c.Config.Labels['com.docker.compose.service'] === service
      && Object.keys(c.NetworkSettings.Networks).length === 1
      && c.NetworkSettings.Networks[H5_PIN.network]?.NetworkID === pins.networkId,
    'wrong project/network/state for ' + service);
  }
  const main = containers.find(c => c.Name.endsWith('-fi-collector-1'))!;
  const consumer = containers.find(c => c.Name.endsWith('-fi-property-catalog-consumer-1'))!;
  invariant(main.Image === pins.image && consumer.Image === pins.image, 'collector image ID drift');
  const m = envOf(main), c = envOf(consumer);
  invariant(m.FI_OBSERVED_CATALOG_MODE === 'kafka' && m.FI_CH_URL === 'http://clickhouse:8123'
    && m.FI_CH_DATABASE === H5_PIN.sourceDb && m.FI_AUTH_REDIS_ADDR === 'redis:6379',
  'source/live collector routing drift');
  for (const e of [m, c]) invariant(e.FI_OBSERVED_CATALOG_KAFKA_BROKERS === H5_PIN.broker
    && e.FI_OBSERVED_CATALOG_KAFKA_TOPIC === H5_PIN.topic, 'Kafka routing drift');
  invariant(c.FI_OBSERVED_CATALOG_CH_DATABASE === H5_PIN.catalogDb
    && c.FI_OBSERVED_CATALOG_CH_URL === 'http://clickhouse:8123'
    && c.FI_OBSERVED_CATALOG_KAFKA_GROUP === H5_PIN.group, 'consumer destination drift');
  invariant((m.FI_OBSERVED_CATALOG_MAX_KEYS_PER_SPAN ?? '128') === '128'
    && (m.FI_OBSERVED_CATALOG_MAX_ARRAY_MEMBERS_PER_SPAN ?? '256') === '256', 'extractor limits changed');
  for (const key of ['FI_PG_WRITE', 'FI_PG_READ']) {
    const u = new URL(m[key]);
    invariant(u.protocol === 'postgres:' && u.hostname === 'postgres' && u.port === '5432'
      && u.pathname === '/futureagi' && u.search === '?sslmode=disable', 'collector PG auth/default drift');
  }
  for (const [service, port, host] of [['fi-collector', '4318/tcp', '24318'], ['clickhouse', '8123/tcp', '28123'],
    ['postgres', '5432/tcp', '25432'], ['backend', '80/tcp', '8100'], ['frontend', '80/tcp', '3100']]) {
    const target = containers.find(c => c.Name === '/futureagi-e2e-' + service + '-1')!;
    invariant(target.NetworkSettings.Ports[port]?.some(p => p.HostPort === host
      && ['127.0.0.1', '0.0.0.0'].includes(p.HostIp)), 'attach port does not match pinned container');
  }
}

export function validateOwnedContainer(c: Container, role: string, runId: string,
  owned: { id: string; image: string }, pins: RuntimePins) {
  const network = c.NetworkSettings.Networks[H5_PIN.network];
  invariant(c.Id === owned.id && c.Image === owned.image && c.Name === '/' + runId + '-' + role
    && c.Config.Labels[LABEL] === runId
    && c.Config.Labels['com.docker.compose.project'] === H5_PIN.project
    && c.HostConfig.NetworkMode === H5_PIN.network
    && Object.keys(c.NetworkSettings.Networks).length === 1 && network
    // Docker fills NetworkID only when a created container is first started.
    && (network.NetworkID === pins.networkId || (!c.State.Running && network.NetworkID === '')),
  'task container identity changed');
  if (owned.image === pins.image) invariant(c.Mounts.length === 1
    && c.Mounts[0].Type === 'volume' && c.Mounts[0].RW
    && c.Mounts[0].Name === runId + (role === 'source' ? '-source' : '-checkpoints')
    && c.Mounts[0].Destination === '/var/lib/fi-collector', 'task checkpoint/source mount changed');
}

export function parseOffsets(output: string, partitions: number) {
  const rows = output.split('\n').map(line => line.trim().split(/\s+/))
    .filter(c => c[0] === H5_PIN.group && c[1] === H5_PIN.topic)
    .map(c => ({ partition: Number(c[2]), committed: c[3] === '-' ? null : Number(c[3]), end: Number(c[4]) }))
    .sort((a, b) => a.partition - b.partition);
  invariant(rows.length === partitions && rows.every((r, i) => r.partition === i
    && Number.isSafeInteger(r.end) && r.end >= 0
    && (r.committed === null || Number.isSafeInteger(r.committed)
      && r.committed >= 0 && r.end >= r.committed)), 'missing/invalid consumed-offset evidence');
  return rows;
}

export function reachedOffsetBarrier(barrier: ReturnType<typeof parseOffsets>, latest: ReturnType<typeof parseOffsets>) {
  return barrier.every(b => latest.some(r => r.partition === b.partition
    && (r.committed === null ? b.end === 0 : r.committed >= b.end)));
}

// Fixed Docker executable/argv, bounded output and deadline. Never a shell command.
async function docker(args: string[], env: NodeJS.ProcessEnv = {}, binary = false): Promise<CommandResult> {
  return new Promise(resolve => execFile('docker', ['--context', H5_PIN.context, ...args],
    { env: { ...process.env, ...env }, timeout: 60_000, maxBuffer: 2 * 1024 * 1024,
      encoding: binary ? 'latin1' : 'utf8' }, (error, stdout, stderr) => {
      resolve({ code: error ? (typeof error.code === 'number' ? error.code : -1) : 0,
        stdout: String(stdout), stderr: String(stderr), uncertain: Boolean(error?.killed) || error?.code === 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER' });
    }));
}
async function checked(args: string[], env?: NodeJS.ProcessEnv) {
  const r = await docker(args, env);
  invariant(r.code === 0 && !r.uncertain, 'Docker read/operation failed: ' + args[0]);
  return r.stdout;
}

/** Small task-owned runner. No arbitrary containers, commands, SQL writes or cleanup API. */
export class CatalogLifecycle {
  readonly receipts: Receipt[] = [];
  private owned = new Map<string, { id: string; image: string }>();
  private counts = new Map<string, number>();
  private progress = new Map<string, Progress>();
  private selection?: Selection;
  private audit = '';
  private volumes = new Set<string>();
  private sourceUser = process.env.E2E_H5_CH_USER!;
  private sourcePassword = process.env.E2E_H5_CH_PASSWORD!;
  private constructor(private containers: Container[], readonly runId: string, private pins: RuntimePins) {}

  static async inspect(runId: string) {
    validateH5Environment(process.env);
    invariant(/^e2e-h5-[a-z0-9-]{1,48}$/.test(runId), 'invalid task resource prefix');
    const pins = await readRuntimePins();
    const socket = JSON.parse(await checked(['context', 'inspect', H5_PIN.context]))[0].Endpoints.docker.Host;
    const containers: Container[] = JSON.parse(await checked(['inspect',
      ...SERVICES.map(s => 'futureagi-e2e-' + s + '-1')]));
    validateRuntime(containers, socket, pins);
    const h5 = new CatalogLifecycle(containers, runId, pins);
    const user = h5.sourceUser;
    const grants = await h5.adminSelect<Grant>('SELECT access_type, database, table, is_partial_revoke, grant_option'
      + ' FROM system.grants WHERE user_name={u:String}', { u: user });
    const roles = await h5.adminSelect<{ n: string }>('SELECT count() AS n FROM system.role_grants WHERE user_name={u:String}', { u: user });
    validateSourceGrants(grants, Number(roles[0].n));
    await h5.sourceSelect('SELECT id FROM spans LIMIT 0');
    const kafka = h5.service('property-catalog-kafka');
    const cli = await docker(['exec', kafka.Id, '/bin/sh', '-c',
      'test -x /opt/kafka/bin/kafka-get-offsets.sh && test -x /opt/kafka/bin/kafka-consumer-groups.sh && command -v java']);
    if (cli.code === 0) h5.audit = kafka.Id;
    else {
      invariant(process.env.E2E_H5_AUDIT_CONTAINER === '1',
        'Kafka native image lacks read-only CLIs; coordinate the retained local audit container');
      const image = JSON.parse(await checked(['image', 'inspect', pins.auditImage]))[0];
      invariant(image.Id === pins.auditImage, 'local audit image missing');
    }
    h5.receipts.push({ phase: 'preflight', at: new Date().toISOString(), context: H5_PIN.context,
      imageId: pins.image, sourceUser: user, grants, containers: containers.map(c =>
        ({ id: c.Id, name: c.Name, image: c.Image })), audit: h5.audit ? 'existing' : 'task-owned-required' });
    return h5;
  }
  private service(name: string) { return this.containers.find(c => c.Name === '/futureagi-e2e-' + name + '-1')!; }
  async recheck() {
    validateH5Environment(process.env);
    const socket = JSON.parse(await checked(['context', 'inspect', H5_PIN.context]))[0].Endpoints.docker.Host;
    const now: Container[] = JSON.parse(await checked(['inspect', ...this.containers.map(c => c.Name.slice(1))]));
    validateRuntime(now, socket, this.pins);
    invariant(now.every((c, i) => c.Id === this.containers[i].Id && c.Image === this.containers[i].Image
      && JSON.stringify(c.Config.Env) === JSON.stringify(this.containers[i].Config.Env)),
    'runtime changed since preflight; coordinate a fresh run');
  }
  private async adminSelect<T>(sql: string, params: Record<string, string> = {}): Promise<T[]> {
    invariant(sql.startsWith('SELECT ') && !sql.includes(';'), 'one metadata SELECT only');
    return JSON.parse('[' + (await checked(['exec', this.service('clickhouse').Id, 'clickhouse-client',
      '--readonly', '1', ...Object.entries(params).flatMap(([k, v]) => ['--param_' + k, v]),
      '--query', sql + ' FORMAT JSONEachRow'])).trim().split('\n').filter(Boolean).join(',') + ']');
  }
  async sourceSelect<T>(sql: string, params: Record<string, string> = {}): Promise<T[]> {
    invariant(sql.startsWith('SELECT ') && !sql.includes(';'), 'one source SELECT only');
    const u = new URL(E2E.chUrl);
    for (const [k, v] of Object.entries({ database: H5_PIN.sourceDb, readonly: '1',
      max_execution_time: '10', max_threads: '1', max_result_rows: '1000', max_result_bytes: '1048576',
      result_overflow_mode: 'throw', ...Object.fromEntries(Object.entries(params).map(([k, v]) => ['param_' + k, v])) }))
      u.searchParams.set(k, v);
    const r = await fetch(u, { method: 'POST', body: sql + ' FORMAT JSONEachRow', redirect: 'error',
      signal: AbortSignal.timeout(15_000),
      headers: { Authorization: 'Basic ' + Buffer.from(this.sourceUser + ':' + this.sourcePassword).toString('base64') } });
    invariant(r.ok && !r.headers.has('X-ClickHouse-Exception-Code'), 'source SELECT failed; no credential fallback');
    const body = await r.text();
    invariant(body.length <= 1_048_576, 'source response exceeds receipt bound');
    return body.trim() ? body.trim().split('\n').map(line => JSON.parse(line)) : [];
  }
  private async create(role: string, entrypoint: string, command: string[], env: NodeJS.ProcessEnv = {},
    source = false, image: string = this.pins.image) {
    await this.recheck();
    invariant(!this.owned.has(role), 'task role already exists');
    const name = this.runId + '-' + role;
    const args = ['create', '--name', name, '--pull', 'never', '--network', H5_PIN.network,
      '--label', LABEL + '=' + this.runId, '--label', 'com.docker.compose.project=' + H5_PIN.project,
      '--restart', 'no', '--cpus', '0.5', '--memory', source || role === 'audit' ? '512m' : '768m',
      '--pids-limit', '128', '--security-opt', 'no-new-privileges', '--cap-drop', 'ALL'];
    if (role === 'audit') args.push('--init');
    if (image === this.pins.image) {
      const volume = this.runId + (source ? '-source' : '-checkpoints');
      if (!this.volumes.has(volume)) {
        const existing = await checked(['volume', 'ls', '--filter', 'name=' + volume, '--format', '{{.Name}}']);
        invariant(!existing.trim(), 'refusing a pre-existing task volume');
        await checked(['volume', 'create', '--label', LABEL + '=' + this.runId, volume]);
        this.volumes.add(volume);
        this.receipts.push({ phase: 'volume-created', at: new Date().toISOString(), volume });
      }
      const v = JSON.parse(await checked(['volume', 'inspect', volume]))[0];
      invariant(v.Name === volume && v.Labels?.[LABEL] === this.runId, 'task volume ownership changed');
      args.push('--mount', 'type=volume,src=' + volume + ',dst=/var/lib/fi-collector');
    }
    if (source) {
      // Colima forwards VM-published ports to the host. A free VM ephemeral port
      // need not be free on the host (or another local Docker context).
      for (const port of Object.values(this.pins.sourcePorts)) for (const host of ['127.0.0.1', '::1'])
        await new Promise<void>((resolve, reject) => {
          const server = createServer();
          server.once('error', () => reject(new Error('H5 source host port already occupied')));
          server.listen({ host, port, exclusive: true }, () => server.close(() => resolve()));
        });
      args.push('-p', '127.0.0.1:' + this.pins.sourcePorts.http + ':4318',
        '-p', '127.0.0.1:' + this.pins.sourcePorts.admin + ':9464');
    }
    args.push(...Object.keys(env).flatMap(k => ['-e', k]), '--entrypoint', entrypoint, image, ...command);
    const id = (await checked(args, env)).trim();
    invariant(/^[0-9a-f]{64}$/.test(id), 'invalid created container ID');
    this.owned.set(role, { id, image });
    this.receipts.push({ phase: 'create-' + role, at: new Date().toISOString(), id, image, args,
      environmentKeys: Object.keys(env) });
    return id;
  }
  private async ownedContainer(role: string) {
    const owned = this.owned.get(role);
    invariant(owned, 'container is not task-owned');
    const c: Container = JSON.parse(await checked(['inspect', owned.id]))[0];
    validateOwnedContainer(c, role, this.runId, owned, this.pins);
    return c;
  }
  async startSource() {
    if (!this.owned.has('source')) {
      const e = envOf(this.service('fi-collector'));
      await this.create('source', '/usr/local/bin/fi-collector', ['-config', '/etc/fi-collector/config.yaml'], {
        FI_OBSERVED_CATALOG_MODE: 'disabled', FI_CH_URL: e.FI_CH_URL, FI_CH_DATABASE: e.FI_CH_DATABASE,
        FI_PG_WRITE: e.FI_PG_WRITE, FI_PG_READ: e.FI_PG_READ, FI_AUTH_REDIS_ADDR: e.FI_AUTH_REDIS_ADDR,
        ...(e.FI_CH_USERNAME ? { FI_CH_USERNAME: e.FI_CH_USERNAME, FI_CH_PASSWORD: e.FI_CH_PASSWORD ?? '' } : {}),
        FI_HTTP_ADDR: ':4318', FI_ADMIN_ADDR: ':9464',
      }, true);
    }
    await this.recheck();
    const c = await this.ownedContainer('source');
    invariant(!c.State.Running, 'source collector already running');
    await checked(['start', c.Id]);
    const started = await this.ownedContainer('source');
    const port = (p: string) => {
      const bindings = started.NetworkSettings.Ports[p];
      invariant(bindings?.length === 1 && bindings[0].HostIp === '127.0.0.1', 'source port is not loopback');
      return 'http://localhost:' + bindings[0].HostPort;
    };
    const endpoints = { collectorUrl: port('4318/tcp'), healthUrl: port('9464/tcp') + '/healthz' };
    this.receipts.push({ phase: 'source-started', at: new Date().toISOString(), id: c.Id, ...endpoints });
    return endpoints;
  }
  async stop(role: string) {
    if (!this.owned.has(role)) return;
    await this.recheck();
    const c = await this.ownedContainer(role);
    if (c.State.Running) await checked(['stop', '--time', '10', c.Id]);
    this.receipts.push({ phase: 'retained-' + role, at: new Date().toISOString(), id: c.Id,
      mounts: c.Mounts, state: (await this.ownedContainer(role)).State });
  }
  async bind(s: Selection) {
    validateSelection(s);
    invariant(!this.selection, 'selection already bound');
    await this.recheck();
    const client = new pg.Client({ connectionString: E2E.pgUrl,
      options: '-c default_transaction_read_only=on -c statement_timeout=10000', connectionTimeoutMillis: 10000 });
    try {
      await client.connect();
      const result = await client.query('SELECT p.id FROM tracer_project p JOIN accounts_workspace w'
        + ' ON w.id=p.workspace_id AND w.organization_id=p.organization_id'
        + ' WHERE p.id=$1 AND p.organization_id=$2 AND p.workspace_id=$3 AND p.name=$4'
        + ' AND NOT p.deleted AND NOT w.deleted AND w.is_active',
      [s.project, s.organization, s.workspace, this.runId]);
      invariant(result.rows.length === 1, 'project is not the task-owned current actor scope');
    } finally { await client.end(); }
    this.selection = Object.freeze({ ...s });
  }
  private async checkpoint(role: string) {
    const c = await this.ownedContainer(role);
    const result = await docker(['cp', c.Id + ':/var/lib/fi-collector/' + role + '.json', '-'], {}, true);
    invariant(result.code === 0 && !result.uncertain, 'checkpoint missing or unreadable');
    const tar = Buffer.from(result.stdout, 'latin1');
    const size = parseInt(tar.subarray(124, 136).toString('ascii').replace(/\0/g, '').trim(), 8);
    invariant(tar.length >= 1024 && ['0', '\0'].includes(tar.subarray(156, 157).toString())
      && size > 0 && size <= 262144 && tar.length >= 512 + size, 'checkpoint must be a bounded regular file');
    return JSON.parse(tar.subarray(512, 512 + size).toString('utf8')) as Progress;
  }
  async backfill(role: 'preview' | 'primary' | 'overlap') {
    invariant(this.selection, 'owned source selection required');
    validateSelection(this.selection);
    const s = this.selection, preview = role === 'preview', previous = this.progress.get(role);
    const count = (this.counts.get(role) ?? 0) + 1;
    invariant(count <= (preview ? 1 : 5), 'bounded invocation ceiling reached');
    this.counts.set(role, count);
    if (!this.owned.has(role)) {
      const env: NodeJS.ProcessEnv = { FI_PG_DSN: envOf(this.service('fi-collector')).FI_PG_READ,
        FI_OBSERVED_BACKFILL_CH_URL: 'http://clickhouse:8123',
        FI_OBSERVED_BACKFILL_CH_DATABASE: H5_PIN.sourceDb,
        FI_OBSERVED_BACKFILL_CH_USERNAME: this.sourceUser, FI_OBSERVED_BACKFILL_CH_PASSWORD: this.sourcePassword };
      if (!preview) Object.assign(env, { FI_OBSERVED_CATALOG_KAFKA_BROKERS: H5_PIN.broker,
        FI_OBSERVED_CATALOG_KAFKA_TOPIC: H5_PIN.topic, FI_OBSERVED_CATALOG_KAFKA_TIMEOUT: '10s' });
      await this.create(role, '/usr/local/bin/fi-observed-catalog-backfill', [
        '--source', 'spans', '--project', s.project, '--since', s.since, '--until', s.until,
        '--page-size', '2', '--max-pages', preview ? '8' : '2', '--page-delay', '100ms',
        ...(!preview ? ['--apply', '--checkpoint', '/var/lib/fi-collector/' + role + '.json'] : []),
      ], env);
    }
    await this.recheck();
    const c = await this.ownedContainer(role);
    invariant(!c.State.Running, 'another invocation owns checkpoint');
    if (previous) invariant(JSON.stringify(await this.checkpoint(role)) === JSON.stringify(previous),
      'checkpoint changed outside this task');
    const result = await docker(['start', '--attach', c.Id]);
    // Record first, even when startup, checkpoint or CLI validation fails.
    this.receipts.push({ phase: role, at: new Date().toISOString(), invocation: count, ...result });
    const state = (await this.ownedContainer(role)).State;
    invariant(!state.Running && !state.OOMKilled && !result.uncertain, 'CLI outcome uncertain; no retry');
    const progress = preview ? undefined : await this.checkpoint(role);
    if (preview) {
      const absent = await docker(['cp', c.Id + ':/var/lib/fi-collector/preview.json', '-']);
      invariant(absent.code === 1 && /Could not find the file/.test(absent.stderr),
        'preview checkpoint unexpectedly exists or cannot be inspected');
    }
    if (progress) validateProgress(progress, s, previous);
    const pages = validateCLIResult(result, preview, progress, previous);
    if (progress) this.progress.set(role, progress);
    this.receipts.push({ phase: role + '-receipt', at: new Date().toISOString(), pages, progress });
    return { pages, progress };
  }
  async offsets() {
    await this.recheck();
    if (!this.audit) {
      this.audit = await this.create('audit', '/bin/sh', ['-c', 'exec sleep 600'], {}, false, this.pins.auditImage);
      await checked(['start', this.audit]);
    }
    const output = await checked(['exec', '-e', 'KAFKA_HEAP_OPTS=-Xms32m -Xmx128m', this.audit,
      '/opt/kafka/bin/kafka-consumer-groups.sh', '--bootstrap-server', H5_PIN.broker,
      '--describe', '--group', H5_PIN.group]);
    this.receipts.push({ phase: 'offsets-raw', at: new Date().toISOString(), output });
    const rows = parseOffsets(output, this.pins.partitions);
    this.receipts.push({ phase: 'offsets', at: new Date().toISOString(), rows });
    return rows;
  }
  async retainAndStop() {
    const errors: string[] = [];
    for (const role of this.owned.keys()) {
      try { await this.stop(role); } catch (error) { errors.push(role + ': ' + String(error)); }
    }
    invariant(errors.length === 0, 'retained resources need attention: ' + errors.join('; '));
  }
}

// Automatic worker preflight precedes inherited actor provisioning. Offline
// guard tests import Playwright's base test and cannot instantiate this runner.
export const catalogLifecycleTest = fixtures.extend<{}, { lifecycle: CatalogLifecycle }>({
  lifecycle: [async ({}, use, workerInfo) => {
    const h5 = await CatalogLifecycle.inspect('e2e-h5-' + workerInfo.workerIndex + '-' + Date.now().toString(36));
    try { await use(h5); } finally { await h5.retainAndStop(); }
  }, { scope: 'worker', auto: true }],
});
