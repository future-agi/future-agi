// A loopback host forwarder: a runner (a browser runner, runner/ports.mjs)
// overrides the compose default network to `internal: true`, so no container on
// that network alone publishes a host port. Instead one host process binds each
// harness port on 127.0.0.1 and ::1 only, pipes it to a container's address on
// that network, and runs the test command as its child. lib/managed-mock.ts
// accepts a harness endpoint that Docker does not publish only through
// attestLoopbackForwards.
//
// CONTRACT for such a forwarder (e2e-owned; the runner implements it):
//   - it runs the test command as its descendant;
//   - it exports E2E_LOOPBACK_FORWARDS to that command, one JSON object:
//       {"pid": <its own pid>, "network": "<compose project>_default",
//        "forwards": [{"service": "frontend", "listen": 3100, "ip": "<the
//          container's IPv4 on that network>", "port": 80}, ...]}
//     one entry per host port it listens on: the compose service, the host
//     port, and exactly where it pipes it;
//   - it pipes every connection on a declared port to that declared target,
//     including the harness's own probe connections (below);
//   - only these attested endpoints may be forwarded, each because it sits on the
//     internal default network alone: frontend 80, postgres 5432, clickhouse 8123,
//     mock-slack 8080 (and mock-llm / webhook-sink 8080, which the stacks publish).
//     Every other one (backend, agentcc-gateway, Standalone's app) cannot be
//     probed and is a STOP; Docker publishes them through the e2e-public network.
// The declaration is a claim, never trusted alone. The inspection accepts it only
// with this evidence, read from Docker and procfs:
//   - the declared network is the project's default network and is internal;
//   - the declared pid is an ancestor of this process (it wraps the run);
//   - every listener on each declared port, in this network namespace
//     (/proc/net/tcp, /proc/net/tcp6), is on 127.0.0.1 or ::1 and belongs to
//     that pid (/proc/<pid>/fd);
//   - each forwarded endpoint's target is exactly the inspected service
//     container's address on the internal network and its container port, as
//     Docker reports it from the container and from the network side; every
//     other declared target is a running container of this project, of the
//     declared service, on that network (by Docker labels, never by name);
// and with the forwarder's EFFECTIVE behaviour, observed by active probes
// (lib/forward-probe.mjs). Through every listener of every forwarded endpoint
// the inspection sends PROBES_PER_LISTENER fresh connections, each carrying its
// own random nonce, and confirms from the inspected container's own side (docker
// exec / docker logs, never through the forwarder) that each one arrived there
// and, for every kind but the frontend, that its answer is one only that
// container could give (answer-bound):
//   - postgres: a session per probe named by its nonce (application_name); the
//     backend pid the forwarder answers must be the pid pg_stat_activity shows
//     for that name inside the container (arrival and answer bound);
//   - clickhouse: a secret per probe, written by docker exec into the
//     container's own user_files directory and never sent through the forwarder;
//     the probe asks for it by its nonce file name (file()) and the answer must be
//     exactly that secret (arrival and answer bound; no system log table needed,
//     which the Standalone install removes; the files are removed after);
//   - mock-llm, webhook-sink, mock-slack (the harness's own mocks, e2e/stack): a
//     fresh key per inspection, written by docker exec (over stdin) into the
//     container's /tmp/fwdprobe-keys and never sent through the forwarder; each
//     GET /__probe/<key id>/<nonce> must be answered with exactly
//     HMAC-SHA256(key, nonce), and the mock must have recorded the nonce exactly
//     once, read inside the container (arrival and answer bound; the key is
//     removed after);
//   - frontend: GET /__e2e-forward-probe/<nonce>, which must appear exactly once
//     in the container's own nginx access log (nginx writes it to stdout, so it
//     is read with docker logs, the daemon's capture of that stream) (ARRIVAL
//     ONLY: see the limitation below).
// Every probe must match: a forwarder that chains to, alternates with, or
// substitutes a non-mock upstream, answers itself, or delivers every probe but
// replaces or replays the answers misses at least one. The probes run on every
// inspection, so they interleave with the run's own traffic; they sample behaviour
// (a forwarder that singles out probe connections, or one that switches after the
// inspection, is outside what sampling can prove).
// KNOWN LIMITATION (frontend, for acceptance): the frontend is the
// product's own nginx, with no route that could answer a probe with a nonce-bound
// value without a product change, so its probes prove arrival only; a forwarder
// that delivers each frontend probe but replaces or replays the response is
// accepted. The receipt says so for that endpoint (kind arrival-only, with the
// reason, ARRIVAL_ONLY); every other forwarded endpoint is kind answer-bound. The
// receipt records each forwarded endpoint's probe count, delivered count and kind,
// never a nonce, key, pid or address.
// Anything missing, unreadable or different is a STOP; without a declaration
// the endpoint keeps the inspection's original "not published" STOP.
import { execFileSync } from 'node:child_process';
import { readdirSync, readFileSync, readlinkSync } from 'node:fs';
import { isIPv4 } from 'node:net';
import { endianness } from 'node:os';
import { fileURLToPath } from 'node:url';
// Type-only: this module stays loadable on its own (node --experimental-strip-types).
import type { Container } from './managed-mock';

