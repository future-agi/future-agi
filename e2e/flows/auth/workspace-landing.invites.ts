// J02-ADOPT-01: consumer-only UI assertions. Each arm opens/closes a fresh
// shared capture session; Django's kit-owned socket setting NEVER changes.
import { randomUUID } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import type { BrowserContext, Page } from '@playwright/test';
import { expect } from '../../lib/scope-actors';
import { pendingInviteIdentity, seedPendingLandingInvite, type OwnedInviteSnapshot } from '../../lib/pending-landing-invite';
import { startLocalMailCapture, type LocalMailCapture } from '../../lib/local-mail-capture';
import { WalkBoundary } from '../../lib/walk/boundary';
import { E2E } from '../../lib/env';
import { readInviteState, noInviteWrites, type Pending } from './workspace-landing.invite-checks';
import { checker, wrongOracle, firstInfo, assertLanding, LANDING, UI_READY, type Consumer, type CaseRow, type Check } from './workspace-landing.browser';

export type MailCase = 'invite-link' | 'invite-deadline-expired-pending' | 'invite-cancelled' | 'invite-missing-authority';
type Arm = MailCase | 'invite-explicit-expired';
function publicRoute(f: Pending) {
  const url = new URL(f.deliveredHref);
  if (url.origin !== new URL(E2E.appUrl).origin || url.search || url.hash ||
    !/^\/auth\/jwt\/invitation\/accept\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+$/.test(url.pathname)) throw new Error('AUTH004_DELIVERED_ROUTE');
  // Same received uid/token, mapped to the product API route; never a minted
  // token, public-response-link fallback, rewritten app origin or guessed URL.
  return `/accounts/accept-invitation/${url.pathname.split('/').slice(-2).join('/')}/`;
}
function responseFor(page: Page, route: string, method: string) {
  const promise = page.waitForResponse(r => new URL(r.url()).origin === new URL(E2E.apiUrl).origin &&
    new URL(r.url()).pathname === route && r.request().method() === method, { timeout: UI_READY });
  void promise.catch(() => undefined);
  return promise;
}
async function signedOut(page: Page) {
  return page.evaluate(() => ({ token: Boolean(localStorage.getItem('accessToken')),
    workspace: sessionStorage.getItem('workspaceId'), org: sessionStorage.getItem('organizationId') }));
}
export async function runInvitationCell(scenario: MailCase, c: Consumer): Promise<CaseRow[]> {
  const rows: CaseRow[] = [];
  // Explicit stored Expired is a separate fresh identity/session within the
  // positive test. It cannot substitute for deadline-only Pending expiry.
  const arms: Arm[] = scenario === 'invite-link' ? ['invite-link', 'invite-explicit-expired'] : [scenario];
  for (const arm of arms) {
    const row: CaseRow = { scenario: arm, browser: c.browserName, control: wrongOracle(), checks: [], phase: 'capture', status: 'not-run' };
    rows.push(row);
    const check: Check = checker(row), boundary = new WalkBoundary(`AUTH004-${arm}-${c.browserName}`);
    let capture: LocalMailCapture | undefined, fixture: Pending | undefined;
    let lastNoWrite: OwnedInviteSnapshot | undefined;
    const contexts: BrowserContext[] = [];
    const freshPage = async () => {
      const context = await c.browser.newContext({ baseURL: E2E.appUrl, serviceWorkers: 'block' });
      contexts.push(context);
      await boundary.install(context, `${arm}-${contexts.length}`);
      const page = await context.newPage(); page.setDefaultTimeout(UI_READY); page.setDefaultNavigationTimeout(UI_READY);
      return page;
    };
    try {
      const nonce = randomUUID(), socketPath = process.env.E2E_MAIL_CAPTURE_SOCKET;
      check('stage_a_fixed_socket_bound', Boolean(socketPath));
      capture = await startLocalMailCapture({ nonce, socketPath: socketPath!,
        allowedRecipients: [pendingInviteIdentity(nonce).email], appOrigin: E2E.appUrl });
      row.phase = 'pending-seed';
      fixture = await seedPendingLandingInvite({ ownerA: c.scopes.ownerA, ownerB: c.scopes.ownerB, probe: c.probe, capture, nonce });
      const f = fixture, route = publicRoute(f);
      await readInviteState(f, c, check, false);
      check('unequal_foreign_wrong_oracle', f.foreignWorkspaceId !== f.expectedWorkspaceId);
      if (arm === 'invite-deadline-expired-pending') {
        // FIX-INVITE-EXPIRY 0f047cc94a, fa-708j-fix-invite-expiry/
        // futureagi/accounts/models/organization_invite.py:78-108:
        // created_at + INVITE_VALIDITY_DAYS, NOT an expires_at field/status flip.
        await f.expirePendingInvite();
        const expired = await f.snapshotAllRows();
        check('deadline_only_pending_valid_token', expired.status === 'Pending' && expired.isExpired &&
          expired.effectiveStatus === 'Expired' && expired.tokenValid && !expired.userActive && expired.activeWorkspaceIds.length === 0);
      }
      if (arm === 'invite-cancelled' || arm === 'invite-explicit-expired') {
        const status = arm === 'invite-cancelled' ? 'Cancelled' : 'Expired';
        await f.setPendingInviteStatus(status);
        const state = await f.snapshotAllRows();
        check('stored_status_valid_token', state.status === status && state.tokenValid && !state.userActive && state.activeWorkspaceIds.length === 0);
      }
      if (arm === 'invite-missing-authority') {
        await f.removePendingInviteAuthority();
        const missing = await f.snapshotAllRows();
        check('missing_authority_valid_token', missing.status === null && missing.inviteCount === 0 && missing.tokenValid &&
          !missing.userActive && missing.activeWorkspaceIds.length === 0);
      }
      const refuse = async (label: string) => {
        const page = await freshPage();
        row.phase = `${label}-get`;
        lastNoWrite = await f.snapshotAllRows();
        const preview = responseFor(page, route, 'GET');
        await page.goto(f.deliveredHref, { waitUntil: 'domcontentloaded' });
        const response = await preview;
        const afterGet = await f.snapshotAllRows();
        noInviteWrites(lastNoWrite, afterGet, check, `${label}_get`);
        check(`${label}_get_400`, response.status() === 400);
        await expect(page.getByText('Invite Link Expired', { exact: true })).toBeVisible({ timeout: UI_READY });
        check(`${label}_no_password_form`, await page.getByRole('button', { name: 'Set Password & Continue', exact: true }).count() === 0);
        check(`${label}_no_session`, !(await signedOut(page)).token);
        // The real refusal UI intentionally has no submit form. Exercise a
        // crafted POST from that signed-out browser, through the exact observed
        // endpoint with valid matching passwords; never fabricate acceptance.
        row.phase = `${label}-post`;
        lastNoWrite = await f.snapshotAllRows();
        const status = await page.evaluate(async ({ api, route, password, timeout }) => {
          const response = await fetch(new URL(route, api), { method: 'POST', credentials: 'omit', redirect: 'error',
            headers: { 'Content-Type': 'application/json' }, signal: AbortSignal.timeout(timeout),
            body: JSON.stringify({ new_password: password, repeat_password: password }) });
          await response.body?.cancel(); return response.status;
        }, { api: E2E.apiUrl, route, password: f.invitee.password, timeout: UI_READY });
        noInviteWrites(lastNoWrite, await f.snapshotAllRows(), check, `${label}_post`);
        check(`${label}_post_400`, status === 400);
        const state = await signedOut(page);
        check(`${label}_no_auth_or_scope`, !state.token && state.workspace === null && state.org === null);
        check('expected_landing', !state.token && state.workspace === (row.control ? f.foreignWorkspaceId : null));
        await page.context().close();
      };
      if (arm !== 'invite-link') {
        await refuse('refusal');
      } else {
        const page = await freshPage();
        row.phase = 'preview';
        lastNoWrite = await f.snapshotAllRows();
        const preview = responseFor(page, route, 'GET');
        await page.goto(f.deliveredHref, { waitUntil: 'domcontentloaded' });
        const response = await preview, body = await response.json() as { valid?: boolean; email?: string };
        noInviteWrites(lastNoWrite, await f.snapshotAllRows(), check, 'valid_preview');
        check('valid_preview_200', response.status() === 200 && body.valid === true && body.email === f.invitee.email);
        const before = await signedOut(page);
        check('preview_signed_out', !before.token && before.workspace === null && before.org === null);
        await page.getByLabel('Password', { exact: true }).fill(f.invitee.password);
        await page.getByLabel('Confirm Password', { exact: true }).fill(f.invitee.password);
        row.phase = 'accept-ui';
        const initial = firstInfo(page, check);
        const accepted = responseFor(page, route, 'POST');
        lastNoWrite = undefined; // This explicit positive transition MUST write.
        await page.getByRole('button', { name: 'Set Password & Continue', exact: true }).click();
        check('real_ui_accept_200', (await accepted).status() === 200);
        const info = await initial;
        const local = await readInviteState(f, c, check, true);
        const wanted = { expected: row.control ? f.foreignWorkspaceId : f.expectedWorkspaceId, org: f.org,
          userId: f.invitee.userId, foreign: f.foreignWorkspaceId, eligible: [f.alternateWorkspaceId, f.preferredWorkspaceId], local };
        row.phase = 'landing';
        await page.goto(new URL(LANDING, E2E.appUrl).href, { waitUntil: 'domcontentloaded' });
        await assertLanding(page, info, wanted, check);
        row.phase = 'reload';
        await page.reload({ waitUntil: 'domcontentloaded' });
        await readInviteState(f, c, check, true);
        await assertLanding(page, info, wanted, check, 'reload');
        await page.context().close(); // Stop authenticated incidental activity before replay snapshots.
        await refuse('accepted_replay');
      }
      row.status = 'passed';
    } catch { row.status = 'failed'; }
    finally {
      // Last observation happens BEFORE shared cleanup, including on failure.
      try {
        if (fixture) {
          const final = await fixture.snapshotAllRows();
          if (lastNoWrite) noInviteWrites(lastNoWrite, final, check, 'finally');
        }
      } catch { row.checks.push({ name: 'final_observation', passed: false }); row.status = 'failed'; }
      for (const context of contexts) { try { await context.close(); } catch { row.status = 'failed'; } }
      try { await fixture?.dispose(); } catch { row.checks.push({ name: 'dispose', passed: false }); row.status = 'failed'; }
      try { await capture?.close(); } catch { row.checks.push({ name: 'capture_close', passed: false }); row.status = 'failed'; }
      const audit = boundary.receipt();
      row.boundary = { localRequests: audit.continuedLocal, refused: audit.attempts.length,
        unrouted: audit.attempts.filter(a => a.phase === 'observed-unrouted').length };
      try { check('zero_external_attempts', audit.attempts.length === 0); } catch { row.status = 'failed'; }
      const receipt = JSON.stringify(row);
      writeFileSync(c.testInfo.outputPath(`landing-case-${arm}.json`), receipt);
      await c.testInfo.attach(`landing-case-${arm}`, { contentType: 'application/json', body: receipt });
    }
  }
  return rows;
}
