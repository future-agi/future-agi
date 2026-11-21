import { test as base, expect, request as pwRequest } from '@playwright/test';
import type { APIRequestContext, Browser, BrowserContext } from '@playwright/test';
import { ApiClient, Tokens } from './api-client';
import { authInitScript } from './auth';
import { E2E } from './env';
import { provisionActor, TestActor } from './provisioning';
import { StateProbe } from './state-probe';

export interface ScopeActor {
  userId: string; email: string; tokens: Tokens;
  organizationId: string; workspaceId: string; api: ApiClient;
}
export interface ScopeUserInfo {
  id: string; email: string; organization: { id: string };
  default_workspace_id: string; org_level: number; ws_level: number;
}
export interface ScopeWorkspace { id: string; name: string }
export interface ScopeResponse<T> { status: number; body: T }
export interface ScopeContextOptions {
  /** Only an owned localhost origin; HTTPS does not relax the host guard. */
  appUrl?: string;
  ignoreHTTPSErrors?: boolean;
}

export function scopeId(value: string): string {
  if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value)
    || value === '00000000-0000-0000-0000-000000000000') {
    throw new Error('H1 expected a non-secret canonical UUID');
  }
  return value;
}

export function ownedScopeOrigin(endpoint: string): string {
  const url = new URL(endpoint);
  if (url.hostname !== 'localhost' || !['http:', 'https:'].includes(url.protocol)
    || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('H1 requires a credential-free localhost HTTP(S) origin');
  }
  return url.origin;
}

interface ScopeRequestReceipt {
  method: string; path: string; status: number;
  organizationId: string | null; workspaceId: string | null; authenticated: boolean;
}

/** H1: test-owned public identities, not SQL roles or replacement auth endpoints.
 * Org keys stay on the original owners; a workspace-scoped actor deliberately
 * has NO apiKey/secretKey fields. Switching JWT scope does not rebind an org key.
 */
export class ScopeActors {
  ownerA!: ScopeActor;
  ownerB!: ScopeActor;
  member!: ScopeActor;
  viewer!: ScopeActor;
  emptyWorkspace!: ScopeWorkspace;
  memberInB?: ScopeActor;
  foreign?: ScopeActor;
  readonly owners: TestActor[] = [];
  readonly receipts: ScopeRequestReceipt[] = [];
  readonly limitations: string[] = [];
  private readonly requests = new Map<string, APIRequestContext>();
  private readonly contexts = new Set<BrowserContext>();

  constructor(readonly prefix: string, readonly options: ScopeContextOptions = {}) {
    // Preserve the API and ordinary app guards even when selecting HTTPS media.
    for (const endpoint of [E2E.apiUrl, E2E.appUrl, options.appUrl ?? E2E.appUrl]) {
      ownedScopeOrigin(endpoint);
    }
  }