export const LOOPBACK_FORWARDS_ENV = 'E2E_LOOPBACK_FORWARDS';

/** Raw procfs reads, relative to /proc. The inspection reads the real /proc;
 * offline tests pass recorded kernel text through the same parser. */
export interface ProcFs { self: number; read(path: string): string; list(path: string): string[]; link(path: string): string }
export const linuxProc = (): ProcFs => ({
  self: process.pid,
  read: path => readFileSync(`/proc/${path}`, 'utf8'),
  list: path => readdirSync(`/proc/${path}`),
  link: path => readlinkSync(`/proc/${path}`),
});

export interface Forward { service: string; listen: number; ip: string; port: number }
export interface ForwarderDeclaration { pid: number; network: string; forwards: Forward[] }
/** An attested endpoint Docker does not publish: [service, container port, harness host port]. */
export interface UnpublishedEndpoint { service: string; containerPort: string; hostPort: number }
/** A forwarded endpoint in the receipt: every probe sent through it was delivered, confirmed
 * by arrival and an answer only the container could give (answer-bound), or by arrival
 * alone (arrival-only, with the reason; ARRIVAL_ONLY). Not ProbeTarget.kind, which names the probe. */
export interface ForwardedEndpoint { service: string; hostPort: number; probes: number; delivered: number;
  kind: 'answer-bound' | 'arrival-only'; reason?: string }

/** Probe connections per loopback listener (127.0.0.1 and ::1 each) of a forwarded endpoint. */
export const PROBES_PER_LISTENER = 8;
/** `<service> <container port>` -> the probe that reads that container's own evidence. */
export const PROBE_KINDS: Record<string, string> = {
  'frontend 80/tcp': 'frontend', 'postgres 5432/tcp': 'postgres', 'clickhouse 8123/tcp': 'clickhouse',
  'mock-slack 8080/tcp': 'mock-slack', 'mock-llm 8080/tcp': 'mock-llm', 'webhook-sink 8080/tcp': 'webhook-sink',
};
/** Probe kinds whose probes confirm arrival only, each with the reason the receipt records. */
export const ARRIVAL_ONLY: Record<string, string> = {
  frontend: 'the frontend is product-owned nginx with no route that could answer a probe with a nonce-bound value, ' +
    'so its probes confirm arrival in its access log only and a forwarder that replaces or replays the answers is not detected',
};
/** One forwarded endpoint to probe: `probes` connections spread evenly over `hosts`. */
export interface ProbeTarget {
  kind: string; service: string; hostPort: number; hosts: string[];
  /** The inspected container (docker exec / docker logs). */
  container: string; probes: number;
}
/** What the probes found, counts only: sent, and delivered to the inspected container. */
export interface ProbeOutcome { service: string; hostPort: number; sent: number; delivered: number }
export type Prober = (targets: ProbeTarget[]) => ProbeOutcome[];
/** The stores' client credentials the harness already uses (lib/env.ts), for the probes only. */
export interface ProbeCredentials {
  pg?: { user: string; password: string; database: string };
  ch?: { user: string; password: string };
}

const PROBE_SCRIPT = fileURLToPath(new URL('./forward-probe.mjs', import.meta.url));
/** The real prober: one child process (the inspection is synchronous) that opens the
 * probe connections and reads the containers' evidence itself. Its plan, credentials
 * included, goes on stdin, never on a command line; it prints counts only. */
