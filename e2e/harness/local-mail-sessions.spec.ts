// J02-ADOPT-01(a). Additive controls; existing J02 assertions stay unchanged.
// Runtime red/green is owed to the guarded integrated run, not static preparation.
import { randomUUID } from 'node:crypto';
import { connect } from 'node:net';
import { mkdir, readdir, realpath, rmdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test, expect } from '@playwright/test';
import { startLocalMailCapture, mailCaptureBinding, MAX_MAIL_FRAME, type LocalMailCapture } from '../lib/local-mail-capture';

const origin = 'http://127.0.0.1:4173';
const href = `${origin}/auth/jwt/invitation/accept/c3ludGhldGlj/synthetic-token`;
const identity = () => {
  const nonce = randomUUID();
  return { nonce, email: `e2e-${nonce}-invite@example.invalid` };
};
const frame = (to: string, link = href) => ({ to: [to], cc: [], bcc: [], from: 'e2e-sender@example.invalid',
  subject: 'Synthetic invitation', text: link, html: `<a href="${link}">Accept</a>`, replyTo: [] });
async function send(socketPath: string, body: object): Promise<boolean> {
  const data = Buffer.from(JSON.stringify(body)), size = Buffer.alloc(4);
  size.writeUInt32BE(data.length);
  return new Promise(resolve => {
    const socket = connect(socketPath); socket.setTimeout(1500);
    let ack = '';
    socket.on('connect', () => socket.end(Buffer.concat([size, data])));
    socket.on('data', chunk => { ack += chunk.toString('ascii'); });
    socket.on('error', () => resolve(false));
    socket.on('timeout', () => { socket.destroy(); resolve(false); });
    socket.on('close', () => resolve(ack === 'OK\n'));
  });
}
async function cell() {
  const transport = identity(), dir = join(await realpath(tmpdir()), `e2e-mail-${transport.nonce}`);
  await mkdir(dir, { mode: 0o700 });
  const socketPath = join(dir, 'mail.sock'), captures: LocalMailCapture[] = [];
  const open = async (session = identity()) => {
    const capture = await startLocalMailCapture({ socketPath, nonce: session.nonce,
      allowedRecipients: [session.email], appOrigin: origin });
    captures.push(capture);
    return { ...session, capture };
  };
  return { transport, dir, socketPath, open,
    async dispose() {
      // Fault controls assert close refusal explicitly; final cleanup is best effort.
      for (const capture of captures) await capture.close().catch(() => {});
      await rmdir(dir);
    } };
}
async function consume(c: Awaited<ReturnType<typeof cell>>, session: Awaited<ReturnType<typeof c.open>>) {
  const after = session.capture.cursor();
  expect(after).toBe(0);
  expect(await send(c.socketPath, frame(session.email))).toBe(true);
  const delivered = await session.capture.takeInvite({ recipient: session.email, after, timeoutMs: 100 });
  expect(delivered.href === href).toBe(true); // No token-bearing assertion output.
  expect(delivered.messageSequence).toBe(1);
  await session.capture.clear(session.email);
}

test('mail sessions: fresh nonce recipients and cursor on the same fixed socket', async () => {
  const c = await cell(), recipients = new Set<string>(), nonces = new Set<string>();
  try {
    for (let i = 0; i < 3; i++) {
      const session = await c.open(); // Deliberately NOT the transport nonce, even on first open.
      expect(session.nonce === c.transport.nonce).toBe(false);
      expect(nonces.has(session.nonce) || recipients.has(session.email)).toBe(false);
      nonces.add(session.nonce); recipients.add(session.email);
      const binding = mailCaptureBinding(session.capture);
      expect(binding.socketPath === c.socketPath && binding.nonce === session.nonce).toBe(true);
      expect(binding.recipients.length === 1 && binding.recipients[0] === session.email).toBe(true);
      expect(binding.appOrigin === origin).toBe(true);
      await consume(c, session);
      expect(await readdir(c.dir)).toEqual(['mail.sock']);
      await session.capture.close();
      await session.capture.close();
      expect(() => session.capture.cursor()).toThrow('mail capture refused');
      expect(await readdir(c.dir)).toEqual([]);
    }
  } finally { await c.dispose(); }
});

test('mail sessions: previous recipient refuses and poisons the new session', async () => {
  const c = await cell();
  try {
    const previous = await c.open(c.transport);
    await consume(c, previous); await previous.capture.close();
    const current = await c.open();
    expect(await send(c.socketPath, frame(previous.email))).toBe(false);
    await expect(current.capture.takeInvite({ recipient: current.email, after: 0, timeoutMs: 100 }))
      .rejects.toThrow('mail capture refused');
    await expect(current.capture.close()).rejects.toThrow('mail capture refused');
    await expect(c.open()).rejects.toThrow('mail capture refused');
  } finally { await c.dispose(); }
});

