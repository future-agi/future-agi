// lib/loopback-forwarder.ts: the declaration contract a runner's loopback forwarder
// exports, and the procfs reader the managed-mock inspection proves it with. The
// whole-inspection cases (accepted layouts and every refusal, with recorded probe
// outcomes) are in managed-mock.spec.ts, the real probes against real containers and
// real unsafe forwarders in loopback-forwarder-lab.spec.ts; here the parser meets the
// real kernel's /proc text.
import { test, expect } from '@playwright/test';
import { createConnection, createServer, type AddressInfo, type Server, type Socket } from 'node:net';
import { LOOPBACK_FORWARDS_ENV, linuxProc, parseForwarderDeclaration, processAncestors, socketInodes, tcpSockets, TCP_LISTEN } from '../lib/loopback-forwarder';
import { inspectManagedMock } from '../lib/managed-mock';

const listen = (host: string) => new Promise<Server>((resolve, reject) => {
  const server = createServer(socket => socket.on('error', () => socket.destroy()));
  server.once('error', reject);
  server.listen({ host, port: 0 }, () => resolve(server));
});
const connect = (port: number) => new Promise<Socket>((resolve, reject) => {
  const socket = createConnection({ host: '127.0.0.1', port }, () => resolve(socket));
  socket.once('error', reject);
});

test('loopback forwarder offline reads a real procfs: this process\'s loopback listeners, a connection and its ancestry', async () => {
  test.skip(process.platform !== 'linux', 'procfs is Linux only');
  const proc = linuxProc();
  const v4 = await listen('127.0.0.1');
  const v6 = await listen('::1').catch(() => undefined); // a VM without IPv6 loopback
  const port4 = (v4.address() as AddressInfo).port;
  const client = await connect(port4);
  try {
    const sockets = tcpSockets(proc);
    const owned = socketInodes(proc, process.pid);
    const listeners4 = sockets.filter(s => s.state === TCP_LISTEN && s.port === port4);
    expect(listeners4.map(s => s.ip)).toEqual(['127.0.0.1']);
    expect(owned.has(listeners4[0].inode)).toBe(true);
    const outbound = sockets.filter(s => s.state === '01' && s.port === client.localPort && s.ip === '127.0.0.1');
    expect(outbound).toHaveLength(1);
    expect(owned.has(outbound[0].inode)).toBe(true);
    if (v6) {
      const port6 = (v6.address() as AddressInfo).port;
      const listeners6 = sockets.filter(s => s.state === TCP_LISTEN && s.port === port6);
      expect(listeners6.map(s => s.ip)).toEqual(['::1']);
      expect(owned.has(listeners6[0].inode)).toBe(true);
    }
    const ancestors = processAncestors(proc);
    expect(ancestors[0]).toBe(process.ppid);
    expect(ancestors).not.toContain(process.pid);
  } finally {
    client.destroy();
    v4.close();
    v6?.close();
  }
});

const valid = { pid: 4000, network: 'futureagi-e2e_default',
  forwards: [{ service: 'frontend', listen: 3100, ip: '172.18.0.10', port: 80 },
    { service: 'fi-collector', listen: 24318, ip: '172.18.0.99', port: 4318 }] };

test('loopback forwarder offline accepts the exact declaration contract', () => {
  expect(parseForwarderDeclaration(JSON.stringify(valid))).toEqual(valid);
});

const malformed: [string, unknown][] = [
  ['an array', [valid]],
  ['an extra top-level key', { ...valid, bind: '0.0.0.0' }],
  ['a missing pid', { network: valid.network, forwards: valid.forwards }],
  ['pid 1', { ...valid, pid: 1 }],
  ['a string pid', { ...valid, pid: '4000' }],
  ['a network name with a slash', { ...valid, network: 'other/default' }],
  ['no forwards', { ...valid, forwards: [] }],
  ['an extra forward key', { ...valid, forwards: [{ ...valid.forwards[0], host: '127.0.0.1' }] }],
  ['a hostname target', { ...valid, forwards: [{ ...valid.forwards[0], ip: 'frontend' }] }],
  ['an IPv6 target', { ...valid, forwards: [{ ...valid.forwards[0], ip: 'fd00::10' }] }],
  ['a port out of range', { ...valid, forwards: [{ ...valid.forwards[0], port: 70000 }] }],
  ['a fractional listen port', { ...valid, forwards: [{ ...valid.forwards[0], listen: 3100.5 }] }],
  ['an upper-case service', { ...valid, forwards: [{ ...valid.forwards[0], service: 'Frontend' }] }],
  ['two forwards on one listen port', { ...valid, forwards: [valid.forwards[0], { ...valid.forwards[1], listen: 3100 }] }],
  ['65 forwards', { ...valid, forwards: Array.from({ length: 65 },
    (_, i) => ({ service: `s${i}`, listen: 1000 + i, ip: '172.18.0.10', port: 80 })) }],
];
for (const [name, value] of malformed) {
  test(`loopback forwarder offline refuses a declaration with ${name}`, () => {
    expect(() => parseForwarderDeclaration(JSON.stringify(value)))
      .toThrow('STOP: managed mock loopback forwarder declaration is malformed');
  });
}
for (const [name, raw] of [['that is not JSON', 'frontend=3100:80'],
  ['over 16 KiB', JSON.stringify(valid) + ' '.repeat(16_384)]] as const) {
  test(`loopback forwarder offline refuses a declaration ${name}`, () => {
    expect(() => parseForwarderDeclaration(raw)).toThrow('STOP: managed mock loopback forwarder declaration is malformed');
  });
}

// A running stack on an internal default network behind a real loopback forwarder
// (the runner exports E2E_LOOPBACK_FORWARDS to this command). E2E_LOOPBACK_EXPECT is
// `accept`, or the STOP a deliberately broken forwarder must produce. Outside the
// offline selection; skipped wherever no forwarder wraps the run.
test('loopback forwarder live: the managed stack behind the runner\'s forwarder', () => {
  test.skip(!process.env[LOOPBACK_FORWARDS_ENV], 'needs a running loopback forwarder (E2E_LOOPBACK_FORWARDS)');
  const expected = process.env.E2E_LOOPBACK_EXPECT ?? 'accept';
  if (expected !== 'accept') {
    expect(() => inspectManagedMock()).toThrow(`STOP: managed mock ${expected}`);
    return;
  }
  const declared = parseForwarderDeclaration(process.env[LOOPBACK_FORWARDS_ENV]!).forwards.map(f => f.listen);
  const receipt = inspectManagedMock();
  expect(receipt.forwarded?.length).toBeGreaterThan(0);
  expect(receipt.forwarded!.every(f => declared.includes(f.hostPort))).toBe(true);
  // Each one proven by active probes, eight or more through each listener, every one delivered.
  const counted: { probes?: number; delivered?: number }[] = JSON.parse(JSON.stringify(receipt.forwarded));
  expect(counted.every(f => (f.probes ?? 0) >= 8 && f.delivered === f.probes)).toBe(true);
});