export const childProber = (context: string, credentials: ProbeCredentials = {}): Prober => targets => {
  const env: NodeJS.ProcessEnv = { ...process.env, DOCKER_CONTEXT: context };
  delete env.NODE_OPTIONS;
  delete env.DOCKER_HOST;
  const out = execFileSync(process.execPath, [PROBE_SCRIPT], { input: JSON.stringify({ context, credentials, targets }),
    encoding: 'utf8', timeout: 150_000, maxBuffer: 1024 * 1024, env, stdio: ['pipe', 'pipe', 'pipe'] });
  return JSON.parse(out) as ProbeOutcome[];
};

const refuse = (ok: unknown, reason: string): void => {
  if (!ok) throw new Error(`STOP: managed mock ${reason}`);
};
const stop = (reason: string): never => { throw new Error(`STOP: managed mock ${reason}`); };
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const isRecord = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const isPort = (v: unknown) => Number.isInteger(v) && (v as number) >= 1 && (v as number) <= 65535;
const SERVICE = /^[a-z0-9][a-z0-9_.-]{0,62}$/;
const NETWORK = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;

/** Exact keys and types; IPv4 targets; one entry per listen port. */
export function parseForwarderDeclaration(raw: string): ForwarderDeclaration {
  let d: unknown;
  try { d = raw.length <= 16_384 ? JSON.parse(raw) : undefined; } catch { d = undefined; }
  const forwards = isRecord(d) ? d.forwards : undefined;
  refuse(isRecord(d) && same(Object.keys(d).sort(), ['forwards', 'network', 'pid']) &&
    Number.isInteger(d.pid) && (d.pid as number) > 1 && typeof d.network === 'string' && NETWORK.test(d.network) &&
    Array.isArray(forwards) && forwards.length >= 1 && forwards.length <= 64 &&
    forwards.every(f => isRecord(f) && same(Object.keys(f).sort(), ['ip', 'listen', 'port', 'service']) &&
      typeof f.service === 'string' && SERVICE.test(f.service) && isPort(f.listen) && isPort(f.port) &&
      typeof f.ip === 'string' && isIPv4(f.ip)) &&
    new Set(forwards.map(f => (f as Forward).listen)).size === forwards.length,
  'loopback forwarder declaration is malformed');
  return d as unknown as ForwarderDeclaration;
}

export interface TcpSocket { ip: string; port: number; state: string; inode: string }
export const TCP_LISTEN = '0A';
export const LOOPBACK = ['127.0.0.1', '::1'];

/** One /proc/net/tcp{,6} address: 32-bit words in host byte order. */
function procAddress(hex: string): string {
  const bytes: number[] = [];
  for (let w = 0; w < hex.length; w += 8) {
    const word = hex.slice(w, w + 8).match(/../g)!.map(b => parseInt(b, 16));
    bytes.push(...(endianness() === 'LE' ? word.reverse() : word));
  }
  if (bytes.length === 4) return bytes.join('.');
  if (bytes.slice(0, 10).every(b => b === 0) && bytes[10] === 255 && bytes[11] === 255) return bytes.slice(12).join('.');
  const groups = Array.from({ length: 8 }, (_, i) => ((bytes[2 * i] << 8) | bytes[2 * i + 1]).toString(16));
  let start = -1, length = 0;
  for (let i = 0; i < 8;) {
    let j = i;
    while (j < 8 && groups[j] === '0') j++;
    if (j - i > length && j - i >= 2) { start = i; length = j - i; }
    i = j > i ? j : i + 1;
  }
  return start < 0 ? groups.join(':')
    : `${groups.slice(0, start).join(':')}::${groups.slice(start + length).join(':')}`;
}