test('mail sessions: overlapping opens admit exactly one, without poisoning the winner', async () => {
  const c = await cell();
  try {
    const outcomes = await Promise.allSettled([c.open(), c.open()]);
    expect(outcomes.filter(o => o.status === 'fulfilled').length).toBe(1);
    expect(outcomes.filter(o => o.status === 'rejected').length).toBe(1);
    const winner = outcomes.find(o => o.status === 'fulfilled');
    if (!winner || winner.status !== 'fulfilled') throw new Error('mail session winner missing');
    await consume(c, winner.value); await winner.value.capture.close();
    const next = await c.open(); await consume(c, next); await next.capture.close();
  } finally { await c.dispose(); }
});

test('mail sessions: consumed but unclosed session blocks next open', async () => {
  const c = await cell();
  try {
    const first = await c.open(c.transport);
    await consume(c, first);
    await expect(c.open()).rejects.toThrow('mail capture refused');
    await first.capture.close();
    const next = await c.open(); await consume(c, next); await next.capture.close();
  } finally { await c.dispose(); }
});

test('mail sessions: closed nonce replay refuses on the fixed socket and another socket', async () => {
  const c = await cell(), other = await cell();
  try {
    const first = await c.open(c.transport);
    await consume(c, first); await first.capture.close();
    await expect(c.open(first)).rejects.toThrow('mail capture refused');
    await expect(other.open(first)).rejects.toThrow('mail capture refused');
    const next = await c.open(); await consume(c, next); await next.capture.close();
  } finally { await c.dispose(); await other.dispose(); }
});

for (const pending of ['unread-message', 'waiter', 'partial-frame'] as const) {
  test(`mail sessions: ${pending} prevents clean close and successor admission`, async () => {
    const c = await cell();
    let client: ReturnType<typeof connect> | undefined;
    try {
      const first = await c.open(c.transport);
      let waiting: Promise<boolean> | undefined;
      if (pending === 'unread-message') expect(await send(c.socketPath, frame(first.email))).toBe(true);
      if (pending === 'waiter') waiting = first.capture.takeInvite({ recipient: first.email, after: 0, timeoutMs: 1000 })
        .then(() => false, () => true);
      if (pending === 'partial-frame') {
        client = connect(c.socketPath); client.on('error', () => {});
        await new Promise<void>((resolve, reject) => { client!.once('connect', resolve); client!.once('error', reject); });
        client.write(Buffer.from([0]));
        // Server-side callback runs before this event-loop turn; leave the frame open.
        await new Promise<void>(resolve => setImmediate(resolve));
      }
      await expect(first.capture.close()).rejects.toThrow('mail capture refused');
      if (waiting) expect(await waiting).toBe(true);
      await expect(c.open()).rejects.toThrow('mail capture refused');
    } finally { client?.destroy(); await c.dispose(); }
  });
}

test('mail sessions: clearing unread mail cannot launder a pending session', async () => {
  const c = await cell();
  try {
    const first = await c.open(c.transport);
    expect(await send(c.socketPath, frame(first.email))).toBe(true);
    await expect(first.capture.clear(first.email)).rejects.toThrow('mail capture refused');
    await expect(first.capture.close()).rejects.toThrow('mail capture refused');
    await expect(c.open()).rejects.toThrow('mail capture refused');
  } finally { await c.dispose(); }
});

test('mail sessions: opening before close settles is refused', async () => {
  const c = await cell();
  try {
    const first = await c.open(c.transport);
    await consume(c, first);
    const closing = first.capture.close();
    await expect(c.open()).rejects.toThrow('mail capture refused');
    await closing;
    const next = await c.open(); await consume(c, next); await next.capture.close();
  } finally { await c.dispose(); }
});

for (const fault of ['second-message', 'nonlocal', 'two-hrefs', 'oversized', 'wrong-recipient', 'stale-cursor'] as const) {
  test(`mail sessions: ${fault} remains refused in a successor session`, async () => {
    const c = await cell();
    try {
      const first = await c.open(c.transport);
      await consume(c, first); await first.capture.close();
      const next = await c.open(), after = next.capture.cursor();
      let payload = frame(next.email);
      if (fault === 'nonlocal') payload = frame(next.email, 'https://external.invalid/auth/jwt/invitation/accept/a/b');
      if (fault === 'two-hrefs') payload = { ...payload, text: `${href} ${href}-other` };
      if (fault === 'oversized') payload = { ...payload, subject: 'x'.repeat(MAX_MAIL_FRAME) };
      if (fault === 'wrong-recipient') payload = frame(next.email.replace('-invite@', '-other@'));
      if (fault === 'second-message') expect(await send(c.socketPath, payload)).toBe(true);
      if (fault === 'stale-cursor') {
        expect(await send(c.socketPath, payload)).toBe(true);
        await expect(next.capture.takeInvite({ recipient: next.email, after: next.capture.cursor(), timeoutMs: 100 }))
          .rejects.toThrow('mail capture refused');
      } else {
        expect(await send(c.socketPath, payload)).toBe(false);
        await expect(next.capture.takeInvite({ recipient: next.email, after, timeoutMs: 100 }))
          .rejects.toThrow('mail capture refused');
      }
      await expect(next.capture.close()).rejects.toThrow('mail capture refused');
    } finally { await c.dispose(); }
  });
}
