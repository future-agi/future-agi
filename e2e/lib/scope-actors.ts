import { createHash, randomBytes } from 'node:crypto';
import { expect, request as pwRequest } from '@playwright/test';
import type { APIRequestContext, Browser, BrowserContext, BrowserContextOptions } from '@playwright/test';
import pg from 'pg';
import { ApiClient, Tokens } from './api-client';
import { authInitScript } from './auth';
import { E2E } from './env';
import { mint } from './minted-values';
import { provisionActor, TestActor } from './provisioning';
import { StateProbe } from './state-probe';
import { assertValueFreeRun, awaitPublicationAdmission, bindValueFree, displayedKeys, requirePublicationAdmission, scrubLogs, sharedValueFree,
  valueFreeFromEnv, valueFreeRequested, ValueFreeRefusal, type Refusal, type ValueFree } from './value-free';
import { test as base } from './trace-hygiene/fixture';

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
/** Organization levels (futureagi/tfc/constants/levels.py): Viewer 1, Member 3, Admin 8, Owner 15.
 * Owners sign up (their org is created with them); every other level is invited. */
export type ScopeLevel = 1 | 3 | 8 | 15;
export type InvitedLevel = Exclude<ScopeLevel, 15>;
/** H2: who a test needs beyond the default Owner A, Owner B, Member and Viewer.
 * Each extra actor is one more public invite and acceptance, so a flow asks only
 * for what it uses. The default ({}) provisions exactly what it always did. */
export interface ScopeRoster {
  /** An Admin (8) of org A, with workspace-admin access to A's default workspace. */
  admin?: boolean;
  /** A second Member (3) of org A (W08's A1 and A2). */
  secondMember?: boolean;
  /** Members of org B at these levels, invited by Owner B into B's default workspace. */
  orgB?: InvitedLevel[];
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
  /** Present only when the roster asks for them (ScopeRoster). */
  admin?: ScopeActor;
  member2?: ScopeActor;
  readonly orgBMembers: ScopeActor[] = [];
  emptyWorkspace!: ScopeWorkspace;
  readonly owners: TestActor[] = [];
  readonly receipts: ScopeRequestReceipt[] = [];
  readonly limitations: string[] = [];
  private readonly requests = new Map<string, APIRequestContext>();
  private readonly contexts = new Set<BrowserContext>();
  /** HR-JRN-01 (lib/value-free.ts): set when evidence() publishes tokens instead of values. */
  readonly valueFree: ValueFree | null;
  /** The domain of every email this set mints. HR-JRN-01 round 10 (708 add.68): value-free, it is `<prefix>.test`, so the
   * org signup names after the domain's first label (accounts/utils.py first_signup) is this set's own prefix, never a
   * fixed word: no value the set mints and registers shares a substring with the checkout's path (the walk rig's own
   * channels carry it), harness/value-free-disjoint.spec.ts. Off, futureagi.com, as it was. */
  readonly domain: string;

