// Live lab for lib/loopback-forwarder.ts on a real Docker daemon and the real procfs
// (Linux, E2E_FORWARDER_LAB=1; skipped everywhere else). The verifier's finding on
// E2E-MANAGED-MOCK-ATTEST-1 r1: the declaration and socket checks prove where a
// forwarder SAYS it pipes, not where it does, so four unsafe forwarders were
// accepted while answering from a non-mock. Here each forwarded service kind runs in
// its own container on an internal, compose-labelled bridge, beside non-mock
// lookalikes on the same network, and a forwarder in this worker declares the real
// container honestly while behaving:
//   honest     pipes every connection to the declared container (a runner's ports.mjs shape);
//   chain      pipes to a second loopback proxy that pipes to a non-mock;
//   mixed      alternates the declared container and a non-mock, connection by connection;
//   lookalike  pipes to a non-mock container carrying the service's alias;
//   local      answers every connection itself, no upstream at all;
//   split      honest on 127.0.0.1, a non-mock on ::1;
// and, from the verifier's finding on r2 (each delivers every request, probes
// included, to the declared container, so every arrival is real):
//   replace    reads and drops the container's answer and sends fixed bytes instead
//              (replace-502: a fixed 502);
//   replay     relays honestly while recording the last answer, then (once primed by
//              an accepted attestation) sends that stale answer to every connection.
// The attestation runs in a child process (forwarder-lab-child.mjs), as a test command
// under a runner's forwarder does. Only honest may be accepted; every other mode
// must STOP, and the lab independently observes that it really answers from a non-mock
// or answers other than the container does. The one exception is the frontend (product
// nginx), whose probes confirm arrival only: a replacing forwarder is accepted there and
// the receipt must say so (kind arrival-only, with the reason).
import { test, expect } from '@playwright/test';
import { execFileSync, spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';
import http from 'node:http';
import { createConnection, createServer, type AddressInfo, type Server, type Socket } from 'node:net';
import { fileURLToPath } from 'node:url';
import pg from 'pg';

const root = fileURLToPath(new URL('../../', import.meta.url));
const enabled = process.env.E2E_FORWARDER_LAB === '1' && process.platform === 'linux';
const context = process.env.DOCKER_CONTEXT || 'default';
const docker = (...args: string[]) => execFileSync('docker', ['--context', context, ...args],
  { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 900_000 }).trim();
const project = `fwdlab-${process.pid}`;
const network = `${project}_default`;
const NON_MOCK = 'NON_MOCK_UPSTREAM';
const PG = { user: 'futureagi', password: 'futureagi', database: 'futureagi' };
const CH = { user: 'default', password: 'e2e-clickhouse-password' };
const repo = (path: string) => `${root}${path}`;

type Kind = { port: number; image: string; command?: string[]; env?: Record<string, string>; mounts?: [string, string][];
  extra?: string[]; bad: string };
/** The forwardable services (lib/loopback-forwarder.ts PROBE_KINDS), each with its non-mock lookalike. */
const KINDS: Record<string, Kind> = {
  'mock-llm': { port: 8080, image: 'node:22-alpine', command: ['node', '/srv/server.mjs'], bad: 'not-a-mock',
    mounts: [[repo('e2e/stack/mock-llm/server.mjs'), '/srv/server.mjs'], [repo('e2e/stack/mock-llm/wire.mjs'), '/srv/wire.mjs']] },
  'webhook-sink': { port: 8080, image: 'node:22-alpine', command: ['node', '/srv/server.mjs'], bad: 'not-a-mock',
    mounts: [[repo('e2e/stack/webhook-sink/server.mjs'), '/srv/server.mjs']] },
  'mock-slack': { port: 8080, image: 'node:22-alpine', command: ['node', '/srv/server.mjs'], bad: 'not-a-mock',
    mounts: [[repo('e2e/stack/mock-slack/server.mjs'), '/srv/server.mjs']] },
  frontend: { port: 80,
    // Collection ships e2e/ only. Read the real pin when enabled setup starts this service.
    get image() { return /^ARG NGINX_IMAGE=(\S+)$/m.exec(readFileSync(`${root}frontend/Dockerfile`, 'utf8'))![1]; },
    bad: 'not-a-mock', mounts: [[repo('frontend/nginx.conf'), '/etc/nginx/conf.d/default.conf'],
    [repo('frontend/security-headers.conf'), '/etc/nginx/security-headers.conf']] },
  postgres: { port: 5432, image: 'postgres:16', bad: 'not-postgres',
    env: { POSTGRES_USER: PG.user, POSTGRES_PASSWORD: PG.password, POSTGRES_DB: PG.database } },
  // As the Standalone install runs it: no system log tables (query_log removed, log_queries 0).
  clickhouse: { port: 8123, image: 'clickhouse/clickhouse-server:25.3-alpine', bad: 'not-a-mock',
    env: { CLICKHOUSE_SKIP_USER_SETUP: '1', CLICKHOUSE_PASSWORD: CH.password, CLICKHOUSE_DB: 'default' },
    mounts: [[repo('deploy/clickhouse/users.d/zz-default-password.xml'), '/etc/clickhouse-server/users.d/zz-default-password.xml'],
      [repo('deploy/standalone/clickhouse/config.d/zz-small-host.xml'), '/etc/clickhouse-server/config.d/zz-small-host.xml'],
      [repo('deploy/standalone/clickhouse/users.d/zz-small-host.xml'), '/etc/clickhouse-server/users.d/zz-small-host.xml']],
    extra: ['--ulimit', 'nofile=262144:262144'] },
};
const NOT_A_MOCK = `const http = require('http');
for (const port of [80, 8080, 8123]) http.createServer((q, s) => { s.setHeader('content-type', 'application/json');
  s.end(JSON.stringify({ sentinel: '${NON_MOCK}', host: 'not-a-mock' })); }).listen(port, '0.0.0.0');`;
const LOOKALIKES: Record<string, Kind & { aliases: string[] }> = {
  'not-a-mock': { port: 8080, image: 'node:22-alpine', command: ['node', '-e', NOT_A_MOCK], bad: '',
    aliases: ['mock-llm', 'webhook-sink', 'mock-slack', 'frontend', 'clickhouse'] },
  'not-postgres': { ...KINDS.postgres, bad: '', aliases: ['postgres'] },
};

type Lab = { id: Record<string, string>; ip: Record<string, string> };
let lab: Lab | undefined;
const started: string[] = [];
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

function run(service: string, kind: Kind, aliases: string[] = [service]): void {
  const name = `${project}-${service}-1`;
  docker('run', '-d', '--name', name, '--network', network, ...aliases.flatMap(a => ['--network-alias', a]),
    '--label', `com.docker.compose.project=${project}`, '--label', `com.docker.compose.service=${service}`,
    ...Object.entries(kind.env ?? {}).flatMap(([k, v]) => ['-e', `${k}=${v}`]),
    ...(kind.mounts ?? []).flatMap(([source, target]) => ['-v', `${source}:${target}:ro`]), ...(kind.extra ?? []),
    kind.image, ...(kind.command ?? []));
  started.push(name);
}

function httpExchange(host: string, port: number, path = '/'): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const req = http.get({ host, port, path, agent: false, timeout: 5_000, headers: { connection: 'close' } }, res => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', chunk => { body += chunk; });
      res.on('end', () => resolve({ status: res.statusCode ?? 0, body }));
    });
    req.on('timeout', () => req.destroy(new Error('timed out')));
    req.on('error', reject);
  });
}
const httpGet = async (host: string, port: number, path = '/') => (await httpExchange(host, port, path)).body;
async function pgServerAddress(host: string, port: number): Promise<string> {
  const client = new pg.Client({ host, port, ...PG, connectionTimeoutMillis: 5_000 });
  client.on('error', () => undefined);
  try {
    await client.connect();
    return (await client.query<{ ip: string }>('SELECT host(inet_server_addr()) AS ip')).rows[0].ip;
  } finally { await client.end().catch(() => undefined); }
}
async function ready(service: string, ip: string, port: number): Promise<void> {
  for (let i = 0; i < 240; i++) {
    try {
      if (port === 5432) await pgServerAddress(ip, port);
      else await httpGet(ip, port, port === 8123 ? '/ping' : '/');
      return;
    } catch { await sleep(500); }
  }
  throw new Error(`lab ${service} never became ready`);
}

