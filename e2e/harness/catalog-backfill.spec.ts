import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import { test, expect } from '@playwright/test';
import { catalogLifecycleTest as live, H5_PIN, h5DockerSocket, validateH5Environment, validateRuntime,
  validateSelection, validateSourceGrants, validateProgress, validateCLIResult, selectionBinding,
  validateOwnedContainer, parseOffsets, reachedOffsetBarrier,
  type Container, type Progress, type Selection, type RuntimePins } from '../lib/catalog-lifecycle';
import { E2E } from '../lib/env';
import { sendTrace, type SeededTrace, type SendTraceConfig } from '../lib/otlp';
import { POLL } from '../lib/state-probe';

// Harness prerequisite H5; OBS-E2E-010 owns the separate browser journey.
// No @flow annotation or shared catalog/config changes.
const DECLARED_CONTEXT = 'e2e-h5-declared-context';
const DECLARED_SOCKET = 'unix:///var/run/docker.sock';
const guardEnv = { E2E_H5_LIVE: '1', E2E_H5_DOCKER_CONTEXT: DECLARED_CONTEXT,
  DOCKER_CONTEXT: DECLARED_CONTEXT,
  E2E_H5_CH_USER: 'h5_source_reader', E2E_H5_CH_PASSWORD: 'offline-placeholder' };
// Synthetic guard data only; live runtime pins are read from external evidence.
const offlinePins: RuntimePins = { socket: DECLARED_SOCKET,
  networkId: 'a'.repeat(64), image: 'sha256:' + 'b'.repeat(64), auditImage: 'sha256:' + 'c'.repeat(64),
  sourcePorts: { http: 34318, admin: 39464 },
  partitions: 6,
  containers: Object.fromEntries(['fi-collector', 'fi-property-catalog-consumer', 'clickhouse',
    'property-catalog-kafka', 'postgres', 'redis', 'backend', 'frontend'].map((s, i) =>
    [s, { id: String(i + 1).repeat(64), image: 'sha256:' + 'b'.repeat(64) }])) };
const historicalStart = () => Math.floor((Date.now() - 2 * 86_400_000) / 3_600_000) * 3_600_000 + 600_000;
function selection(): Selection {
  const start = historicalStart();
  return { organization: '00000000-0000-4000-8000-000000000001',
    workspace: '00000000-0000-4000-8000-000000000002', project: '00000000-0000-4000-8000-000000000003',
    since: new Date(start).toISOString(), until: new Date(start + 3000).toISOString() };
}
function checkpoint(s = selection()): Progress {
  return { binding: selectionBinding(s), hour: new Date(Math.floor(Date.parse(s.since) / 3_600_000) * 3_600_000).toISOString(),
    after: { Observation: 'llm', Service: 'e2e-h5-offline', Trace: s.project, Span: '0000000000000001' },
    published_pages: 2, source_rows: 4, scan_complete: false };
}
function runtime(): Container[] {
  const source = { FI_CH_URL: 'http://clickhouse:8123', FI_CH_DATABASE: 'default',
    FI_AUTH_REDIS_ADDR: 'redis:6379', FI_OBSERVED_CATALOG_MODE: 'kafka',
    FI_PG_WRITE: 'postgres://user:offline@postgres:5432/futureagi?sslmode=disable',
    FI_PG_READ: 'postgres://user:offline@postgres:5432/futureagi?sslmode=disable' };
  return ['fi-collector', 'fi-property-catalog-consumer', 'clickhouse', 'property-catalog-kafka',
    'postgres', 'redis', 'backend', 'frontend'].map((service, i) => ({
    Id: String(i + 1).repeat(64), Name: '/futureagi-e2e-' + service + '-1', Image: offlinePins.image,
    State: { Running: true, ExitCode: 0, OOMKilled: false },
    HostConfig: { NetworkMode: H5_PIN.network },
    Config: { User: 'nonroot', Labels: { 'com.docker.compose.project': H5_PIN.project,
      'com.docker.compose.service': service },
    Env: Object.entries({ ...source, FI_OBSERVED_CATALOG_KAFKA_BROKERS: H5_PIN.broker,
      FI_OBSERVED_CATALOG_KAFKA_TOPIC: H5_PIN.topic, FI_OBSERVED_CATALOG_KAFKA_GROUP: H5_PIN.group,
      FI_OBSERVED_CATALOG_CH_URL: 'http://clickhouse:8123',
      FI_OBSERVED_CATALOG_CH_DATABASE: 'property_catalog' }).map(([k, v]) => k + '=' + v) },
    NetworkSettings: { Networks: { [H5_PIN.network]: { NetworkID: offlinePins.networkId } }, Ports: {
      '4318/tcp': [{ HostIp: '127.0.0.1', HostPort: '24318' }],
      '8123/tcp': [{ HostIp: '127.0.0.1', HostPort: '28123' }],
      '5432/tcp': [{ HostIp: '127.0.0.1', HostPort: '25432' }],
      '80/tcp': [{ HostIp: '0.0.0.0', HostPort: service === 'frontend' ? '3100' : '8100' }],
    } }, Mounts: [],
  }));
}