const SOCKET_LINE = /^\s*\d+:\s+([0-9A-Fa-f]{8}|[0-9A-Fa-f]{32}):([0-9A-Fa-f]{4})\s+[0-9A-Fa-f]+:[0-9A-Fa-f]{4}\s+([0-9A-Fa-f]{2})\s+\S+\s+\S+\s+\S+\s+\d+\s+\d+\s+(\d+)(\s|$)/;
function parseTcpTable(text: string): TcpSocket[] {
  const [header, ...lines] = text.split('\n');
  // tcp prints rem_address, tcp6 remote_address.
  if (!/^\s*sl\s+local_address\s+rem(ote)?_address\s+st\b/.test(header)) throw new Error('unexpected procfs socket table');
  return lines.filter(line => line.trim()).map(line => {
    const m = SOCKET_LINE.exec(line);
    if (!m) throw new Error('unexpected procfs socket line');
    return { ip: procAddress(m[1]), port: parseInt(m[2], 16), state: m[3].toUpperCase(), inode: m[4] };
  });
}

/** Every TCP socket of this network namespace (both families). */
export function tcpSockets(proc: ProcFs): TcpSocket[] {
  let v6 = '';
  try { v6 = proc.read('net/tcp6'); }
  catch (error) { if ((error as { code?: unknown }).code !== 'ENOENT') throw error; } // IPv6 off: no v6 sockets
  return [...parseTcpTable(proc.read('net/tcp')), ...(v6 ? parseTcpTable(v6) : [])];
}

/** The socket inodes a process holds; any unreadable descriptor table throws. */
export function socketInodes(proc: ProcFs, pid: number): Set<string> {
  const inodes = new Set<string>();
  for (const fd of proc.list(`${pid}/fd`)) {
    if (!/^\d+$/.test(fd)) continue;
    let target: string;
    try { target = proc.link(`${pid}/fd/${fd}`); }
    catch (error) { if ((error as { code?: unknown }).code === 'ENOENT') continue; throw error; } // closed meanwhile
    const m = /^socket:\[(\d+)\]$/.exec(target);
    if (m) inodes.add(m[1]);
  }
  return inodes;
}

/** This process's ancestors, nearest first (from /proc/<pid>/stat's ppid). */
export function processAncestors(proc: ProcFs): number[] {
  const out: number[] = [];
  for (let pid = proc.self; pid > 1 && out.length < 128;) {
    const stat = proc.read(`${pid}/stat`);
    const m = /^\) \S+ (\d+) /.exec(stat.slice(stat.lastIndexOf(')')));
    if (!m) throw new Error('unexpected procfs stat');
    pid = Number(m[1]);
    out.push(pid);
  }
  return out;
}

export interface ForwarderEvidence {
  /** The compose project's name and its default network's name (compose config). */
  project: string; network: string;
  /** docker network inspect of that network, already checked as the project's own bridge. */
  net: { Internal?: unknown; Containers?: Record<string, { IPv4Address?: string }> | null };
  selected: Record<string, Container>;
  /** docker inspect, read-only, for declared targets outside the inspected services. */
  inspect(ids: string[]): Container[];
  proc: ProcFs;
  /** The Docker context the probes read the containers through; default as the inspection's. */
  context?: string;
  credentials?: ProbeCredentials;
  /** Offline tests pass recorded outcomes; flows never pass it (the real child prober runs). */
  probe?: Prober;
}

/** Proves every endpoint in `unpublished` is reached through the declared loopback
 * forwarder (see the contract above) or STOPs; returns them for the receipt. */