// Run with --workers=1. Not serial: a failing case must not skip the rest (a failure
// restarts the worker, whose hooks remove this lab and build a fresh one).
test.beforeAll(async ({}, testInfo) => {
  if (!enabled) return;
  testInfo.setTimeout(900_000);
  docker('network', 'create', '--internal', '--driver', 'bridge', '--label', `com.docker.compose.project=${project}`,
    '--label', 'com.docker.compose.network=default', network);
  for (const [service, kind] of Object.entries(KINDS)) run(service, kind);
  for (const [service, kind] of Object.entries(LOOKALIKES)) run(service, kind, kind.aliases);
  const id: Record<string, string> = {}, ip: Record<string, string> = {};
  for (const service of [...Object.keys(KINDS), ...Object.keys(LOOKALIKES)]) {
    const [c] = JSON.parse(docker('inspect', `${project}-${service}-1`));
    id[service] = c.Id;
    ip[service] = c.NetworkSettings.Networks[network].IPAddress;
  }
  for (const service of Object.keys(KINDS)) await ready(service, ip[service], KINDS[service].port);
  await ready('not-a-mock', ip['not-a-mock'], 8080);
  await ready('not-postgres', ip['not-postgres'], 5432);
  lab = { id, ip };
});
test.afterAll(() => {
  for (const name of started.reverse()) { try { docker('rm', '-f', name); } catch { /* already gone */ } }
  if (enabled) { try { docker('network', 'rm', network); } catch { /* never created */ } }
});

