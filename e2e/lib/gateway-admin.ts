// The gateway's admin API (`/-/...`, agentcc-gateway/internal/server/server.go
// routes) in one place, with the token default and the 404 handling. Reads only,
// plus the one PUT a fallback proof needs. Local stacks only: the token is the compose
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

  /** GET /-/orgs/{org}/config (admin_orgs.go GetOrgConfig, redacted); null when the gateway holds none (404). */
  async orgConfig(orgId: string): Promise<Record<string, any> | null> {
    const path = `/-/orgs/${encodeURIComponent(orgId)}/config`;
    const res = await this.send<Record<string, any>>('get', path);
    if (res.status === 404) return null;
    if (res.status !== 200) throw new GatewayAdminError(res.status, path, res.body);
    return res.body;
  }

  /** PUT /-/orgs/{org}/config (SetOrgConfig, unknown fields refused): a fallback proof only. */
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

  /** GET /-/keys/{id} (admin_keys.go GetKey): 404 key_not_found once the gateway does not hold it. */
  key(id: string): Promise<AdminResponse> { return this.send('get', `/-/keys/${encodeURIComponent(id)}`); }

  dispose(): Promise<void> { return this.ctx.dispose(); }
}

const orgQuery = (orgId?: string) => (orgId === undefined ? '' : `?org_id=${encodeURIComponent(orgId)}`);