test.describe('H5 offline guards', { tag: '@h5-guard' }, () => {
  test('rejects nonlocal attach and missing authority before a mutation callback', () => {
    const changes: [NodeJS.ProcessEnv, typeof E2E][] = [
      [{ ...guardEnv, E2E_H5_LIVE: undefined }, E2E],
      [{ ...guardEnv, DOCKER_CONTEXT: 'colima' }, E2E],
      [{ ...guardEnv, DOCKER_CONTEXT: 'fi-catalog-simple-ch-20260907' }, E2E],
      [{ ...guardEnv, DOCKER_CONTEXT: undefined }, E2E],
      [{ ...guardEnv, E2E_H5_DOCKER_CONTEXT: undefined }, E2E],
      [{ ...guardEnv, E2E_H5_DOCKER_CONTEXT: '', DOCKER_CONTEXT: '' }, E2E],
      [{ ...guardEnv, DOCKER_HOST: 'tcp://remote:2375' }, E2E],
      [{ ...guardEnv, E2E_H5_CH_USER: 'default' }, E2E],
      [{ ...guardEnv, E2E_H5_CH_USER: 'observed_catalog_writer' }, E2E],
      [{ ...guardEnv, E2E_H5_CH_USER: 'reader; touch /tmp/oops' }, E2E],
      [{ ...guardEnv, E2E_H5_CH_PASSWORD: '' }, E2E],
      [guardEnv, { ...E2E, apiUrl: 'https://api.futureagi.com' }],
      [guardEnv, { ...E2E, collectorUrl: 'http://localhost:4318' }],
      [guardEnv, { ...E2E, chUrl: 'http://localhost:28123?readonly=0' }],
      [guardEnv, { ...E2E, pgUrl: 'postgresql://user:password@remote:5432/futureagi' }],
      [guardEnv, { ...E2E, chDatabase: 'property_catalog' }],
    ];
    let mutations = 0;
    for (const [env, endpoints] of changes) expect(() => {
      validateH5Environment(env, endpoints); mutations++;
    }).toThrow('H5 refused');
    expect(mutations).toBe(0);
    expect(() => validateH5Environment(guardEnv)).not.toThrow();
    // The declared socket is required, absolute, and never a remote daemon.
    for (const bad of [{}, { E2E_H5_DOCKER_SOCKET: '' }, { E2E_H5_DOCKER_SOCKET: 'tcp://remote:2375' },
      { E2E_H5_DOCKER_SOCKET: '/var/run/docker.sock' }, { E2E_H5_DOCKER_SOCKET: 'unix:///a/../b.sock' }])
      expect(() => h5DockerSocket(bad)).toThrow('H5 refused');
    expect(h5DockerSocket({ E2E_H5_DOCKER_SOCKET: DECLARED_SOCKET })).toBe(DECLARED_SOCKET);
  });

  test('rejects unknown runtime, image, routing and auth defaults', () => {
    const cases: ((c: Container[]) => void)[] = [
      c => c.pop(),
      c => { c[0].Config.Labels['com.docker.compose.project'] = 'protected'; },
      c => { c[0].Config.Labels['com.docker.compose.service'] = 'other'; },
      c => { c[0].NetworkSettings.Networks[H5_PIN.network].NetworkID = 'unknown'; },
      c => { c[0].Image = 'futureagi/fi-collector:e2e-local'; },
      c => { c[1].Image = 'sha256:' + '0'.repeat(64); },
      c => { c[0].State.Running = false; },
      c => { c[0].Config.Env.push('FI_OBSERVED_CATALOG_KAFKA_BROKERS=remote:9092'); },
      c => { c[1].Config.Env.push('FI_OBSERVED_CATALOG_CH_DATABASE=default'); },
      c => { c[0].Config.Env.push('FI_PG_WRITE=postgres://user:password@db:5432/tfc'); },
      c => { c[0].Config.Env.push('FI_OBSERVED_CATALOG_MAX_KEYS_PER_SPAN=256'); },
      c => { c[6].NetworkSettings.Ports['80/tcp']![0].HostPort = '80'; },
    ];
    expect(() => validateRuntime(runtime(), offlinePins.socket, offlinePins)).not.toThrow();
    for (const change of cases) {
      const c = runtime(); change(c);
      expect(() => validateRuntime(c, offlinePins.socket, offlinePins)).toThrow();
    }
    expect(() => validateRuntime(runtime(), 'tcp://remote:2375', offlinePins)).toThrow('runtime/socket');
  });

  test('requires actual source SELECT with no writes, grant option or unresolved roles', () => {
    const read = { access_type: 'SELECT', database: 'default', table: 'spans', is_partial_revoke: 0, grant_option: 0 };
    expect(() => validateSourceGrants([read], 0)).not.toThrow();
    expect(() => validateSourceGrants([{ ...read, database: 'property_catalog', table: 'observed_attribute_keys' }], 0))
      .toThrow('missing SELECT ON default.spans');
    for (const invalid of [{ ...read, access_type: 'INSERT' }, { ...read, grant_option: 1 },
      { ...read, is_partial_revoke: 1 }])
      expect(() => validateSourceGrants([read, invalid], 0)).toThrow('SELECT-only');
    expect(() => validateSourceGrants([read], 1)).toThrow('roles');
    expect(() => validateSourceGrants([], 0)).toThrow('SELECT-only');
  });

  test('validates created containers before Docker has assigned a network ID', () => {
    const c = runtime()[0], owned = { id: c.Id, image: c.Image }, run = 'e2e-h5-offline';
    c.Name = '/' + run + '-source';
    c.Config.Labels['com.futureagi.e2e.h5'] = run;
    c.State.Running = false;
    c.NetworkSettings.Networks[H5_PIN.network].NetworkID = '';
    c.Mounts = [{ Type: 'volume', Name: run + '-source', Source: '/irrelevant', RW: true,
      Destination: '/var/lib/fi-collector' }];
    expect(() => validateOwnedContainer(c, 'source', run, owned, offlinePins)).not.toThrow();
    c.State.Running = true;
    expect(() => validateOwnedContainer(c, 'source', run, owned, offlinePins)).toThrow();
    c.NetworkSettings.Networks[H5_PIN.network].NetworkID = offlinePins.networkId;
    expect(() => validateOwnedContainer(c, 'source', run, owned, offlinePins)).not.toThrow();
    c.Mounts[0].Name = 'futureagi-e2e_fi-collector-data';
    expect(() => validateOwnedContainer(c, 'source', run, owned, offlinePins)).toThrow('mount changed');
  });

  test('bounds scope, interval and progress and rejects foreign destination checkpoints', () => {
    const s = selection(), p = checkpoint(s);
    expect(() => validateSelection(s)).not.toThrow();
    expect(() => validateProgress(p, s)).not.toThrow();
    for (const change of [{ project: 'bad;id' }, { since: s.until }, { until: new Date(Date.parse(s.until) + 1000).toISOString() },
      { organization: '00000000-0000-0000-0000-000000000000' }])
      expect(() => validateSelection({ ...s, ...change })).toThrow();
    for (const change of [{ binding: 'wrong' }, { published_pages: 9 }, { source_rows: 15 },
      { scan_complete: true }, { hour: s.until }, { after: {} }])
      expect(() => validateProgress({ ...p, ...change } as Progress, s)).toThrow();
    expect(() => validateProgress({ ...p, published_pages: 1 }, s, p)).toThrow('regressed');
    expect(selectionBinding({ ...s, project: s.workspace })).not.toBe(p.binding);
  });

  test('only accepts the exact bounded-stop receipt, not timeout, missing pages or generic exit one', () => {
    const p = checkpoint(), page = { preview: false, source_rows: 2, keys: 2, values: 2,
      scan_complete: false, pages_read: 1, consumer_visibility_verified: false };
    const result = { code: 1, uncertain: false,
      stdout: JSON.stringify(page) + '\n' + JSON.stringify({ ...page, pages_read: 2 }),
      stderr: 'observed catalog backfill: page budget reached; scan incomplete, resume the same checkpoint\n' };
    expect(validateCLIResult(result, false, p)).toHaveLength(2);
    for (const change of [{ uncertain: true }, { stderr: 'network failure' }, { stdout: '' }, { code: 137 }])
      expect(() => validateCLIResult({ ...result, ...change }, false, p)).toThrow();
    expect(() => validateCLIResult(result, true)).toThrow();
    expect(() => validateCLIResult(result, false, { ...p, source_rows: 3 })).toThrow('disagreement');
    const completed = { ...p, scan_complete: true };
    expect(validateCLIResult({ code: 0, uncertain: false, stdout: '', stderr: '' }, false, completed, completed)).toEqual([]);
    expect(() => validateCLIResult(result, false, completed, completed)).toThrow('completed resume');
  });

  test('accepts uncommitted partitions but waits for the barrier and rejects malformed offsets', () => {
    const row = (p: number, committed: string, end: number) =>
      H5_PIN.group + ' ' + H5_PIN.topic + ' ' + p + ' ' + committed + ' ' + end + ' - client host client';
    expect(parseOffsets(row(0, '-', 0) + '\n' + row(1, '12', 12), 2)).toEqual([
      { partition: 0, committed: null, end: 0 }, { partition: 1, committed: 12, end: 12 } ]);
    const unconsumed = parseOffsets(row(0, '-', 1), 1);
    expect(unconsumed).toEqual([{ partition: 0, committed: null, end: 1 }]);
    expect(reachedOffsetBarrier(unconsumed, unconsumed)).toBe(false);
    expect(reachedOffsetBarrier(unconsumed, parseOffsets(row(0, '0', 1), 1))).toBe(false);
    expect(reachedOffsetBarrier(unconsumed, parseOffsets(row(0, '1', 1), 1))).toBe(true);
    const empty = parseOffsets(row(0, '-', 0), 1);
    expect(reachedOffsetBarrier(empty, empty)).toBe(true);
    expect(() => parseOffsets(row(0, '12', 12), 2)).toThrow('offset');
    expect(() => parseOffsets(row(0, '13', 12), 1)).toThrow('offset');
    expect(() => parseOffsets(row(0, '-', -1), 1)).toThrow('offset');
    expect(() => parseOffsets(row(0, '1.5', 2), 1)).toThrow('offset');
    expect(() => parseOffsets(row(0, '1', 1) + '\n' + row(0, '1', 1), 2)).toThrow('offset');
  });
});