  constructor(readonly prefix: string, readonly roster: ScopeRoster = {}, options: { valueFree?: boolean } = {}) {
    // This fixture is approved for the owned local stack only.
    for (const endpoint of [E2E.apiUrl, E2E.appUrl]) {
      if (new URL(endpoint).hostname !== 'localhost') throw new Error('H1 requires localhost endpoints');
    }
    this.valueFree = valueFreeRequested(options.valueFree) ? sharedValueFree().register(prefix) : null;
    this.domain = this.valueFree ? `${prefix.toLowerCase()}.test` : 'futureagi.com';
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
      method, data, params, headers: actor ? this.headers(actor) : {}, maxRedirects: 0,
    });
    this.receipts.push({ method, path: path.replace(/accept-invitation\/.+/, 'accept-invitation/<redacted>/'),
      status: response.status(), organizationId: actor?.organizationId ?? null,
      workspaceId: actor?.workspaceId ?? null, authenticated: Boolean(actor) });
    return { status: response.status(), body: await response.json() as T };
  }

  private async newRequest(emailKey: string): Promise<APIRequestContext> {
    const req = await pwRequest.newContext({ baseURL: E2E.apiUrl });
    this.requests.set(emailKey, req);
    return req;
  }

  private async owner(label: string): Promise<ScopeActor> {
    const req = await this.newRequest(label);
    const owner = await provisionActor(req, label, undefined, this.domain);
    this.valueFree?.registerAs('credential', owner.password, owner.tokens.access, owner.tokens.refresh);
    this.requests.delete(label);
    this.requests.set(owner.email, req);
    this.owners.push(owner);
    const actor: ScopeActor = { userId: '', email: owner.email, tokens: owner.tokens,
      organizationId: owner.organizationId, workspaceId: owner.workspaceId, api: owner.api };
    const info = await this.send<ScopeUserInfo>(actor, 'GET', '/accounts/user-info/');
    expect(info.status).toBe(200);
    expect(info.body.organization.id).toBe(actor.organizationId);
    actor.userId = info.body.id;
    return actor;
  }

  // Optional, so existing flows keep their original provisioning footprint.
  // Admin is level 8 in both organization and workspace roles, as in W09.
  async provisionAdmin(): Promise<ScopeActor> {
    this.admin ??= await this.invite('admin', 8, [this.ownerA.workspaceId]);
    return this.admin;
  }

  private wsAdmin?: ScopeActor;

  /** A workspace Admin of ownerA's workspace: organization Member (level 3), workspace level 8
   * (futureagi/tfc/constants/levels.py Level.WORKSPACE_ADMIN). Invited on first use, so only the
   * flows that ask for one pay for it. */
  async workspaceAdmin(): Promise<ScopeActor> {
    this.wsAdmin ??= await this.invite('wsadmin', 3, [this.ownerA.workspaceId], this.ownerA, 8);
    return this.wsAdmin;
  }

  private async invite(label: string, level: InvitedLevel, workspaceIds: string[],
    inviter: ScopeActor = this.ownerA, workspaceLevel: number = level): Promise<ScopeActor> {
    // accounts/serializers/rbac.py:34 (org_level in Level.CHOICES; workspace level
    // in Level.WORKSPACE_CHOICES, where Admin is 8 too) and views/rbac_views.py:238.
    const email = `${this.prefix}-${label}@${this.domain}`;
    const invited = await this.send<{ result: { invited: string[];
      invites?: { email: string; invite_link: string }[] } }>(
      inviter, 'POST', '/accounts/organization/invite/', {
        emails: [email], org_level: level,
        workspace_access: workspaceIds.map(workspace_id => ({ workspace_id, level: workspaceLevel })),
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
    this.valueFree?.registerAs('invite link', link);
    if (link.startsWith('None/auth/')) this.limitations.push('APP_URL unset: public invite link starts None/auth/; direct link navigation not qualified');
    const match = new URL(link.startsWith('None/auth/') ? link.slice(4) : link, E2E.appUrl).pathname
      .match(/^\/auth\/jwt\/invitation\/accept\/([^/]+)\/([^/]+)\/?$/);
    if (!match) throw new Error('H1 blocked: unexpected public invitation link shape');
    mint(match[2]);
    this.valueFree?.registerAs('invite token', match[1], match[2]);
    const req = await this.newRequest(email);
    const path = `/accounts/accept-invitation/${match[1]}/${match[2]}/`;
    const preview = await this.sendOn<{ valid: boolean; email: string }>(req, 'GET', path);
    expect(preview.status).toBe(200);
    expect(preview.body).toMatchObject({ valid: true, email });
    // accounts/serializers/contracts.py:414; views/signup.py:724,806.
    const password = `H1-${this.prefix}-${label}!`;
    mint(password);
    this.valueFree?.registerAs('credential', password);
    const accepted = await this.sendOn<Tokens>(req, 'POST', path,
      { new_password: password, repeat_password: password });
    expect(accepted.status).toBe(200);
    expect(accepted.body.access).toBeTruthy();
    expect(accepted.body.refresh).toBeTruthy();
    const tokens = { access: accepted.body.access, refresh: accepted.body.refresh };
    mint(tokens.access, tokens.refresh);
    this.valueFree?.registerAs('credential', tokens.access, tokens.refresh);
    const actor: ScopeActor = { userId: '', email, tokens,
      organizationId: inviter.organizationId, workspaceId: workspaceIds[0],
      api: new ApiClient(req, E2E.apiUrl).withAuth(tokens, inviter.organizationId, workspaceIds[0]) };
    const info = await this.send<ScopeUserInfo>(actor, 'GET', '/accounts/user-info/');
    expect(info.status).toBe(200);
    expect(info.body).toMatchObject({ email, organization: { id: actor.organizationId }, org_level: level });
    actor.userId = info.body.id;
    return actor;
  }

  async provision(): Promise<void> {
    // HR-JRN-01 structural S3: the shared dependency every walk (W00 first) provisions through; nothing before admission.
    await requirePublicationAdmission('ScopeActors.provision');
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
    if (this.roster.admin) this.admin = await this.invite('admin', 8, [this.ownerA.workspaceId]);
    if (this.roster.secondMember) this.member2 = await this.invite('member2', 3, [this.ownerA.workspaceId]);
    for (const [i, level] of (this.roster.orgB ?? []).entries()) {
      this.orgBMembers.push(await this.invite(`b${i}-l${level}`, level, [this.ownerB.workspaceId], this.ownerB));
    }
    await this.registerSeeded();
  }

  private actors(): ScopeActor[] {
    return [this.ownerA, this.ownerB, this.member, this.viewer, this.admin, this.member2, this.wsAdmin, ...this.orgBMembers]
      .filter((a): a is ScopeActor => Boolean(a));
  }

  /** HR-JRN-01, value-free only: every identity value Postgres holds for the orgs this set seeded (each actor's org
   * and every org they belong to), read back, not taken from what a test happens to use: user ids, emails, local
   * parts and names; org ids and names; every workspace's id, name and display name (the owners' default ones too);
   * every org key whole, as its 8-character prefix and masked. Registered before anything can quote them. provision()
   * ends with it and the fixture runs it again at teardown, for what the test created since. One too short to hide
   * refuses the run (ValueFreeRefusal). */
  async registerSeeded(): Promise<void> {
    const vf = this.valueFree;
    const actors = this.actors();
    if (!vf || !actors.length) return;
    const client = new pg.Client({ connectionString: E2E.pgUrl });
    await client.connect();
    const refused: Refusal[] = [];
    const add = (what: string, values: (string | null | undefined)[]) => {
      try { vf.registerAs(what, ...values); } catch (error) {
        if (!(error instanceof ValueFreeRefusal)) throw error;
        refused.push(...error.refusals);
      }
    };
    try {
      const rows = async <T extends pg.QueryResultRow>(sql: string, params: string[][]) => (await client.query<T>(sql, params)).rows;
      const orgs = await rows<{ id: string; name: string; display_name: string }>(`SELECT id::text, name, display_name
        FROM accounts_organization WHERE id = ANY($1::uuid[]) OR id IN (SELECT organization_id
        FROM accounts_organization_membership WHERE user_id = ANY($2::uuid[]))`,
      [[...new Set(actors.map((a) => a.organizationId).filter(Boolean))], actors.map((a) => a.userId).filter(Boolean)]);
      const ids = orgs.map((o) => o.id);
      const people = await rows<{ id: string; email: string; name: string }>(`SELECT id::text, email, name FROM accounts_user
        WHERE organization_id = ANY($1::uuid[]) OR id IN (SELECT user_id FROM accounts_organization_membership
        WHERE organization_id = ANY($1::uuid[]))`, [ids]);
      const workspaces = await rows<{ id: string; name: string; display_name: string }>(
        'SELECT id::text, name, display_name FROM accounts_workspace WHERE organization_id = ANY($1::uuid[])', [ids]);
      const keys = await rows<{ a: string; s: string }>(
        'SELECT api_key AS a, secret_key AS s FROM accounts_orgapikey WHERE organization_id = ANY($1::uuid[])', [ids]);
      add('organization id', ids);
      add('organization name', orgs.flatMap((o) => [o.name, o.display_name]));
      add('user id', people.map((p) => p.id));
      add('email', people.map((p) => p.email));
      add('email local part', people.map((p) => p.email.split('@')[0]));
      add('user name', people.map((p) => p.name));
      add('workspace id', workspaces.map((w) => w.id));
      add('workspace name', workspaces.flatMap((w) => [w.name, w.display_name]));
      add('org key', keys.flatMap((k) => [k.a, k.s, ...displayedKeys(k.a, k.s)]));
    } finally { await client.end(); }
    if (refused.length) throw new ValueFreeRefusal(refused);
  }

  /** Every provisioned actor of org A with its level, Owner first (the census order). */
  orgA(): { level: ScopeLevel; role: string; actor: ScopeActor }[] {
    return [{ level: 15 as const, role: 'owner', actor: this.ownerA },
      ...(this.admin ? [{ level: 8 as const, role: 'admin', actor: this.admin }] : []),
      { level: 3 as const, role: 'member', actor: this.member },
      ...(this.member2 ? [{ level: 3 as const, role: 'member2', actor: this.member2 }] : []),
      { level: 1 as const, role: 'viewer', actor: this.viewer }];
  }

  /** `walk` (H1/H12, lib/walk/): extra context options, and a guard installed on
   * the fresh context before any page or init script exists (the walk boundary). */
  async openContext(browser: Browser, actor: ScopeActor, walk?: { options?: BrowserContextOptions;
    guard?: (context: BrowserContext) => Promise<void> }): Promise<BrowserContext> {
    const context = await browser.newContext({ ...walk?.options, baseURL: E2E.appUrl });
    this.contexts.add(context);
    context.on('close', () => this.contexts.delete(context));
    if (walk?.guard) {
      try {
        await walk.guard(context);
      } catch (error) {
        await context.close();
        throw error;
      }
    }
    await context.addInitScript(authInitScript, { access: actor.tokens.access, refresh: actor.tokens.refresh,
      organizationId: actor.organizationId, workspaceId: actor.workspaceId });
    // WorkspaceContext.jsx:176 rejects stored workspace IDs without the org marker.
    await context.addInitScript(orgId => sessionStorage.setItem('workspaceOrgId', orgId), actor.organizationId);
    return context;
  }

  evidence() {
    const evidence = { prefix: this.prefix, emptyWorkspace: this.emptyWorkspace, limitations: this.limitations,
      actors: [this.ownerA, this.ownerB, this.member, this.viewer, this.admin, this.member2, this.wsAdmin, ...this.orgBMembers]
        .filter((a): a is ScopeActor => Boolean(a)).map(
        ({ userId, email, organizationId, workspaceId }) => ({ userId, email, organizationId, workspaceId })),
      requests: this.receipts };
    if (!this.valueFree) return evidence;
    // Patterns already cover emails, UUIDs, keys and JWTs. Registering also covers
    // ids of any other shape, an email's local part (which names an owner's org), the
    // workspace name, and every key as the product displays it; the run shares them.
    // registerSeeded() adds everything else Postgres holds for these orgs.
    for (const a of evidence.actors) {
      this.valueFree.register(a.email, a.email.split('@')[0], a.userId, a.organizationId, a.workspaceId);
    }
    for (const o of this.owners) {
      this.valueFree.register(o.apiKey, o.secretKey, o.tokens.access, o.tokens.refresh, ...displayedKeys(o.apiKey, o.secretKey));
    }
    return this.valueFree.register(this.emptyWorkspace?.id, this.emptyWorkspace?.name).json(evidence);
  }

  async dispose(): Promise<void> {
    for (const context of this.contexts) await context.close();
    for (const req of this.requests.values()) await req.dispose();
  }
}

// Separate, test-scoped fixture: no mutation of lib/fixtures.ts or its worker actor.
// Browser is deliberately NOT a dependency; API-only selection launches none.
// `scopeRoster` is an option: a file asks for its extra actors with
// test.use({ scopeRoster: { admin: true } }); unset it provisions the default four.
export function scopeAttachment(evidence: unknown, mode: 'raw' | 'digest' = 'raw'): string {
  const body = JSON.stringify(evidence);
  return mode === 'raw' ? body : JSON.stringify({ digest: `sha256:${createHash('sha256').update(body).digest('hex')}` });
}
// `scopeValueFree` (HR-JRN-01, lib/value-free.ts): on, the fixture publishes tokens,
// never values, in its attachments, the walk receipt, logs and the report. Its one
// switch is E2E_VALUE_FREE=1 for the run; the option can confirm it, never replace it.
export const test = base.extend<{ scopeActors: ScopeActors; scopeProbe: StateProbe; scopeRoster: ScopeRoster;
  scopeEvidence: 'raw' | 'digest'; scopeValueFree: boolean }>({
  scopeRoster: [{}, { option: true }],
  scopeEvidence: ['raw', { option: true }],
  scopeValueFree: [valueFreeFromEnv(), { option: true }],
  scopeActors: async ({ scopeRoster, scopeEvidence, scopeValueFree, trace, screenshot, video }, use, testInfo) => {
    if (valueFreeRequested(scopeValueFree)) {
      // R2 (S3): a worker asks its own runner first (lib/value-free.ts), so the check below knows the answer.
      await awaitPublicationAdmission();
      assertValueFreeRun({ trace, screenshot, video });
    }
    // Long and random: every name it makes carries this prefix (value-free mode refuses a value too short to hide).
    const scopes = new ScopeActors(`e2e-h1-${testInfo.workerIndex}-${Date.now().toString(36)}${randomBytes(4).toString('hex')}`,
      scopeRoster, { valueFree: scopeValueFree });
    if (scopes.valueFree) {
      bindValueFree(testInfo, scopes.valueFree);
      scrubLogs();
    }
    try {
      await scopes.provision();
      await testInfo.attach('seeded-scopes', { contentType: 'application/json', body: scopeAttachment(scopes.evidence(), scopeEvidence) });
      await use(scopes);
    } finally {
      try {
        await scopes.registerSeeded(); // value-free: what the test created since (a workspace, a key)
        await testInfo.attach('scope-api-receipt', { contentType: 'application/json', body: scopeAttachment(scopes.evidence(), scopeEvidence) });
      } finally { await scopes.dispose(); }
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
