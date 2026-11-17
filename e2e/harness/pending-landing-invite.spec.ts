// J02 fixture controls, NOT WS-LANDING browser acceptance. Runtime is owed to G3.
import { randomUUID } from 'node:crypto';
import { connect } from 'node:net';
import { mkdir, readdir, realpath, rmdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';
import { startLocalMailCapture, MAX_MAIL_FRAME, type LocalMailCapture } from '../lib/local-mail-capture';
import { seedPendingLandingInvite, pendingInviteIdentity, assertPendingInviteSnapshot, type OwnedInviteSnapshot } from '../lib/pending-landing-invite';
import { test as scopeTest } from '../lib/scope-actors';
import { E2E } from '../lib/env';
import { landingPlan } from '../lib/landing-fixtures';

const origin = 'http://127.0.0.1:4173';
const href = `${origin}/auth/jwt/invitation/accept/c3ludGhldGlj/synthetic-token`;
const frame = (to: string, link = href) => ({ to: [to], cc: [], bcc: [], from: 'e2e-sender@example.invalid',
  subject: 'Synthetic invitation', text: link, html: `<a href="${link}">Accept</a>`, replyTo: [] });
async function send(socketPath: string, body: object | Buffer): Promise<boolean> {
  const data = Buffer.isBuffer(body) ? body : Buffer.from(JSON.stringify(body));
  const size = Buffer.alloc(4); size.writeUInt32BE(data.length);
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
async function mailbox() {
  const nonce = randomUUID(), email = pendingInviteIdentity(nonce).email;
  const dir = join(await realpath(tmpdir()), `e2e-mail-${nonce}`);
  await mkdir(dir, { mode: 0o700 });
  const socketPath = join(dir, 'mail.sock');
  const capture = await startLocalMailCapture({ socketPath, nonce, allowedRecipients: [email], appOrigin: origin });
  return { nonce, email, dir, socketPath, capture,
    async dispose(fault = false) {
      if (fault) await expect(capture.close()).rejects.toThrow('mail capture refused');
      else await capture.close();
      await rmdir(dir);
    } };
}

test('local mail capture: deduplicates HTML/text copies and retains nothing on disk or stdout', async () => {
  const m = await mailbox();
  const output: string[] = [];
  const stdout = process.stdout.write, stderr = process.stderr.write;
  const sink = ((chunk: unknown) => { output.push(String(chunk)); return true; }) as typeof stdout;
  try {
    const after = m.capture.cursor();
    process.stdout.write = sink; process.stderr.write = sink;
    const ack = await send(m.socketPath, frame(m.email));
    const delivered = await m.capture.takeInvite({ recipient: m.email, after, timeoutMs: 100 });
    process.stdout.write = stdout; process.stderr.write = stderr;
    expect(ack).toBe(true);
    expect(delivered.href === href).toBe(true); // Never print the href in assertion output.
    expect(delivered.messageSequence).toBe(after + 1);
    expect(await readdir(m.dir)).toEqual(['mail.sock']);
    expect(output.length).toBe(0);
    await m.capture.clear(m.email);
  } finally { process.stdout.write = stdout; process.stderr.write = stderr; await m.dispose(); }
});

for (const fault of ['wrong-recipient', 'second-message', 'nonlocal', 'two-hrefs', 'oversized'] as const) {
  test(`local mail capture: ${fault} poisons capture before navigation`, async () => {
    const m = await mailbox();
    try {
      const after = m.capture.cursor();
      if (fault === 'second-message') expect(await send(m.socketPath, frame(m.email))).toBe(true);
      let payload: object | Buffer = frame(m.email);
      if (fault === 'wrong-recipient') payload = frame(m.email.replace('-invite@', '-other@'));
      if (fault === 'nonlocal') payload = frame(m.email, 'https://external.invalid/auth/jwt/invitation/accept/a/b');
      if (fault === 'two-hrefs') payload = { ...frame(m.email), text: `${href} ${href}-other` };
      // Keep valid JSON/envelope/link so removing ONLY the size guard must accept it.
      if (fault === 'oversized') {
        expect(MAX_MAIL_FRAME).toBe(256 * 1024);
        payload = { ...frame(m.email), subject: 'x'.repeat(256 * 1024) };
      }
      expect(await send(m.socketPath, payload)).toBe(false);
      await expect(m.capture.takeInvite({ recipient: m.email, after, timeoutMs: 100 })).rejects.toThrow('mail capture refused');
    } finally { await m.dispose(true); }
  });
}

// The pre-repair collector ACKs every one of these because it scans raw MIME
// text. Only a real visible plain-text URL or usable HTML anchor is delivery.
for (const [kind, html, text] of [
  ['comment', `<!-- ${href} -->`, 'No link'],
  ['comment-anchor', `<!-- <a href="${href}">Accept</a> -->`, 'No link'],
  ['html-text', `<p>${href}</p>`, 'No link'],
  ['attribute', `<p data-url="${href}">Accept</p>`, 'No link'],
  ['script', `<script>const link = '<a href="${href}">Accept</a>';</script>`, 'No link'],
  ['style', `<style>/* ${href} */</style>`, 'No link'],
  ['template', `<template><a href="${href}">Accept</a></template>`, 'No link'],
  ['hidden', `<div hidden><a href="${href}">Accept</a></div>`, 'No link'],
  ['hidden-style', `<a style="display:none" href="${href}">Accept</a>`, 'No link'],
  ['empty-anchor', `<a href="${href}"></a>`, 'No link'],
  ['whitespace-anchor', `<a href="${href}"> &nbsp; </a>`, 'No link'],
  ['stylesheet-hidden', `<style>a {display:none}</style><a href="${href}">Accept</a>`, 'No link'],
  ['aria-hidden', `<div aria-hidden="true"><a href="${href}">Accept</a></div>`, 'No link'],
  ['plaintext-element', `<plaintext><a href="${href}">Accept</a></plaintext>`, 'No link'],
  ['select-element', `<select><a href="${href}">Accept</a></select>`, 'No link'],
  ['duplicate-href', `<a href="${href}" href="${href}">Accept</a>`, 'No link'],
  ['quoted-markup', `<p title='<a href="${href}">Accept</a>'>No link</p>`, 'No link'],
  ['plaintext-comment', '', `<!-- ${href} -->`],
  ['unclosed-comment', `<!-- <a href="${href}">Accept</a>`, 'No link'],
]) {
  test(`local mail capture: ${kind}-only invitation is not delivered`, async () => {
    const m = await mailbox();
    try {
      const after = m.capture.cursor();
      expect(await send(m.socketPath, { ...frame(m.email), html, text })).toBe(false);
      await expect(m.capture.takeInvite({ recipient: m.email, after, timeoutMs: 100 })).rejects.toThrow('mail capture refused');
    } finally { await m.dispose(true); }
  });
}
for (const part of ['html', 'text'] as const) {
  test(`local mail capture: actual ${part} link survives unrelated non-rendered URLs`, async () => {
    const m = await mailbox();
    try {
      const after = m.capture.cursor();
      const html = `<!-- ${href}-comment --><script>const hidden = '${href}-script';</script>` +
        (part === 'html' ? `<A title='1 > 0' HREF='${href}'><span>Accept</span></A>` : '<p>No HTML link</p>');
      expect(await send(m.socketPath, { ...frame(m.email), html, text: part === 'text' ? href : 'No text link' })).toBe(true);
      expect((await m.capture.takeInvite({ recipient: m.email, after, timeoutMs: 100 })).href === href).toBe(true);
    } finally { await m.dispose(); }
  });
}

test('local mail capture: missing duplicate wrong-recipient and nonlocal links refuse before navigation', async () => {
  const m = await mailbox();
  try {
    await expect(m.capture.takeInvite({ recipient: m.email, after: m.capture.cursor(), timeoutMs: 20 }))
      .rejects.toThrow('mail capture refused');
  } finally { await m.dispose(true); }
});

test('local mail capture: socket outside nonce-owned directory and nonlocal app origin refuse', async () => {
  const nonce = randomUUID();
  const parent = await realpath(tmpdir());
  const owned = join(parent, `e2e-mail-${nonce}`), outside = join(parent, `j02-wrong-${nonce}`);
  await mkdir(owned, { mode: 0o700 }); await mkdir(outside, { mode: 0o700 });
  const options = { nonce, allowedRecipients: [pendingInviteIdentity(nonce).email], appOrigin: origin };
  try {
    for (const candidate of [{ ...options, socketPath: join(outside, 'mail.sock') },
      { ...options, socketPath: join(owned, 'mail.sock'), appOrigin: 'https://external.invalid' }]) {
      let rejected = false, unexpected: LocalMailCapture | undefined;
      try { unexpected = await startLocalMailCapture(candidate); } catch { rejected = true; }
      finally { await unexpected?.close(); }
      expect(rejected).toBe(true); // Existing directories: ENOENT cannot impersonate the guard.
    }
  } finally { await rmdir(owned); await rmdir(outside); }
});

test('pending landing invite: inspector preserves inherited startup guards', () => {
  const source = readFileSync(new URL('../lib/landing-fixtures.ts', import.meta.url), 'utf8');
  expect(source.includes('NO_STARTUP_DB_MUTATIONS=false')).toBe(false);
});

test('local mail backend: source has no SMTP/file/console backend or content emitter', () => {
  const source = readFileSync(new URL('../lib/local_mail_backend.py', import.meta.url), 'utf8');
  expect(/smtplib|FileBased|console\.EmailBackend|print\(|logging\.|open\(/.test(source)).toBe(false);
  expect(source.includes('socket.AF_UNIX')).toBe(true);
  expect(source.includes('E2E_MAIL_CAPTURE_SOCKET')).toBe(true);
  const collector = readFileSync(new URL('../lib/local-mail-capture.ts', import.meta.url), 'utf8');
  expect(/writeFile|appendFile|createWriteStream|console\.|process\.(stdout|stderr)/.test(collector)).toBe(false);
});

test('pending landing invite: fixed default beats first grant and lowest UUID', () => {
  const plan = landingPlan('11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222', 'pending-invite');
  const preferred = plan.workspaces.find(w => w.org === plan.org && w.is_default)!;
  const alternate = plan.workspaces.find(w => w.org === plan.org && !w.is_default)!;
  expect(plan.expected === preferred.id && alternate.id < preferred.id).toBe(true);
  expect(plan.expectedActiveMembershipIdsBeforeAuth.length).toBe(0);
  expect(plan.expectedActiveMembershipIdsAfterAuth.sort()).toEqual([preferred.id, alternate.id].sort());
  // Fixture validation must reject autoaccepted ScopeActors and all key minting.
  const pending = { userActive: false, status: 'Pending', inviteCount: 1, isExpired: false, tokenValid: true,
    systemKeyCount: 0, keyCount: 0, tokenCount: 0, grantsValid: true, orgPending: true, selectorsAbsent: true,
    activeWorkspaceIds: [], workspaceIds: ['alternate', 'preferred'],
    inviteGrants: [{ workspace_id: 'alternate', level: 3 }, { workspace_id: 'preferred', level: 3 }] } as unknown as OwnedInviteSnapshot;
  expect(() => assertPendingInviteSnapshot(pending, 'alternate', 'preferred')).not.toThrow();
  for (const fault of [{ userActive: true }, { status: 'Accepted' }, { systemKeyCount: 1 },
    { keyCount: 1 }, { tokenCount: 1 }, { activeWorkspaceIds: ['alternate'] },
    { workspaceIds: ['foreign', 'preferred'] }, { inviteGrants: [...pending.inviteGrants].reverse() }]) {
    expect(() => assertPendingInviteSnapshot({ ...pending, ...fault }, 'alternate', 'preferred')).toThrow();
  }
});

// Independent PG observations retain full token rows and microsecond timestamps.
// No access/refresh bearer values are read or emitted by these controls.
type AuthRow = { id: string; user_id: string; auth_type: string; used_at: string; micros: string; stable: Record<string, unknown> };
async function authRows(probe: import('../lib/state-probe').StateProbe, userIds: string[]) {
  return probe.pg<AuthRow>(`SELECT id::text, user_id::text, auth_type, last_used_at::text AS used_at,
    (extract(epoch FROM last_used_at)*1000000)::bigint::text AS micros, to_jsonb(t)-'last_used_at' AS stable
    FROM accounts_auth_token t WHERE user_id=ANY($1::uuid[]) ORDER BY id`, [userIds]);
}
function issuerTouch(before: AuthRow[], after: AuthRow[], ownerId: string) {
  expect(after.length === before.length).toBe(true);
  expect(JSON.stringify(after.map(t => t.stable)) === JSON.stringify(before.map(t => t.stable))).toBe(true);
  const changed = after.filter((t, i) => t.used_at !== before[i]?.used_at);
  expect(changed.length === 1 && changed[0].user_id === ownerId && changed[0].auth_type === 'access').toBe(true);
  const row = changed[0], previous = before.find(t => t.id === row.id)!;
  expect(BigInt(row.micros) > BigInt(previous.micros)).toBe(true);
  return row;
}

scopeTest('pending landing invite: cleanup restores owned rows and leaves foreign tenant unchanged', async ({ scopeActors, scopeProbe }) => {
  const { capture, nonce } = await liveCapture();
  let fixture: Awaited<ReturnType<typeof seedPendingLandingInvite>> | undefined;
  const snapshot = async () => scopeProbe.pg<{ row: unknown }>(
    `SELECT to_jsonb(w) AS row FROM accounts_workspace w WHERE organization_id=ANY($1::uuid[]) ORDER BY id`,
    [[scopeActors.ownerA.organizationId, scopeActors.ownerB.organizationId]]);
  const before = JSON.stringify(await snapshot());
  const tokenUsers = [scopeActors.ownerA, scopeActors.ownerB, scopeActors.member, scopeActors.viewer].map(a => a.userId);
  const beforeAuth = await authRows(scopeProbe, tokenUsers);
  try {
    fixture = await seedPendingLandingInvite({ ownerA: scopeActors.ownerA, ownerB: scopeActors.ownerB, probe: scopeProbe, capture, nonce });
    const issuedAuth = await authRows(scopeProbe, tokenUsers);
    issuerTouch(beforeAuth, issuedAuth, scopeActors.ownerA.userId);
    const pending = await fixture.snapshotAllRows();
    expect(!pending.userActive && pending.status === 'Pending' && pending.tokenCount === 0).toBe(true);
    await fixture.dispose(); await fixture.dispose();
    // Disposal preserves the ONE legitimate touch, not a silent rewind to before issuance.
    expect(JSON.stringify(await authRows(scopeProbe, tokenUsers)) === JSON.stringify(issuedAuth)).toBe(true);
    expect(JSON.stringify(await snapshot()) === before).toBe(true);
    const users = await scopeProbe.pg<{ n: string }>('SELECT count(*)::text AS n FROM accounts_user WHERE email=$1', [pendingInviteIdentity(nonce).email]);
    expect(users[0]?.n).toBe('0');
  } finally { try { await fixture?.dispose(); } finally { await capture.close(); } }
});

scopeTest('pending landing invite: owner roster and member JWT readbacks retain exact cleanup bookkeeping', async ({ scopeActors, scopeProbe }) => {
  const { capture, nonce } = await liveCapture();
  let f: Awaited<ReturnType<typeof seedPendingLandingInvite>> | undefined;
  const users = [scopeActors.ownerA, scopeActors.ownerB, scopeActors.member, scopeActors.viewer].map(a => a.userId);
  try {
    f = await seedPendingLandingInvite({ ownerA: scopeActors.ownerA, ownerB: scopeActors.ownerB, probe: scopeProbe, capture, nonce });
    const issued = await authRows(scopeProbe, users);
    const rawBefore = await f.snapshotAllRows();
    const roster = await f.readOwnerRoster<{ result: { workspaces: { id: string }[]; total: number } }>();
    expect(roster.status).toBe(200);
    expect(roster.body.result.total).toBe(roster.body.result.workspaces.length);
    expect([f.preferredWorkspaceId, f.alternateWorkspaceId].every(id => roster.body.result.workspaces.some(w => w.id === id))).toBe(true);
    expect(roster.body.result.workspaces.some(w => w.id === f!.foreignWorkspaceId)).toBe(false);
    for (const workspace of roster.body.result.workspaces) {
      const members = await f.readOwnerMembers<{ result: { workspace: { id: string }; members: { user_id: string }[]; total: number } }>(workspace.id);
      expect(members.status).toBe(200);
      expect(members.body.result.workspace.id).toBe(workspace.id);
      expect(members.body.result.members.length).toBe(members.body.result.total);
    }
    const readAuth = await authRows(scopeProbe, users);
    issuerTouch(issued, readAuth, scopeActors.ownerA.userId);
    const rawAfter = await f.snapshotAllRows();
    // Raw refusal readers do NOT normalize legitimate owner token/cache writes.
    expect(rawAfter.categories.tokens === rawBefore.categories.tokens).toBe(false);
    expect(rawAfter.protectedDigest === rawBefore.protectedDigest).toBe(false);
    for (const category of Object.keys(rawBefore.categories).filter(k => !['tokens', 'cache'].includes(k)))
      expect(rawAfter.categories[category] === rawBefore.categories[category]).toBe(true);
    await expect(f.readOwnerMembers(f.foreignWorkspaceId)).rejects.toThrow();
    expect((await f.snapshotAllRows()).protectedDigest === rawAfter.protectedDigest).toBe(true);
    const parts = new URL(f.deliveredHref).pathname.split('/').slice(-2);
    expect(await inviteRequest(`/accounts/accept-invitation/${parts.join('/')}/`, 'POST',
      { new_password: f.invitee.password, repeat_password: f.invitee.password })).toBe(200);
    const accepted = await f.snapshotAllRows();
    expect(accepted.userActive && accepted.status === 'Accepted').toBe(true);
    expect(accepted.activeWorkspaceIds.sort()).toEqual([f.preferredWorkspaceId, f.alternateWorkspaceId].sort());
    for (const workspaceId of [f.preferredWorkspaceId, f.alternateWorkspaceId]) {
      const members = await f.readOwnerMembers<{ result: { members: { user_id: string }[] } }>(workspaceId);
      expect(members.status).toBe(200);
      expect(members.body.result.members.some(m => m.user_id === f!.invitee.userId)).toBe(true);
    }
    const postAcceptAuth = await authRows(scopeProbe, users);
    issuerTouch(readAuth, postAcceptAuth, scopeActors.ownerA.userId);
    await f.dispose(); await f.dispose();
    expect(JSON.stringify(await authRows(scopeProbe, users)) === JSON.stringify(postAcceptAuth)).toBe(true);
  } finally { try { await f?.dispose(); } finally { await capture.close(); } }
});

// Corrupt a preexisting row AFTER the public issuer's legitimate auth touch.
// Cleanup must roll back and must not re-baseline or repair any planted drift.
// Restoring ONLY the planted field must then allow cleanup, retaining the touch.
for (const fault of ['member-level', 'user-config', 'issuer-token-time', 'issuer-token-active',
  'issuer-token-deleted', 'other-token-time'] as const) {
  scopeTest(`pending landing invite: cleanup refuses preexisting A ${fault} drift`, async ({ scopeActors, scopeProbe }) => {
    const { capture, nonce } = await liveCapture();
    const actor = scopeActors.member, org = scopeActors.ownerA.organizationId;
    const memberships = await scopeProbe.pg<{ id: string; level: number }>(
      'SELECT id, level FROM accounts_organization_membership WHERE user_id=$1 AND organization_id=$2', [actor.userId, org]);
    const users = await scopeProbe.pg<{ config: unknown }>('SELECT config FROM accounts_user WHERE id=$1', [actor.userId]);
    expect(memberships.length === 1 && users.length === 1).toBe(true);
    const member = memberships[0];
    let f: Awaited<ReturnType<typeof seedPendingLandingInvite>> | undefined;
    const read = async () => JSON.stringify(await scopeProbe.pg<{ row: unknown }>(
      `SELECT to_jsonb(u) AS row FROM accounts_user u WHERE id=$1
       UNION ALL SELECT to_jsonb(m) AS row FROM accounts_organization_membership m WHERE user_id=$1 AND organization_id=$2`,
      [actor.userId, org]));
    const before = await read();
    const tokenUsers = [scopeActors.ownerA, scopeActors.ownerB, actor, scopeActors.viewer].map(a => a.userId);
    const beforeAuth = await authRows(scopeProbe, tokenUsers);
    let issuedAuth: AuthRow[] | undefined, plantedToken: AuthRow | undefined;
    const tokenField = fault.endsWith('-active') ? 'is_active' : fault.endsWith('-deleted') ? 'deleted' : 'last_used_at';
    try {
      f = await seedPendingLandingInvite({ ownerA: scopeActors.ownerA, ownerB: scopeActors.ownerB, probe: scopeProbe, capture, nonce });
      // Mandatory public readback must not make later unrelated drift admissible.
      expect((await f.readOwnerRoster()).status).toBe(200);
      issuedAuth = await authRows(scopeProbe, tokenUsers);
      const issuer = issuerTouch(beforeAuth, issuedAuth, scopeActors.ownerA.userId);
      const issued = (await f.snapshotAllRows()).protectedDigest;
      if (fault === 'member-level') await scopeProbe.pg(
        'UPDATE accounts_organization_membership SET level=$1 WHERE id=$2 AND user_id=$3 AND organization_id=$4',
        [member.level === 1 ? 3 : 1, member.id, actor.userId, org]);
      else if (fault === 'user-config') await scopeProbe.pg('UPDATE accounts_user SET config=$1::jsonb WHERE id=$2 AND organization_id=$3',
        [JSON.stringify({ j02Control: nonce }), actor.userId, org]);
      else {
        plantedToken = fault === 'other-token-time' ? issuedAuth.find(t => t.user_id === actor.userId && t.auth_type === 'access') : issuer;
        expect(Boolean(plantedToken)).toBe(true);
        const value = tokenField === 'last_used_at' ? 'last_used_at + interval \'1 microsecond\'' : `NOT ${tokenField}`;
        await scopeProbe.pg(`UPDATE accounts_auth_token SET ${tokenField}=${value} WHERE id=$1 AND user_id=$2`,
          [plantedToken!.id, plantedToken!.user_id]);
      }
      const planted = (await f.snapshotAllRows()).protectedDigest;
      expect(planted === issued).toBe(false);
      if (plantedToken) {
        // A second legitimate GET cannot launder a pre-request token change.
        await expect(f.readOwnerRoster()).rejects.toThrow('landing fixture command failed (ValueError)');
        expect((await f.snapshotAllRows()).protectedDigest === planted).toBe(true);
      }
      await expect(f.dispose()).rejects.toThrow('landing fixture command failed (ValueError)');
      // Refusal must leave the entire planted state and invitee intact.
      expect((await f.snapshotAllRows()).protectedDigest === planted).toBe(true);
      const pending = await scopeProbe.pg<{ n: string }>('SELECT count(*)::text AS n FROM accounts_user WHERE id=$1', [f.invitee.userId]);
      expect(pending[0]?.n).toBe('1');
    } finally {
      try {
        if (fault === 'member-level') await scopeProbe.pg(
          'UPDATE accounts_organization_membership SET level=$1 WHERE id=$2 AND user_id=$3 AND organization_id=$4',
          [member.level, member.id, actor.userId, org]);
        if (fault === 'user-config') await scopeProbe.pg('UPDATE accounts_user SET config=$1::jsonb WHERE id=$2 AND organization_id=$3',
          [JSON.stringify(users[0].config), actor.userId, org]);
        if (plantedToken) await scopeProbe.pg(`UPDATE accounts_auth_token SET ${tokenField}=$1 WHERE id=$2 AND user_id=$3`,
          [tokenField === 'last_used_at' ? plantedToken.used_at : plantedToken.stable[tokenField], plantedToken.id, plantedToken.user_id]);
        await f?.dispose();
        expect(await read() === before).toBe(true);
        if (issuedAuth) expect(JSON.stringify(await authRows(scopeProbe, tokenUsers)) === JSON.stringify(issuedAuth)).toBe(true);
      } finally { await capture.close(); }
    }
  });
}

// Live setup is deliberately a fresh user, never scopeActors.member/invite().
// Collector directory/mount and effective settings must be supplied by Stage A.
// J02-ADOPT-01: each live case opens a fresh session on the SAME Stage A socket.
// The capture admits only one session at a time and tombstones every nonce.
async function inviteRequest(route: string, method: 'GET' | 'POST', data?: object): Promise<number> {
  try {
    const response = await fetch(`${E2E.apiUrl}${route}`, { method, redirect: 'error', signal: AbortSignal.timeout(30_000),
      headers: { 'Content-Type': 'application/json' }, body: data ? JSON.stringify(data) : undefined });
    await response.body?.cancel();
    return response.status;
  } catch { throw new Error('invitation request transport failed'); } // Never report the token-bearing URL.
}
async function liveCapture(): Promise<{ capture: LocalMailCapture; nonce: string }> {
  const nonce = randomUUID();
  const socketPath = process.env.E2E_MAIL_CAPTURE_SOCKET;
  if (!socketPath) throw new Error('G3 CJ-OVERLAY fixed mail binding missing');
  const capture = await startLocalMailCapture({ nonce, socketPath,
    allowedRecipients: [pendingInviteIdentity(nonce).email], appOrigin: E2E.appUrl });
  return { capture, nonce };
}

async function assertReplayRefusals(
  snapshot: () => Promise<{ protectedDigest: string }>,
  request: (method: 'GET' | 'POST') => Promise<number>,
) {
  const beforeGet = await snapshot();
  expect(await request('GET')).toBe(400);
  expect((await snapshot()).protectedDigest === beforeGet.protectedDigest,
    'replay GET must not write protected state').toBe(true);
  // Independently bracket POST; never permit compensating writes to hide GET.
  const beforePost = await snapshot();
  expect(await request('POST')).toBe(400);
  expect((await snapshot()).protectedDigest === beforePost.protectedDigest,
    'replay POST must not write protected state').toBe(true);
}

for (const category of ['cache', 'database'] as const) {
  test(`pending landing invite: replay detects compensating ${category} writes at the GET boundary`, async () => {
    const state = { cache: 'original', database: 'original' }, calls: string[] = [];
    const snapshot = async () => ({ protectedDigest: JSON.stringify(state) });
    const request = async (method: 'GET' | 'POST') => {
      calls.push(method);
      state[category] = method === 'GET' ? 'illicit-write' : 'original';
      return 400;
    };
    // With the old GET -> POST -> snapshot sequence both faulty requests pass.
    await expect(assertReplayRefusals(snapshot, request)).rejects.toThrow('replay GET must not write protected state');
    expect(calls).toEqual(['GET']);
    expect(state[category]).toBe('illicit-write');
  });
}
test('pending landing invite: replay POST has its own immediate no-write bracket', async () => {
  let state = 'original';
  const calls: string[] = [];
  await expect(assertReplayRefusals(async () => ({ protectedDigest: state }), async method => {
    calls.push(method); if (method === 'POST') state = 'illicit-write'; return 400;
  })).rejects.toThrow('replay POST must not write protected state');
  expect(calls).toEqual(['GET', 'POST']);
});

for (const arm of ['valid', 'preview', 'deadline', 'expired', 'cancelled', 'missing'] as const) {
  const names = {
    valid: 'pending landing invite: delivered local link activates exactly the two planned grants',
    preview: 'pending landing invite: preview is read-only and replay is rejected',
    deadline: 'pending landing invite: deadline-expired Pending with valid token refuses GET and POST without writes',
    expired: 'pending landing invite: EXPIRED status refuses without membership writes',
    cancelled: 'pending landing invite: Cancelled and missing authority refuse without writes',
    missing: 'pending landing invite: missing authority refuses without writes',
  };
  scopeTest(names[arm], async ({ scopeActors, scopeProbe }) => {
    const { capture, nonce } = await liveCapture();
    let f: Awaited<ReturnType<typeof seedPendingLandingInvite>> | undefined;
    try {
      f = await seedPendingLandingInvite({ ownerA: scopeActors.ownerA, ownerB: scopeActors.ownerB, probe: scopeProbe, capture, nonce });
      const pending = await f.snapshotAllRows();
      expect(pending.userActive).toBe(false);
      expect(pending.status).toBe('Pending');
      expect(pending.activeWorkspaceIds.length).toBe(0);
      expect(pending.workspaceIds.sort()).toEqual([f.alternateWorkspaceId, f.preferredWorkspaceId].sort());
      expect(pending.systemKeyCount).toBe(0);
      expect(f.expectedWorkspaceId === f.preferredWorkspaceId && f.alternateWorkspaceId < f.preferredWorkspaceId).toBe(true);
      const parts = new URL(f.deliveredHref).pathname.split('/').slice(-2);
      const route = `/accounts/accept-invitation/${parts.join('/')}/`;
      if (arm === 'deadline') await f.expirePendingInvite();
      if (arm === 'expired' || arm === 'cancelled') await f.setPendingInviteStatus(arm === 'expired' ? 'Expired' : 'Cancelled');
      if (arm === 'missing') await f.removePendingInviteAuthority();
      const before = await f.snapshotAllRows();
      if (arm === 'deadline') {
        expect(before.status).toBe('Pending'); expect(before.isExpired).toBe(true);
        expect(before.effectiveStatus).toBe('Expired'); expect(before.tokenValid).toBe(true);
      }
      const positive = arm === 'valid' || arm === 'preview';
      const preview = await inviteRequest(route, 'GET');
      expect(preview).toBe(positive ? 200 : 400);
      expect((await f.snapshotAllRows()).protectedDigest === before.protectedDigest).toBe(true);
      const accepted = await inviteRequest(route, 'POST',
        { new_password: f.invitee.password, repeat_password: f.invitee.password });
      expect(accepted).toBe(positive ? 200 : 400);
      const after = await f.snapshotAllRows();
      if (positive) {
        expect(after.userActive).toBe(true); expect(after.status).toBe('Accepted');
        expect(after.activeWorkspaceIds.sort()).toEqual([f.alternateWorkspaceId, f.preferredWorkspaceId].sort());
        await assertReplayRefusals(f.snapshotAllRows, method => inviteRequest(route, method,
          method === 'POST' ? { new_password: f!.invitee.password, repeat_password: f!.invitee.password } : undefined));
        expect((await f.snapshotAllRows()).protectedDigest === after.protectedDigest).toBe(true);
      } else expect(after.protectedDigest === before.protectedDigest).toBe(true);
    } finally {
      // Snapshot before deletion, also on a failed assertion; nothing is attached.
      try { if (f) await f.snapshotAllRows(); } finally {
        try { await f?.dispose(); } finally { await capture.close(); }
      }
    }
  });
}
