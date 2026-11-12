import { test, expect } from '@playwright/test';
import type { APIRequestContext, Browser, BrowserContext, BrowserContextOptions } from '@playwright/test';
import { ApiClient } from '../lib/api-client';
import { authInitScript } from '../lib/auth';
import { E2E } from '../lib/env';
import { ScopeActors, type ScopeActor, type ScopeRoster } from '../lib/scope-actors';

// c99dc71f35 compatibility: exercise the original one-argument constructor,
// public invitation wire, two-argument openContext, evidence and workspace copy.
// Only the unchanged owner-signup and transport seams are doubled. No API,
// Docker, database or browser is opened, and no synthetic tokens are attached.
function scopeDouble(roster?: ScopeRoster) {
  const scopes = roster === undefined ? new ScopeActors('compat') : new ScopeActors('compat', roster);
  const sent: { path: string; options: any }[] = [];
  const invites = new Map<string, { email: string; organizationId: string; workspaceId: string; level: number }>();
  let disposed = 0;
  const internal = scopes as unknown as {
    owner(label: string): Promise<ScopeActor>;
    newRequest(email: string): Promise<APIRequestContext>;
    requests: Map<string, APIRequestContext>;
  };
  const request = (email: string) => ({
    async fetch(url: string, options: any) {
      const path = new URL(url).pathname;
      sent.push({ path, options });
      let status = 200;
      let body: unknown;
      if (path === '/accounts/workspaces/') {
        status = 201;
        body = { result: { workspace: { id: 'workspace-empty', name: 'compat-empty' }, failed_users: [], other_org_users: [] } };
      } else if (path === '/accounts/organization/invite/') {
        const data = options.data;
        const entry = { email: data.emails[0], organizationId: options.headers['X-Organization-Id'],
          workspaceId: data.workspace_access[0].workspace_id, level: data.org_level };
        invites.set(entry.email, entry);
        body = { result: { invited: [entry.email], invites: [{ email: entry.email,
          invite_link: 'None/auth/jwt/invitation/accept/fixture/fixture/' }] } };
      } else if (path.startsWith('/accounts/accept-invitation/')) {
        body = options.method === 'GET' ? { valid: true, email } : { access: 'synthetic-access', refresh: 'synthetic-refresh' };
      } else if (path === '/accounts/user-info/') {
        const entry = invites.get(email)!;
        body = { id: `user-${email}`, email, organization: { id: entry.organizationId }, org_level: entry.level };
      } else {
        throw new Error(`unexpected offline request ${path}`);
      }
      return { status: () => status, json: async () => body };
    },
    async dispose() { disposed++; },
  }) as unknown as APIRequestContext;
  internal.newRequest = async (email) => {
    const req = request(email);
    internal.requests.set(email, req);
    return req;
  };
  internal.owner = async (label) => {
    const email = `${label}@example.invalid`;
    const req = await internal.newRequest(email);
    const tokens = { access: 'synthetic-access', refresh: 'synthetic-refresh' };
    return { userId: `user-${label}`, email, tokens, organizationId: `org-${label}`, workspaceId: `workspace-${label}`,
      api: new ApiClient(req, E2E.apiUrl).withAuth(tokens, `org-${label}`, `workspace-${label}`) };
  };
  return { scopes, sent, disposed: () => disposed };
}

function browserDouble() {
  const calls: { name: string; value?: unknown }[] = [];
  let onClose = () => {};
  const context = {
    on(event: string, fn: () => void) { expect(event).toBe('close'); onClose = fn; },
    async addInitScript(fn: unknown, arg: unknown) { calls.push({ name: 'init', value: { fn, arg } }); },
    async close() { calls.push({ name: 'close' }); onClose(); },
  } as unknown as BrowserContext;
  const browser = { async newContext(options: BrowserContextOptions) {
    calls.push({ name: 'context', value: options }); return context;
  } } as unknown as Browser;
  return { browser, context, calls };
}

