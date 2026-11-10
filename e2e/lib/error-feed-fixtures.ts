import { execFileSync } from 'node:child_process';
import { test as base, expect } from '@playwright/test';
import { ScopeActors } from './scope-actors';
import { provisionActor } from './provisioning';
import { managedErrorFeedBackend } from './error-feed-harness';
import { POLL, StateProbe } from './state-probe';
import { E2E } from './env';

// Enterprise signup mails a generated password. Use the supported local owner
// CLI for fixture creation; login, roles, invitations and API keys use normal
// product paths. No direct SQL role edits, token minting or license bypass.
export const test = base.extend<{ errorFeedProfile: void; scopeActors: ScopeActors; scopeProbe: StateProbe }>({
  errorFeedProfile: [async ({}, use) => {
    base.skip(process.env.E2E_STACK !== 'error-feed', 'Requires the dedicated entitled Error Feed stack');
    await use();
  }, { auto: true }],
  // CLI account setup runs before a test callback can extend its own timeout.
  scopeActors: [async ({}, use, info) => {
    const backend = managedErrorFeedBackend();
    // Independent cohorts share Docker's host IP. Reset only this isolated
    // stack's login IP budget; never auth tokens, roles, memberships or data.
    execFileSync('docker', ['exec', backend.Id, 'python', '/opt/e2e/reset_fixture_auth.py'],
      { encoding: 'utf8', timeout: 60_000, stdio: ['pipe', 'pipe', 'pipe'] });
    const scopes = new ScopeActors(`e2e-h1-${info.workerIndex}-${Date.now().toString(36)}`,
      (req, label) => provisionActor(req, label, async ({ email, fullName, password }) => {
        if (!email.startsWith('e2e-e2e-h1-') || !email.endsWith('@futureagi.com')) {
          throw new Error('Refusing owner creation outside the owned E2E prefix');
        }
        execFileSync('docker', ['exec', backend.Id, 'python', 'manage.py', 'create_user',
          '--email', email, '--name', fullName, '--password', password],
          { encoding: 'utf8', timeout: 120_000, stdio: ['pipe', 'pipe', 'pipe'] });
      }), async email => {
        if (!email.startsWith(scopes.prefix + '-')) throw new Error('Refusing foreign local-mail read');
        let link = '';
        await expect.poll(() => {
          try {
            link = execFileSync('docker', ['exec', backend.Id, 'python', '/app/backend/error_feed_mail.py', email],
              { encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }).trim();
            return link.startsWith('http://localhost:3113/auth/jwt/invitation/accept/');
          } catch { return false; }
        }, POLL.ASYNC_JOB).toBe(true);
        return link;
      });
    try {
      await scopes.provision();
      await info.attach('seeded-scopes', { contentType: 'application/json', body: JSON.stringify(scopes.evidence()) });
      await use(scopes);
    } finally {
      await info.attach('scope-api-receipt', { contentType: 'application/json', body: JSON.stringify(scopes.evidence()) });
      await scopes.dispose();
    }
  }, { timeout: 360_000 }],
  scopeProbe: async ({ scopeActors }, use) => {
    const probe = new StateProbe({ api: scopeActors.ownerA.api, pgUrl: E2E.pgUrl,
      chUrl: E2E.chUrl, chDatabase: E2E.chDatabase, chPassword: E2E.chPassword,
      catalogChUrl: E2E.catalogChUrl, catalogChDatabase: E2E.catalogChDatabase });
    try { await use(probe); } finally { await probe.dispose(); }
  },
});
export { expect };