type Mode = 'honest' | 'chain' | 'mixed' | 'lookalike' | 'local' | 'split' | 'replace' | 'replace-502' | 'replay';
type Forwarder = { port: number; v6: boolean; connections(): number; close(): void;
  /** replay mode: stop relaying answers and send the last recorded one to every connection. */
  replay(): void };
/** A forwarder in this worker (an ancestor of the attesting child) on 127.0.0.1 and, where the VM has it, ::1. */
async function forwarder(mode: Mode, good: [string, number], bad: [string, number]): Promise<Forwarder> {
  const servers: Server[] = [], sockets = new Set<Socket>();
  const track = (socket: Socket) => { sockets.add(socket); socket.once('close', () => sockets.delete(socket)); socket.on('error', () => socket.destroy()); };
  const pipe = (client: Socket, [host, port]: [string, number]) => {
    const upstream = createConnection({ host, port });
    track(upstream);
    upstream.once('close', () => client.destroy());
    client.once('close', () => upstream.destroy());
    client.pipe(upstream).pipe(client);
    return upstream;
  };
  // Every request byte reaches the upstream; its answer is read and dropped, and the
  // client gets `answer()` instead once the upstream has finished answering.
  const substitute = (client: Socket, [host, port]: [string, number], answer: () => string | Buffer) => {
    const upstream = createConnection({ host, port });
    track(upstream);
    upstream.once('error', () => client.destroy());
    client.once('close', () => upstream.destroy());
    client.pipe(upstream);
    upstream.resume();
    upstream.once('end', () => client.end(answer()));
  };
  const fixed = (status: string) =>
    `HTTP/1.1 ${status}\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: ${NON_MOCK.length + 2}\r\n\r\n"${NON_MOCK}"`;
  let recorded: Buffer | undefined, replaying = false;
  const listen = (host: string, port: number, handler: (client: Socket) => void) => new Promise<number>((resolve, reject) => {
    const server = createServer(client => { track(client); handler(client); });
    servers.push(server);
    server.once('error', reject);
    server.listen({ host, port }, () => resolve((server.address() as AddressInfo).port));
  });
  let chain = 0;
  if (mode === 'chain') chain = await listen('127.0.0.1', 0, client => pipe(client, bad));
  let connections = 0;
  const handler = (family: 4 | 6) => (client: Socket) => {
    connections++;
    if (mode === 'honest' || (mode === 'split' && family === 4)) pipe(client, good);
    else if (mode === 'chain') pipe(client, ['127.0.0.1', chain]);
    else if (mode === 'mixed') pipe(client, connections % 2 ? good : bad);
    else if (mode === 'local') client.once('data', () => client.end('HTTP/1.1 200 OK\r\nConnection: close\r\n' +
      `Content-Length: ${NON_MOCK.length + 2}\r\n\r\n"${NON_MOCK}"`));
    else if (mode === 'replace') substitute(client, good, () => fixed('200 OK'));
    else if (mode === 'replace-502') substitute(client, good, () => fixed('502 Bad Gateway'));
    else if (mode === 'replay' && replaying) substitute(client, good, () => recorded!);
    else if (mode === 'replay') { // priming: relay honestly, keeping the last whole answer
      const chunks: Buffer[] = [];
      const upstream = pipe(client, good);
      upstream.on('data', (chunk: Buffer) => { chunks.push(chunk); });
      upstream.once('end', () => { recorded = Buffer.concat(chunks); });
    }
    else pipe(client, bad); // lookalike, and split on ::1
  };
  const port = await listen('127.0.0.1', 0, handler(4));
  const v6 = await listen('::1', port, handler(6)).then(() => true, () => false);
  return { port, v6, connections: () => connections,
    close: () => { for (const s of sockets) s.destroy(); for (const s of servers) s.close(); },
    replay: () => { if (!recorded?.length) throw new Error('lab replay forwarder recorded no answer'); replaying = true; } };
}

