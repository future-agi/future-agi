// H6: the gateway's admin API (`/-/...`, agentcc-gateway/internal/server/server.go
// routes) in one place, so flows stop copying the token default and the 404
// handling (GW-E2E-002, -005, -007, -008 call it inline). Reads only, plus the
// PUT the bc3 fallback proof needs and the credits-granted push GW-E2E-020
// seeds Future AGI credit with. Local stacks only: the token is the compose
// files' published dev token, which e2e/stack/*.env run with
// AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN=1.
import { request, type APIRequestContext } from '@playwright/test';
import { E2E } from './env';

export const GATEWAY_ADMIN_TOKEN = process.env.AGENTCC_ADMIN_TOKEN || 'local-dev-only-admin-token-replace-me';

export interface AdminResponse<T = unknown> { status: number; body: T }

/** An admin call answered with a status the caller did not expect. Never carries the token. */
export class GatewayAdminError extends Error {
  constructor(readonly status: number, readonly path: string, readonly body: unknown) {
    super(`gateway admin ${status} on ${path}: ${JSON.stringify(body).slice(0, 300)}`);
  }
}

const LOCAL = new Set(['localhost', '127.0.0.1', '[::1]']);

export class GatewayAdmin {
  private constructor(private readonly ctx: APIRequestContext) {}

  /** `baseURL` must be a local gateway: the admin token is never sent anywhere else. */
  static async open(token = GATEWAY_ADMIN_TOKEN, baseURL = E2E.gatewayUrl): Promise<GatewayAdmin> {
    if (!LOCAL.has(new URL(baseURL).hostname)) throw new Error('GatewayAdmin talks to a local gateway only');
    return new GatewayAdmin(await request.newContext({ baseURL,
      extraHTTPHeaders: { Authorization: `Bearer ${token}` } }));
  }

  private async send<T>(method: 'get' | 'post' | 'put', path: string, data?: unknown): Promise<AdminResponse<T>> {
    const res = await this.ctx[method](path, data === undefined ? {} : { data });
    const raw = await res.text();
    let body: unknown;
    try { body = raw ? JSON.parse(raw) : null; } catch { body = { raw }; }
    return { status: res.status(), body: body as T };
  }

  private async expectOk<T>(method: 'get' | 'put', path: string, data?: unknown): Promise<T> {
    const res = await this.send<T>(method, path, data);
    if (res.status !== 200) throw new GatewayAdminError(res.status, path, res.body);
    return res.body;
  }

  /** GET /-/config (server.go configHandler): server, logging, batch, guardrail_pipeline, org_sections_enforced. */
  config(): Promise<Record<string, any>> { return this.expectOk('get', '/-/config'); }

  /** catalog_admin.go:handleCatalog, GET /-/catalog, models[].id (no paging). */
  catalog(): Promise<{ models: { id: string; state?: string; [field: string]: unknown }[] }> { return this.expectOk('get', '/-/catalog'); }

  /** GET /-/orgs/{org}/config (admin_orgs.go GetOrgConfig, redacted); null when the gateway holds none (404). */
  async orgConfig(orgId: string): Promise<Record<string, any> | null> {
    const path = `/-/orgs/${encodeURIComponent(orgId)}/config`;
    const res = await this.send<Record<string, any>>('get', path);
    if (res.status === 404) return null;
    if (res.status !== 200) throw new GatewayAdminError(res.status, path, res.body);
    return res.body;
  }

  /** PUT /-/orgs/{org}/config (SetOrgConfig, unknown fields refused): the bc3 fallback proof only. */
  async putOrgConfig(orgId: string, cfg: unknown): Promise<void> {
    await this.expectOk('put', `/-/orgs/${encodeURIComponent(orgId)}/config`, cfg);
  }

  /** GET /-/batches/{id}: only the batch's own org finds it (getBatchHandler), so pass that org's id. */
  batch(id: string, orgId?: string): Promise<AdminResponse> {
    return this.send('get', `/-/batches/${encodeURIComponent(id)}${orgQuery(orgId)}`);
  }

  /** POST /-/batches/{id}/cancel (cancelBatchHandler), scoped like batch(). */
  cancelBatch(id: string, orgId?: string): Promise<AdminResponse> {
    return this.send('post', `/-/batches/${encodeURIComponent(id)}/cancel${orgQuery(orgId)}`);
  }

  /** GET /-/orgs/{org}/credits (credits_admin.go handleOrgCredits, credits-v1): the org's Future AGI
   * credit account as the gateway holds it; an org never pushed reads as zeros. */
  credits(orgId: string): Promise<OrgCredits> {
    return this.expectOk('get', `/-/orgs/${encodeURIComponent(orgId)}/credits`);
  }

  /** PUT /-/orgs/{org}/credits/granted (credits_admin.go handleOrgCreditsGranted,
   * credits-granted-push-v1): the push Django makes after a grant
   * (ee/usage/services/managed_credits.py push_granted). The gateway applies the
   * granted total only when `version` is newer than the one it holds, so a seed
   * whose version is above any Django will push (its account version counts
   * grants from 0) survives a later push from Django. Seeding only: Postgres
   * keeps no grant entry for it. */
  pushGrantedCredits(orgId: string, push: GrantedPush): Promise<GrantedPushResult> {
    return this.expectOk('put', `/-/orgs/${encodeURIComponent(orgId)}/credits/granted`, push);
  }

  /** GET /-/keys/{id} (admin_keys.go GetKey): 404 key_not_found once the gateway no longer holds it. */
  key(id: string): Promise<AdminResponse> { return this.send('get', `/-/keys/${encodeURIComponent(id)}`); }

  /** The node's served MCP state (older nodes ignore org_id). 404 means not served.
   * Org defaults must be read separately with orgConfig, never inferred from this flag. */
  mcpStatus(orgId: string): Promise<AdminResponse<{ enabled?: boolean; servers?: unknown[] }>> {
    return this.send('get', `/-/mcp/status${orgQuery(orgId)}`);
  }

  /** Whether the gateway serves MCP, read from its org-scoped MCP admin route with
   * no org named (routes_mcp_admin.go): registered only with mcp.enabled, it then
   * answers 400 missing_org_id; with MCP off no /-/mcp/* route exists and the
   * router answers 404 not_found (router.go). Anything else throws. */
  async mcpServed(): Promise<boolean> {
    const path = '/-/mcp/status';
    const res = await this.send<{ error?: { code?: string } }>('get', path);
    if (res.status === 400 && res.body?.error?.code === 'missing_org_id') return true;
    if (res.status === 404 && res.body?.error?.code === 'not_found') return false;
    throw new GatewayAdminError(res.status, path, res.body);
  }

  dispose(): Promise<void> { return this.ctx.dispose(); }
}

/** credits-v1 Admin (credits_admin.go creditsAdminView). */
export interface OrgCredits {
  org_id: string; granted_total_micros: number; granted_version: number; spent_micros: number;
  held_micros: number; available_micros: number; balance_usd: number; markup_percent: number;
  outbox_pending: number; ledger: string;
}
/** credits-granted-push-v1's body: every field required and non-negative. */
export interface GrantedPush { granted_total_micros: number; version: number; settled_spend_floor_micros: number }
/** credits-granted-push-v1's response (credits_admin.go grantedPushView). */
export interface GrantedPushResult {
  applied: boolean; granted_total_micros: number; granted_version: number; spent_micros: number;
  held_micros: number; available_micros: number;
}

const orgQuery = (orgId?: string) => (orgId === undefined ? '' : `?org_id=${encodeURIComponent(orgId)}`);