export function attestLoopbackForwards(unpublished: UnpublishedEndpoint[], ev: ForwarderEvidence): ForwardedEndpoint[] {
  const first = unpublished[0].service;
  const raw = process.env[LOOPBACK_FORWARDS_ENV];
  // No forwarder: exactly the inspection's original refusal.
  refuse(raw, `endpoint is not published by ${first}`);
  const declaration = parseForwarderDeclaration(raw!);
  refuse(ev.net.Internal === true && declaration.network === ev.network,
    'loopback forwarder is not on the managed internal network');
  const undeterminable = (service: string) => `loopback forwarder mapping for ${service} is undeterminable`;
  // Unreadable or unexpected procfs: no evidence, never an assumption.
  const evidence = <T>(read: () => T): T => {
    try { return read(); } catch { throw new Error(`STOP: managed mock ${undeterminable(first)}`); }
  };
  const sockets = evidence(() => tcpSockets(ev.proc));
  refuse(evidence(() => processAncestors(ev.proc)).includes(declaration.pid), 'loopback forwarder is not an ancestor of this run');
  const owned = evidence(() => socketInodes(ev.proc, declaration.pid));

  // Where each declared target sits on the internal network, from the network's own side.
  const attached = new Map<string, string[]>();
  for (const [id, endpoint] of Object.entries(ev.net.Containers ?? {})) {
    const ip = String(endpoint?.IPv4Address ?? '').split('/')[0];
    attached.set(ip, [...(attached.get(ip) ?? []), id]);
  }
  const targetReason = (service: string) =>
    `loopback forwarder target for ${service} is not the managed ${service} container on the internal network`;
  for (const { service, containerPort, hostPort } of unpublished) {
    const forward = declaration.forwards.find(f => f.listen === hostPort);
    refuse(forward, undeterminable(service));
    const c = ev.selected[service];
    const ip = c.NetworkSettings.Networks[ev.network]?.IPAddress;
    refuse(forward!.service === service && `${forward!.port}/tcp` === containerPort && ip && forward!.ip === ip &&
      same(attached.get(ip), [c.Id]), targetReason(service));
  }
  const extras = declaration.forwards.filter(f => !unpublished.some(e => e.hostPort === f.listen));
  const ids = extras.map(f => attached.get(f.ip));
  const outside = [...new Set(ids.flatMap(list => list?.length === 1 ? list : []))]
    .filter(id => !Object.values(ev.selected).some(c => c.Id === id));
  const extra = outside.length ? ev.inspect(outside) : [];
  extras.forEach((f, i) => {
    const id = ids[i]?.length === 1 ? ids[i]![0] : undefined;
    const c = [...Object.values(ev.selected), ...extra].find(x => x.Id === id);
    refuse(c && c.State.Running && c.Config.Labels['com.docker.compose.project'] === ev.project &&
      c.Config.Labels['com.docker.compose.service'] === f.service &&
      c.NetworkSettings.Networks[ev.network]?.IPAddress === f.ip, targetReason(f.service));
  });

  // The forwarder alone holds every declared port, on loopback only.
  for (const f of declaration.forwards) {
    const listeners = sockets.filter(s => s.state === TCP_LISTEN && s.port === f.listen);
    refuse(listeners.length > 0, undeterminable(f.service));
    refuse(listeners.every(s => LOOPBACK.includes(s.ip)), `loopback forwarder for ${f.service} is bound to a non-loopback address`);
    refuse(listeners.every(s => owned.has(s.inode)), `loopback forwarder for ${f.service} does not hold its port alone`);
  }

  // Effective behaviour, not the declaration: probes through every listener, each
  // confirmed from the inspected container's own side (see the header).
  const targets: ProbeTarget[] = unpublished.map(({ service, containerPort, hostPort }) => {
    const kind = PROBE_KINDS[`${service} ${containerPort}`];
    refuse(kind, `loopback forwarder for ${service} cannot be probed`);
    const hosts = LOOPBACK.filter(ip => sockets.some(s => s.state === TCP_LISTEN && s.port === hostPort && s.ip === ip));
    const c = ev.selected[service];
    return { kind, service, hostPort, hosts, container: c.Id, probes: PROBES_PER_LISTENER * hosts.length };
  });
  const context = ev.context ?? (process.env.DOCKER_CONTEXT || (process.env.CI ? 'default' : ''));
  refuse(context, 'requires an explicit Docker context outside CI');
  let outcomes: ProbeOutcome[];
  try { outcomes = (ev.probe ?? childProber(context, ev.credentials))(targets); }
  catch { return stop(`loopback forwarder probes for ${first} could not run`); }
  for (const t of targets) {
    const o = Array.isArray(outcomes) ? outcomes.filter(x => x?.service === t.service && x?.hostPort === t.hostPort) : [];
    refuse(t.probes >= PROBES_PER_LISTENER && o.length === 1 && o[0].sent === t.probes && o[0].delivered === t.probes,
      `loopback forwarder for ${t.service} did not deliver every probe to the managed ${t.service} container`);
  }
  return targets.map(({ kind, service, hostPort, probes }): ForwardedEndpoint => ARRIVAL_ONLY[kind]
    ? { service, hostPort, probes, delivered: probes, kind: 'arrival-only', reason: ARRIVAL_ONLY[kind] }
    : { service, hostPort, probes, delivered: probes, kind: 'answer-bound' });
}