type Verdict = { attestation: 'ACCEPT' | 'REFUSE'; reason?: string;
  receipt?: { service: string; hostPort: number; probes?: number; delivered?: number; kind?: string; reason?: string }[] };
/** The attestation, in a child of this worker, with the declaration a runner exports. */
function attest(service: string, port: number): Promise<Verdict> {
  const kind = KINDS[service];
  const declaration = { pid: process.pid, network, forwards: [{ service, listen: port, ip: lab!.ip[service], port: kind.port }] };
  return new Promise((resolve, reject) => {
    const env: NodeJS.ProcessEnv = { ...process.env, E2E_LOOPBACK_FORWARDS: JSON.stringify(declaration), DOCKER_CONTEXT: context };
    delete env.NODE_OPTIONS;
    const child = spawn(process.execPath, ['--experimental-strip-types', '--no-warnings', fileURLToPath(new URL('./forwarder-lab-child.mjs', import.meta.url))],
      { env, stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    child.stdout.on('data', d => { stdout += d; });
    child.stderr.on('data', d => { stderr += d; });
    child.on('error', reject);
    child.on('close', code => {
      if (code !== 0) return reject(new Error(`lab attestation child exited ${code}: ${stderr.slice(0, 2000)}`));
      resolve(JSON.parse(stdout.trim().split('\n').pop()!));
    });
    child.stdin.end(JSON.stringify({ root, context, project, network, service, containerId: lab!.id[service],
      containerPort: `${kind.port}/tcp`, hostPort: port, credentials: { pg: PG, ch: CH } }));
  });
}

/** What two plain clients see through the forwarder: did either reach a non-mock (or nothing at all)? */
async function reachesNonMock(service: string, host: string, port: number): Promise<boolean> {
  let nonMock = false;
  for (let i = 0; i < 2; i++) {
    try {
      if (service === 'postgres') nonMock ||= (await pgServerAddress(host, port)) !== lab!.ip.postgres;
      else nonMock ||= (await httpGet(host, port, service === 'clickhouse' ? '/ping' : '/')).includes(NON_MOCK);
    } catch { nonMock = true; } // nothing a mock would answer
  }
  return nonMock;
}

/** The r2 modes' control: an ordinary request (no probe route) answered through the
 * forwarder differs from the container's own answer to it (status or body). */
const ORDINARY = '/e2e-forwarder-lab-ordinary';
const HTTP_KINDS = ['mock-llm', 'webhook-sink', 'mock-slack', 'frontend'];
async function answersDifferently(service: string, port: number): Promise<boolean> {
  const direct = await httpExchange(lab!.ip[service], KINDS[service].port, ORDINARY);
  const through = await httpExchange('127.0.0.1', port, ORDINARY).catch(() => undefined);
  return !through || through.status !== direct.status || through.body !== direct.body;
}
/** The harness's own mocks (e2e/stack), whose probe answers are bound to a key planted in the container. */
const HARNESS_MOCKS = ['mock-llm', 'webhook-sink', 'mock-slack'];
/** Probe keys left inside a container (the probe removes its key after reading the arrivals). */
const keysLeft = (service: string) =>
  docker('exec', lab!.id[service], 'sh', '-c', 'ls -A /tmp/fwdprobe-keys 2>/dev/null | wc -l');

const stopFor = (service: string) =>
  `STOP: managed mock loopback forwarder for ${service} did not deliver every probe to the managed ${service} container`;

for (const service of Object.keys(KINDS)) {
  test(`loopback forwarder lab: an honest runner-shaped forwarder for ${service} is accepted`, async () => {
    test.skip(!enabled, 'needs Linux, Docker and E2E_FORWARDER_LAB=1');
    test.setTimeout(240_000);
    const kind = KINDS[service];
    const f = await forwarder('honest', [lab!.ip[service], kind.port], [lab!.ip[kind.bad], kind.port]);
    try {
      const verdict = await attest(service, f.port);
      expect(verdict.reason).toBeUndefined();
      expect(verdict.attestation).toBe('ACCEPT');
      expect(await reachesNonMock(service, '127.0.0.1', f.port)).toBe(false);
      // The r2 modes' control holds no false positive: honest answers are the container's own.
      if (HTTP_KINDS.includes(service)) expect(await answersDifferently(service, f.port)).toBe(false);
      if (HARNESS_MOCKS.includes(service)) expect(keysLeft(service)).toBe('0');
    } finally { f.close(); }
  });

  for (const mode of ['chain', 'mixed', 'lookalike', 'local'] as const) {
    test(`loopback forwarder lab: a ${mode} forwarder for ${service} that declares the real container is refused`, async () => {
      test.skip(!enabled, 'needs Linux, Docker and E2E_FORWARDER_LAB=1');
      test.setTimeout(240_000);
      const kind = KINDS[service];
      const f = await forwarder(mode, [lab!.ip[service], kind.port], [lab!.ip[kind.bad], kind.port]);
      try {
        const verdict = await attest(service, f.port);
        expect(verdict.attestation).toBe('REFUSE');
        expect(verdict.reason).toBe(stopFor(service));
        // The control is genuinely unsafe: plain clients through it reach a non-mock.
        expect(await reachesNonMock(service, '127.0.0.1', f.port)).toBe(true);
      } finally { f.close(); }
    });
  }
}

for (const service of ['mock-llm', 'postgres']) {
  test(`loopback forwarder lab: a ${service} forwarder honest on 127.0.0.1 but not on ::1 is refused`, async () => {
    test.skip(!enabled, 'needs Linux, Docker and E2E_FORWARDER_LAB=1');
    test.setTimeout(240_000);
    const kind = KINDS[service];
    const f = await forwarder('split', [lab!.ip[service], kind.port], [lab!.ip[kind.bad], kind.port]);
    try {
      test.skip(!f.v6, 'this VM has no IPv6 loopback');
      const verdict = await attest(service, f.port);
      expect(verdict.attestation).toBe('REFUSE');
      expect(verdict.reason).toBe(stopFor(service));
      expect(await reachesNonMock(service, '127.0.0.1', f.port)).toBe(false);
      expect(await reachesNonMock(service, '::1', f.port)).toBe(true);
    } finally { f.close(); }
  });
}

// E2E-MANAGED-MOCK-ATTEST-1 r3 (the verifier's finding on r2): forwarders that deliver
// every request, probes included, to the declared container but replace or replay what
// comes back. Arrival alone accepted them. The harness mocks' probes must now be answered
// with HMAC-SHA256(key, nonce), the key planted inside that container for the inspection
// and never sent through the forwarder, so a replaced or replayed answer misses.
for (const service of HARNESS_MOCKS) {
  for (const mode of ['replace', 'replay'] as const) {
    test(`loopback forwarder lab: a ${mode} forwarder for ${service} that delivers every request is refused`, async () => {
      test.skip(!enabled, 'needs Linux, Docker and E2E_FORWARDER_LAB=1');
      test.setTimeout(240_000);
      const kind = KINDS[service];
      const f = await forwarder(mode, [lab!.ip[service], kind.port], [lab!.ip[kind.bad], kind.port]);
      try {
        if (mode === 'replay') {
          // Primed by an accepted attestation: what it replays is a genuine earlier probe answer.
          expect((await attest(service, f.port)).attestation).toBe('ACCEPT');
          f.replay();
        }
        const before = f.connections();
        const verdict = await attest(service, f.port);
        expect(verdict.attestation).toBe('REFUSE');
        expect(verdict.reason).toBe(stopFor(service));
        // The probes were sent through it (the STOP is not a probe that never ran) ...
        expect(f.connections() - before).toBeGreaterThanOrEqual(8 * (f.v6 ? 2 : 1));
        expect(keysLeft(service)).toBe('0');
        // ... and the control is genuinely unsafe: it answers other than the container does.
        expect(await answersDifferently(service, f.port)).toBe(true);
      } finally { f.close(); }
    });
  }
}

test('loopback forwarder lab: the harness mocks answer no probe without a key planted for it', async () => {
  test.skip(!enabled, 'needs Linux, Docker and E2E_FORWARDER_LAB=1');
  for (const service of HARNESS_MOCKS) {
    const unplanted = `fwdprobe-${randomBytes(16).toString('hex')}`;
    const { status, body } = await httpExchange(lab!.ip[service], KINDS[service].port, `/__probe/${unplanted}/${unplanted}`);
    expect(status).not.toBe(200);
    expect(body).not.toContain('answer');
  }
});

// The frontend is the product's own nginx: no harness route can answer a probe with a
// nonce-bound value there without a product change, so its probes confirm arrival only
// (its access log). A forwarder that delivers each probe but replaces the answer is
// therefore accepted, and the receipt must say so: the documented limitation (P3).
for (const mode of ['replace', 'replace-502'] as const) {
  test(`loopback forwarder lab: frontend probes are arrival-only, so a ${mode} forwarder is accepted and recorded as arrival-only`, async () => {
    test.skip(!enabled, 'needs Linux, Docker and E2E_FORWARDER_LAB=1');
    test.setTimeout(240_000);
    const f = await forwarder(mode, [lab!.ip.frontend, 80], [lab!.ip['not-a-mock'], 80]);
    try {
      const verdict = await attest('frontend', f.port);
      expect(verdict.attestation).toBe('ACCEPT');
      expect(verdict.receipt?.[0]?.kind).toBe('arrival-only');
      expect(verdict.receipt?.[0]?.reason).toContain('product-owned nginx');
      expect(await answersDifferently('frontend', f.port)).toBe(true);
    } finally { f.close(); }
  });
}

test('loopback forwarder lab: an accepted forward records its probe and delivered counts, value-free', async () => {
  test.skip(!enabled, 'needs Linux, Docker and E2E_FORWARDER_LAB=1');
  test.setTimeout(240_000);
  const f = await forwarder('honest', [lab!.ip['mock-llm'], 8080], [lab!.ip['not-a-mock'], 8080]);
  try {
    const verdict = await attest('mock-llm', f.port);
    const probes = 8 * (f.v6 ? 2 : 1);
    expect(verdict.receipt).toEqual([{ service: 'mock-llm', hostPort: f.port, probes, delivered: probes, kind: 'answer-bound' }]);
    // Each probe was its own connection through the forwarder.
    expect(f.connections()).toBeGreaterThanOrEqual(probes);
  } finally { f.close(); }
});