for (const roster of [undefined, {}]) {
  test(`scope compatibility offline: ${roster ? 'explicit empty' : 'default'} roster retains the four legacy actors and invite wire`, async () => {
    const { scopes, sent, disposed } = scopeDouble(roster);
    await scopes.provision();
    const invitations = sent.filter(row => row.path === '/accounts/organization/invite/');
    expect(invitations.map(row => row.options.data)).toEqual([
      { emails: ['compat-member@futureagi.com'], org_level: 3, workspace_access: [
        { workspace_id: scopes.ownerA.workspaceId, level: 3 }, { workspace_id: 'workspace-empty', level: 3 }] },
      { emails: ['compat-viewer@futureagi.com'], org_level: 1,
        workspace_access: [{ workspace_id: scopes.ownerA.workspaceId, level: 1 }] },
    ]);
    for (const row of invitations) expect(row.options.headers).toEqual(scopes.headers(scopes.ownerA));
    expect(sent.map(row => row.options.method)).toEqual(['POST', 'POST', 'GET', 'POST', 'GET', 'POST', 'GET', 'POST', 'GET']);
    expect(sent.every(row => row.options.maxRedirects === 0)).toBe(true);
    expect(scopes.admin).toBeUndefined();
    expect(scopes.member2).toBeUndefined();
    expect(scopes.orgBMembers).toEqual([]);
    expect(scopes.orgA().map(row => [row.level, row.role])).toEqual([[15, 'owner'], [3, 'member'], [1, 'viewer']]);
    const actors = [scopes.ownerA, scopes.ownerB, scopes.member, scopes.viewer];
    expect(scopes.evidence()).toEqual({ prefix: 'compat', emptyWorkspace: scopes.emptyWorkspace,
      limitations: Array(2).fill('APP_URL unset: public invite link starts None/auth/; direct link navigation not qualified'),
      actors: actors.map(({ userId, email, organizationId, workspaceId }) => ({ userId, email, organizationId, workspaceId })),
      requests: scopes.receipts });
    expect(JSON.stringify(scopes.evidence())).not.toContain('synthetic-access');
    expect(scopes.receipts.filter(row => row.path.includes('accept-invitation')).every(
      row => row.path === '/accounts/accept-invitation/<redacted>/' && !row.authenticated)).toBe(true);
    const scoped = scopes.withWorkspace(scopes.member, scopes.emptyWorkspace.id);
    expect(scoped.workspaceId).toBe('workspace-empty');
    expect(scoped.organizationId).toBe(scopes.member.organizationId);
    expect(scoped.tokens).toBe(scopes.member.tokens);
    expect(scopes.member.workspaceId).toBe(scopes.ownerA.workspaceId);
    expect(scopes.headers(scoped)['X-Workspace-Id']).toBe('workspace-empty');
    await scopes.dispose();
    expect(disposed()).toBe(4);
  });
}

test('scope compatibility offline: extra actors are opt-in and org B invitations never use org A', async () => {
  const { scopes, sent, disposed } = scopeDouble({ admin: true, secondMember: true, orgB: [1, 3, 8] });
  await scopes.provision();
  const invitations = sent.filter(row => row.path === '/accounts/organization/invite/');
  expect(invitations.map(row => row.options.data.org_level)).toEqual([3, 1, 8, 3, 1, 3, 8]);
  expect(invitations.slice(0, 4).every(row => row.options.headers['X-Organization-Id'] === scopes.ownerA.organizationId)).toBe(true);
  expect(invitations.slice(4).every(row => row.options.headers['X-Organization-Id'] === scopes.ownerB.organizationId)).toBe(true);
  expect(scopes.orgBMembers.every(actor => actor.organizationId === scopes.ownerB.organizationId
    && actor.workspaceId === scopes.ownerB.workspaceId)).toBe(true);
  expect(scopes.orgA().map(row => row.level)).toEqual([15, 8, 3, 3, 1]);
  expect(scopes.evidence().actors).toHaveLength(9);
  await scopes.dispose();
  expect(disposed()).toBe(9);
});

test('scope compatibility offline: two-argument openContext retains base URL and both auth seeds without a guard', async () => {
  const { scopes } = scopeDouble();
  await scopes.provision();
  const fake = browserDouble();
  expect(await scopes.openContext(fake.browser, scopes.member)).toBe(fake.context);
  expect(fake.calls).toEqual([
    { name: 'context', value: { baseURL: E2E.appUrl } },
    { name: 'init', value: { fn: authInitScript, arg: { ...scopes.member.tokens,
      organizationId: scopes.member.organizationId, workspaceId: scopes.member.workspaceId } } },
    { name: 'init', value: { fn: expect.any(Function), arg: scopes.member.organizationId } },
  ]);
  await fake.context.close();
  await scopes.dispose();
  expect(fake.calls.filter(row => row.name === 'close')).toHaveLength(1);
});

test('scope compatibility offline: an opt-in guard runs before seeds and closes the context on refusal', async () => {
  const { scopes } = scopeDouble();
  await scopes.provision();
  const fake = browserDouble();
  await scopes.openContext(fake.browser, scopes.ownerA, { options: { serviceWorkers: 'block', baseURL: 'http://wrong.invalid' },
    guard: async (context) => { expect(context).toBe(fake.context); fake.calls.push({ name: 'guard' }); } });
  expect(fake.calls.map(row => row.name)).toEqual(['context', 'guard', 'init', 'init']);
  expect(fake.calls[0].value).toEqual({ serviceWorkers: 'block', baseURL: E2E.appUrl });
  await scopes.dispose();
  expect(fake.calls.filter(row => row.name === 'close')).toHaveLength(1);
  const refused = browserDouble();
  await expect(scopes.openContext(refused.browser, scopes.ownerA, { guard: async () => { throw new Error('offline refusal'); } }))
    .rejects.toThrow('offline refusal');
  expect(refused.calls.map(row => row.name)).toEqual(['context', 'close']);
});