interface SourceRow {
  id: string; trace_id: string; org_id: string; project_id: string; name: string; start_us: string;
  attrs_string: Record<string, string>; attrs_number: Record<string, number>; attrs_bool: Record<string, number>;
  extra: string; version: string;
}
type Fixture = { label: string; cfg: SendTraceConfig; seeded: SeededTrace };
type Tuple = { attribute_key: string; attribute_type: string; value_json?: string;
  value_fingerprint?: string; first_us: string; last_us: string };
const sorted = <T>(rows: T[]) => [...rows].sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));

// Independent small-fixture oracle; no Go/Python extractor or catalog API output
// is used to derive expected observations. Codec domain: attributecatalog/codec.go.
function expectedTuples(fixtures: Fixture[], values: boolean): Tuple[] {
  const tuples = new Map<string, Tuple>();
  for (const { cfg } of fixtures) for (const [key, value] of Object.entries(cfg.rootAttributes!)) {
    const kind = Array.isArray(value) ? 'array' : typeof value === 'object' ? 'map' :
      typeof value === 'boolean' ? 'boolean' : typeof value === 'number' ? 'number' : 'string';
    const scalars = values ? (kind === 'map' ? [] : Array.isArray(value) ? value : [value]) : [undefined];
    for (const scalar of scalars) {
      const t: Omit<Tuple, 'first_us' | 'last_us'> = { attribute_key: key, attribute_type: kind };
      if (values) {
        t.value_json = JSON.stringify(scalar);
        t.value_fingerprint = createHash('sha256').update('futureagi.span-attribute-catalog.scalar.v1\0'
          + typeof scalar + '\0' + t.value_json).digest('hex');
      }
      const identity = JSON.stringify(t), seen = String(BigInt(cfg.startTimeUnixNano!) / 1000n);
      const old = tuples.get(identity);
      tuples.set(identity, { ...t, first_us: old && BigInt(old.first_us) < BigInt(seen) ? old.first_us : seen,
        last_us: old && BigInt(old.last_us) > BigInt(seen) ? old.last_us : seen });
    }
  }
  return sorted([...tuples.values()]);
}