  headers(actor: ScopeActor): Record<string, string> {
    return { Authorization: `Bearer ${actor.tokens.access}`,
      'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId };
  }

  withWorkspace(actor: ScopeActor, workspaceId: string): ScopeActor {
    const req = this.requests.get(actor.email)!;
    return { ...actor, workspaceId,
      api: new ApiClient(req, E2E.apiUrl).withAuth(actor.tokens, actor.organizationId, workspaceId) };
  }

  /** Retains actual status codes, but never credentials or invitation tokens. */
  async send<T>(actor: ScopeActor, method: string, path: string, data?: unknown,
    params?: Record<string, string | number>): Promise<ScopeResponse<T>> {
    return this.sendOn<T>(this.requests.get(actor.email)!, method, path, data, actor, params);
  }

  private async sendOn<T>(req: APIRequestContext, method: string, path: string, data?: unknown,
    actor?: ScopeActor, params?: Record<string, string | number>): Promise<ScopeResponse<T>> {
    if (!path.startsWith('/') || path.startsWith('//')) throw new Error('H1 needs a relative public API path');
    const response = await req.fetch(`${E2E.apiUrl}${path}`, {
      method, data, params, headers: actor ? this.headers(actor) : {}, maxRedirects: 0, maxRetries: 0,
    });
    this.receipts.push({ method, path: path.split('?')[0].replace(/accept-invitation\/.+/, 'accept-invitation/<redacted>/'),
      status: response.status(), organizationId: actor?.organizationId ?? null,
      workspaceId: actor?.workspaceId ?? null, authenticated: Boolean(actor) });
    const raw = await response.text();
    return { status: response.status(), body: (raw ? JSON.parse(raw) : undefined) as T };
  }

  private async newRequest(emailKey: string): Promise<APIRequestContext> {
    const req = await pwRequest.newContext({ baseURL: E2E.apiUrl,
      ignoreHTTPSErrors: this.options.ignoreHTTPSErrors ?? false });
    this.requests.set(emailKey, req);
    return req;
  }

  private async owner(label: string): Promise<ScopeActor> {
    const req = await this.newRequest(label);
    const owner = await provisionActor(req, label);
    this.requests.delete(label);
    this.requests.set(owner.email, req);
    this.owners.push(owner);
    const actor: ScopeActor = { userId: '', email: owner.email, tokens: owner.tokens,
      organizationId: owner.organizationId, workspaceId: owner.workspaceId, api: owner.api };
    const info = await this.send<ScopeUserInfo>(actor, 'GET', '/accounts/user-info/');
    expect(info.status).toBe(200);
    expect(info.body.organization.id).toBe(actor.organizationId);
    actor.userId = scopeId(info.body.id);
    return actor;
  }

  private async invite(label: string, level: 1 | 3, workspaceIds: string[]): Promise<ScopeActor> {
    // accounts/serializers/rbac.py:34 and views/rbac_views.py:238.
    const email = `${this.prefix}-${label}@futureagi.com`;
    const invited = await this.send<{ result: { invited: string[];
      invites?: { email: string; invite_link: string }[] } }>(
      this.ownerA, 'POST', '/accounts/organization/invite/', {
        emails: [email], org_level: level,
        workspace_access: workspaceIds.map(workspace_id => ({ workspace_id, level })),
      });
    expect(invited.status).toBe(200);
    expect(invited.body.result.invited).toEqual([email]);
    const links = invited.body.result.invites?.filter(item => item.email === email);
    if (links?.length !== 1) throw new Error('H1 blocked: public OSS invite link missing; no SQL/mail shortcut');
    // accounts/utils.py:394 supplies this public link; never derive tokens.
    // Only its route parameters are used against E2E.apiUrl, never its origin.
    // The current local stack has APP_URL=None, yielding the literal prefix
    // "None/auth/...". Retain that direct-navigation defect as evidence, while
    // using the public response's unchanged token on the public API endpoint.
    const link = links[0].invite_link;
    if (link.startsWith('None/auth/')) this.limitations.push('APP_URL unset: public invite link starts None/auth/; direct link navigation not qualified');
    const match = new URL(link.startsWith('None/auth/') ? link.slice(4) : link, E2E.appUrl).pathname
      .match(/^\/auth\/jwt\/invitation\/accept\/([^/]+)\/([^/]+)\/?$/);
    if (!match) throw new Error('H1 blocked: unexpected public invitation link shape');
    const req = await this.newRequest(email);
    const path = `/accounts/accept-invitation/${match[1]}/${match[2]}/`;
    const preview = await this.sendOn<{ valid: boolean; email: string }>(req, 'GET', path);
    expect(preview.status).toBe(200);
    expect(preview.body).toMatchObject({ valid: true, email });
    // accounts/serializers/contracts.py:414; views/signup.py:724,806.
    const password = `H1-${this.prefix}-${label}!`;
    const accepted = await this.sendOn<Tokens>(req, 'POST', path,
      { new_password: password, repeat_password: password });
    expect(accepted.status).toBe(200);
    expect(accepted.body.access).toBeTruthy();
    expect(accepted.body.refresh).toBeTruthy();
    const tokens = { access: accepted.body.access, refresh: accepted.body.refresh };
    const actor: ScopeActor = { userId: '', email, tokens,
      organizationId: this.ownerA.organizationId, workspaceId: workspaceIds[0],
      api: new ApiClient(req, E2E.apiUrl).withAuth(tokens, this.ownerA.organizationId, workspaceIds[0]) };
    const info = await this.send<ScopeUserInfo>(actor, 'GET', '/accounts/user-info/');
    expect(info.status).toBe(200);
    expect(info.body).toMatchObject({ email, organization: { id: actor.organizationId }, org_level: level });
    actor.userId = scopeId(info.body.id);
    return actor;
  }

  async provision(): Promise<void> {
    this.ownerA = await this.owner(`${this.prefix}-a`);
    this.ownerB = await this.owner(`${this.prefix}-b`);
    expect(this.ownerA.organizationId).not.toBe(this.ownerB.organizationId);
    // accounts/serializers/contracts.py:423; views/workspace.py:428.
    const created = await this.send<{ result: { workspace: ScopeWorkspace;
      failed_users: unknown[]; other_org_users: unknown[] } }>(
      this.ownerA, 'POST', '/accounts/workspaces/', { name: `${this.prefix}-empty` });
    expect(created.status).toBe(201);
    expect(created.body.result.failed_users).toEqual([]);
    expect(created.body.result.other_org_users).toEqual([]);
    this.emptyWorkspace = created.body.result.workspace;
    this.member = await this.invite('member', 3, [this.ownerA.workspaceId, this.emptyWorkspace.id]);
    this.viewer = await this.invite('viewer', 1, [this.ownerA.workspaceId]);
  }

  /** Explicit JWT scope only. Never copies/rebinds an owner's organization keys. */
  inOrganization(actor: ScopeActor, owner: ScopeActor): ScopeActor {
    if (![this.ownerA, this.ownerB, this.foreign].includes(owner) || !this.requests.has(actor.email)) {
      throw new Error('H1 requires test-owned actors and organization');
    }
    const organizationId = scopeId(owner.organizationId), workspaceId = scopeId(owner.workspaceId);
    return { ...actor, organizationId, workspaceId,
      api: new ApiClient(this.requests.get(actor.email)!, E2E.apiUrl)
        .withAuth(actor.tokens, organizationId, workspaceId) };
  }

  /** Opt-in: existing harness cases retain their original single-org memberships.
   * Source pin at 69567785f: accounts/serializers/rbac.py InviteCreateSerializer;
   * views/rbac_views.py InviteCreateAPIView.post/_dual_write_legacy (164-369).
   * Existing ACTIVE users are accepted immediately, with the same User row and
   * tokens. AcceptInvitationRequestSerializer (contracts.py) is ONLY for inactive users;
   * calling that password-setting endpoint here would replace real authority.
   */
  async provisionTwoOrganizationMember(): Promise<{ a: ScopeActor; b: ScopeActor; foreign: ScopeActor }> {
    if (this.memberInB) throw new Error('H1 two-organization membership already provisioned');
    this.foreign = await this.owner(`${this.prefix}-foreign`);
    const invited = await this.send<{ result: { invited: string[] } }>(
      this.ownerB, 'POST', '/accounts/organization/invite/', {
        emails: [this.member.email], org_level: 3,
        workspace_access: [{ workspace_id: this.ownerB.workspaceId, level: 3 }],
      });
    expect(invited.status).toBe(200);
    // Boolean avoids logging even test-owned email addresses on a failed check.
    expect(invited.body.result.invited.length === 1
      && invited.body.result.invited[0] === this.member.email).toBe(true);
    this.memberInB = this.inOrganization(this.member, this.ownerB);
    for (const actor of [this.member, this.memberInB]) {
      const info = await this.send<ScopeUserInfo>(actor, 'GET', '/accounts/user-info/');
      expect(info.status).toBe(200);
      expect(scopeId(info.body.id)).toBe(this.member.userId);
      expect(scopeId(info.body.organization.id)).toBe(actor.organizationId);
      expect(info.body.org_level).toBe(3);
    }
    return { a: this.member, b: this.memberInB, foreign: this.foreign };
  }

  /** Additional non-admin members in owner A's default workspace. */
  async inviteMember(label: string): Promise<ScopeActor> {
    return this.invite(label, 3, [this.ownerA.workspaceId]);
  }

  async openContext(browser: Browser, actor: ScopeActor,
    options: ScopeContextOptions = {}): Promise<BrowserContext> {
    const baseURL = ownedScopeOrigin(options.appUrl ?? this.options.appUrl ?? E2E.appUrl);
    const context = await browser.newContext({ baseURL,
      ignoreHTTPSErrors: options.ignoreHTTPSErrors ?? this.options.ignoreHTTPSErrors ?? false,
      serviceWorkers: 'block' });
    this.contexts.add(context);
    context.on('close', () => this.contexts.delete(context));
    await context.addInitScript(authInitScript, { access: actor.tokens.access, refresh: actor.tokens.refresh,
      organizationId: scopeId(actor.organizationId), workspaceId: scopeId(actor.workspaceId) });
    // WorkspaceContext.jsx:176 rejects stored workspace IDs without the org marker.
    await context.addInitScript(orgId => sessionStorage.setItem('workspaceOrgId', orgId), actor.organizationId);
    return context;
  }

  evidence() {
    // Only typed public identities/booleans. No emails, credentials, invite links,
    // full responses, or caller-supplied names in attachments.
    return { emptyWorkspaceId: this.emptyWorkspace ? scopeId(this.emptyWorkspace.id) : null,
      limitations: [...this.limitations],
      actors: [this.ownerA, this.ownerB, this.member, this.viewer, this.memberInB, this.foreign]
        .filter((actor): actor is ScopeActor => Boolean(actor)).map(
          ({ userId, organizationId, workspaceId }) => ({ userId: scopeId(userId),
            organizationId: scopeId(organizationId), workspaceId: scopeId(workspaceId) })),
      requests: this.receipts.map(receipt => ({ ...receipt,
        organizationId: receipt.organizationId ? scopeId(receipt.organizationId) : null,
        workspaceId: receipt.workspaceId ? scopeId(receipt.workspaceId) : null })) };
  }

  async dispose(): Promise<void> {
    for (const context of this.contexts) await context.close();
    for (const req of this.requests.values()) await req.dispose();
  }
}

// Separate, test-scoped fixture: no mutation of lib/fixtures.ts or its worker actor.
// Browser is deliberately NOT a dependency; API-only selection launches none.
export const test = base.extend<{ scopeActors: ScopeActors; scopeProbe: StateProbe }>({
  scopeActors: async ({}, use, testInfo) => {
    const scopes = new ScopeActors(`e2e-h1-${testInfo.workerIndex}-${Date.now().toString(36)}`);
    try {
      await scopes.provision();
      await testInfo.attach('seeded-scopes', { contentType: 'application/json', body: JSON.stringify(scopes.evidence()) });
      await use(scopes);
    } finally {
      await testInfo.attach('scope-api-receipt', { contentType: 'application/json', body: JSON.stringify(scopes.evidence()) });
      await scopes.dispose();
    }
  },
  scopeProbe: async ({ scopeActors }, use) => {
    const probe = new StateProbe({ api: scopeActors.ownerA.api,
      pgUrl: E2E.pgUrl, chUrl: E2E.chUrl, chDatabase: E2E.chDatabase, chPassword: E2E.chPassword,
      catalogChUrl: E2E.catalogChUrl, catalogChDatabase: E2E.catalogChDatabase });
    try { await use(probe); } finally { await probe.dispose(); }
  },
});
export { expect };