live.describe('H5 bounded local backfill', { tag: '@h5-local' }, () => {
  live.skip(process.env.E2E_H5_LIVE !== '1', 'NOT RUN: main must coordinate local resources before enabling H5');
  live('historical OTLP facts preview, publish, resume and converge with live data', async ({ actor, probe, request, lifecycle: h5 }, testInfo) => {
    // Readiness 30 + source 15 + preview 60 + repair 180 + late source 15
    // + overlap 180 + final API 60 + stop/receipt headroom 60.
    live.setTimeout(600_000);
    const start = BigInt(historicalStart()) * 1_000_000n;
    const prefix = h5.runId, customer = prefix + '.customer';
    const s: Selection = { organization: actor.organizationId, workspace: actor.workspaceId, project: '',
      since: new Date(Number(start / 1_000_000n)).toISOString(),
      until: new Date(Number(start / 1_000_000n) + 3000).toISOString() };
    const fixtures: Fixture[] = [];
    let outcome = 'running', failure: string | undefined;
    const attach = async (name: string, body: unknown) => {
      const path = testInfo.outputPath(name + '.json');
      await mkdir(dirname(path), { recursive: true });
      await writeFile(path, JSON.stringify(body, null, 2), { mode: 0o600, flag: 'wx' });
      await testInfo.attach(name, { contentType: 'application/json', path });
    };
    const wrong = process.env.E2E_H5_EXPECT_WRONG;
    expect([undefined, 'source-id', 'preview-count', 'checkpoint', 'index-value', 'api-type', 'late-time']).toContain(wrong);
    const readiness = async (health: string) => expect.poll(async () => {
      try {
        const r = await request.get(health, { timeout: 2000, maxRedirects: 0 });
        const body = await r.json();
        return r.status() === 200 && body.status === 'ok' && typeof body.stats === 'object';
      } catch { return false; }
    }, { timeout: 30_000, intervals: [250, 500, 1000] }).toBe(true);
    const seed = async (url: string, label: string, at: bigint, value: string | number) => {
      await h5.recheck();
      // Read-only identity check before credentials or OTLP are sent. The live
      // auth middleware wraps the route (pkg/auth/middleware.go), so its exact
      // missing-credential 401 precedes the underlying GET method check.
      await expect.poll(async () => {
        try {
          const r = await request.get(url + '/v1/traces', { timeout: 2000, maxRedirects: 0 });
          return { status: r.status(), body: (await r.text()).trim() };
        } catch { return { status: 0, body: '' }; }
      }, { timeout: 15_000, intervals: [250, 500, 1000] })
        .toEqual({ status: 401,
          body: '{"error":"missing credentials: provide X-Api-Key/X-Secret-Key or Authorization: Basic"}' });
      const cfg: SendTraceConfig = { collectorUrl: url, apiKey: actor.apiKey, secretKey: actor.secretKey,
        projectName: prefix, rootName: prefix + '.' + label, startTimeUnixNano: at, endTimeUnixNano: at + 50_000_000n,
        rootAttributes: { [customer]: value, [prefix + '.zero']: 0, [prefix + '.enabled']: false,
          [prefix + '.array']: ['001', 0, false], [prefix + '.object']: { nested: 'key-only' },
          [prefix + '.雪/"\\key']: '雪' } };
      const fixture = { label, cfg, seeded: await sendTrace(request, cfg) };
      fixtures.push(fixture);
      return fixture;
    };
    const source = () => h5.sourceSelect<SourceRow>(
      'SELECT id, trace_id, org_id, project_id, name, toString(toUnixTimestamp64Micro(start_time)) AS start_us,'
      + ' attrs_string, attrs_number, attrs_bool, toString(attributes_extra) AS extra, toString(_version) AS version'
      + ' FROM spans FINAL WHERE service_name={n:String} ORDER BY id', { n: prefix });
    const sourceReady = async () => {
      await expect.poll(async () => (await source()).map(r => r.id).sort(), POLL.SPAN_VISIBLE)
        .toEqual(fixtures.flatMap(f => f.seeded.spanIds).sort());
      const rows = await source();
      for (const f of fixtures) {
        const root = rows.find(r => r.id === f.seeded.spanIds[0])!;
        expect([root.trace_id, root.org_id, root.start_us]).toEqual([
          wrong === 'source-id' ? 'deliberately-wrong-source-id' : f.seeded.traceId,
          actor.organizationId, String(BigInt(f.cfg.startTimeUnixNano!) / 1000n)]);
        expect(root.name).toBe(f.cfg.rootName);
        for (const [key, value] of Object.entries(f.cfg.rootAttributes!)) {
          if (typeof value === 'string') expect(root.attrs_string[key]).toBe(value);
          else if (typeof value === 'number') expect(root.attrs_number[key]).toBe(value);
          else if (typeof value === 'boolean') expect(root.attrs_bool[key]).toBe(value ? 1 : 0);
          else expect(JSON.parse(root.extra)[key]).toEqual(value);
        }
      }
      return rows;
    };
    // AggregatingMergeTree requires grouped min/max, not FINAL or physical counts.
    const index = (values: boolean) => probe.ch<Tuple>('SELECT attribute_key, attribute_type,'
      + (values ? ' value_json, value_fingerprint,' : '')
      + ' toString(toUnixTimestamp64Micro(min(first_seen))) AS first_us,'
      + ' toString(toUnixTimestamp64Micro(max(last_seen))) AS last_us FROM property_catalog.'
      + (values ? 'observed_attribute_values' : 'observed_attribute_keys')
      + ' WHERE organization_id={o:String} AND workspace_id={w:String} AND project_id={p:String}'
      + ' AND source_kind=\'custom_attribute\' AND startsWith(attribute_key, {n:String})'
      + ' GROUP BY attribute_key, attribute_type' + (values ? ', value_json, value_fingerprint' : '')
      + ' SETTINGS readonly=1, max_execution_time=10, max_threads=1',
    { o: s.organization, w: s.workspace, p: s.project, n: prefix }).then(sorted);
    const values = async () => (await actor.api.post<{ result: { values: { value: unknown; type: string }[] } }>(
      '/tracer/dashboard/filter_values/', { property_id: 'custom_attribute:' + customer,
        source: 'traces', project_ids: s.project, page_size: 25 })).result.values;
    const drain = async () => {
      const barrier = await h5.offsets();
      await expect.poll(async () => {
        const latest = await h5.offsets();
        return reachedOffsetBarrier(barrier, latest);
      }, { timeout: 60_000, intervals: [1000, 2000, 5000] }).toBe(true);
      return barrier;
    };
    const verify = async (eligible: Fixture[], late = false) => {
      for (const valueRows of [false, true]) {
        const expected = expectedTuples(eligible, valueRows);
        if (wrong === 'index-value' && valueRows) expected[0].value_json = '"deliberately-wrong-value"';
        if (wrong === 'late-time' && late) expected[0].first_us = '1';
        await expect.poll(() => index(valueRows), { timeout: 60_000, intervals: [500, 1000, 2000] }).toEqual(expected);
      }
      const want = sorted([...new Map(eligible.map(f => {
        const value = f.cfg.rootAttributes![customer];
        return [JSON.stringify(value), { value, type: wrong === 'api-type' ? 'deliberately-wrong-type' : typeof value }];
      })).values()]);
      await expect.poll(() => values().then(v => sorted(v.map(({ value, type }) => ({ value, type })))),
        { timeout: 60_000, intervals: [500, 1000, 2000] }).toEqual(want);
      await drain();
    };
    const finish = async (role: 'primary' | 'overlap') => {
      const deadline = Date.now() + 180_000;
      for (let i = 0; i < 4; i++) {
        expect(Date.now()).toBeLessThan(deadline);
        const result = await h5.backfill(role);
        if (result.progress!.scan_complete) return result.progress!;
      }
      throw new Error('H5 incomplete after four bounded invocations; receipts retained');
    };
    try {
      const old = await h5.startSource();
      await readiness(old.healthUrl);
      for (const [label, delta, value] of [
        ['before', -1000n, 'excluded-before'], ['since', 0n, '001'], ['middle', 1_000_000_000n, 1],
        ['last', 2_999_999_000n, '002'], ['until', 3_000_000_000n, 'excluded-until'],
      ] as const) await seed(old.collectorUrl, label, start + delta, value);
      const baseline = await sourceReady();
      const projects = await probe.pg<{ id: string }>(
        'SELECT id FROM tracer_project WHERE name=$1 AND organization_id=$2 AND workspace_id=$3',
        [prefix, actor.organizationId, actor.workspaceId]);
      expect(projects).toHaveLength(1);
      s.project = projects[0].id;
      expect([...new Set(baseline.map(row => row.project_id))]).toEqual([s.project]);
      await h5.bind(s);
      await h5.stop('source');
      await drain();
      expect(await index(false)).toEqual([]);
      expect(await index(true)).toEqual([]);
      expect(await values()).toEqual([]);

      const preview = await h5.backfill('preview');
      expect(preview.pages.reduce((sum, p) => sum + p.source_rows, 0)).toBe(wrong === 'preview-count' ? 9 : 10);
      expect(preview.pages.at(-1).scan_complete).toBe(true);
      await drain();
      expect(await source()).toEqual(baseline);
      expect(await index(false)).toEqual([]);
      expect(await index(true)).toEqual([]);
      expect(await values()).toEqual([]);

      const first = await h5.backfill('primary');
      expect(first.progress!.scan_complete).toBe(false);
      expect(first.progress!.published_pages).toBe(wrong === 'checkpoint' ? 1 : 2);
      // Start a bounded resumed CLI invocation and issue both live OTLP requests
      // while that invocation is in flight. Post-resume overlap reconciles races.
      const resumed = h5.backfill('primary');
      const seededLive = (async () => {
        await seed(E2E.collectorUrl, 'live-now', BigInt(Date.now()) * 1_000_000n, '003');
        await seed(E2E.collectorUrl, 'live-overlap', start + 1_500_000_000n, '001');
      })();
      const concurrent = await Promise.allSettled([resumed, seededLive]);
      for (const result of concurrent) if (result.status === 'rejected') throw result.reason;
      await sourceReady();
      const primary = await finish('primary');
      const eligible = fixtures.filter(f => !['before', 'until'].includes(f.label));
      await verify(eligible);
      expect((await h5.backfill('primary')).progress).toEqual(primary);

      const lateSource = await h5.startSource();
      await readiness(lateSource.healthUrl);
      const late = await seed(lateSource.collectorUrl, 'late', start + 500_000_000n, 'late-only');
      await sourceReady();
      await h5.stop('source');
      expect((await values()).some(v => v.value === 'late-only')).toBe(false);
      const beforeRepair = await source();
      await finish('overlap');
      await verify([...eligible, late], true);
      expect(await source()).toEqual(beforeRepair);
      // OBS010 receives real source IDs and source-config references; no API rows are fabricated.
      await attach('h5-fixture-reference', { runId: prefix, selection: s, fixtures: fixtures.map(f => ({
          label: f.label, seeded: f.seeded, rootAttributes: f.cfg.rootAttributes,
          startTimeUnixNano: String(f.cfg.startTimeUnixNano), endTimeUnixNano: String(f.cfg.endTimeUnixNano),
        })), expectedKeys: expectedTuples([...eligible, late], false),
        expectedValues: expectedTuples([...eligible, late], true), sourceRows: await source(),
        catalogKeys: await index(false), catalogValues: await index(true), apiValues: await values() });
      outcome = 'passed';
    } catch (error) {
      outcome = 'failed';
      failure = String(error);
      throw error;
    } finally {
      try { await h5.retainAndStop(); }
      finally { await attach('h5-retained-receipts', { outcome, failure,
        wrongExpectedAnchor: wrong, receipts: h5.receipts,
        selection: s, seeded: fixtures.map(f => ({ label: f.label, ...f.seeded })) }); }
    }
  });
});
