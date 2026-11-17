import { randomBytes, createHash } from 'node:crypto';
import { loopbackUrl } from './local-http.ts';
import type { Counters } from './counters.ts';

const opaque = () => randomBytes(32).toString('base64url');
type Grant = { principalId: string; clientId: string; resource: string; scope: string; family: string };
type Code = Omit<Grant, 'family'> & { redirect: string; challenge: string; expires: number };
type Refresh = Grant & { used: boolean };
type Access = Grant & { expires: number };
export type OAuthResult = { status: number; body: Record<string, unknown> };
export type Authorization = { clientId: string; redirect: string; state: string; principalId: string;
  resource: string; scope: string; challenge: string };
const error = (code: string): OAuthResult => ({ status: 400, body: { error: code } });

/** Behavioral pin da4c81cc623f909ce38f53e01b557ee8374d5adb:
 * agentcc-gateway/cmd/agentcc-mcpmock/mcpmock/{oauth,authorize}.go:
 * authorize/token parameters, opaque grants, S256, 600s code / 900s access,
 * resource/client/redirect binding and rotating refresh replay-family refusal.
 * This fixture additionally binds access tokens to revoked families and checks
 * foreign client before replay revocation; it does NOT certify product behavior.
 *
 * DCR and RFC7009 below are NEW SYNTHETIC FIXTURE contracts, not guessed product
 * APIs. DCR field names client_id/redirect_uris/application_type and kind=dcr
 * are pinned to futureagi/agentcc/models/mcp_oauth.py. /register and /revoke are
 * fixture-owned paths advertised by oauth-http.ts; product wire binding is OWED
 * because urls_mcp_{connect,oauth}.py are empty at the prep pin.
 */
export function createOAuthState(counters: Counters, redirectUris: readonly string[], now = Date.now) {
  if (!redirectUris.length || new Set(redirectUris).size !== redirectUris.length) throw new Error('Require unique native callback allowlist');
  const allowedRedirects = new Set(redirectUris.map(raw => {
    const url = loopbackUrl(raw);
    if (url.search || url.pathname !== '/callback') throw new Error('Expected native fixture callback');
    return url.href;
  }));
  const clients = new Map<string, readonly string[]>();
  const codes = new Map<string, Code>();
  const refresh = new Map<string, Refresh>();
  const access = new Map<string, Access>();
  const revoked = new Set<string>();
  let resource = '';
  const issue = (grant: Grant): OAuthResult => {
    const accessToken = opaque(), refreshToken = opaque();
    access.set(accessToken, { ...grant, expires: now() + 900_000 });
    refresh.set(refreshToken, { ...grant, used: false });
    return { status: 200, body: { access_token: accessToken, token_type: 'Bearer',
      expires_in: 900, refresh_token: refreshToken, scope: grant.scope } };
  };
  return {
    bindResource(value: string) {
      if (resource) throw new Error('OAuth fixture already bound');
      resource = loopbackUrl(value).href;
    },
    register(input: Record<string, unknown>): OAuthResult {
      const redirects = input.redirect_uris;
      const valid = Object.keys(input).sort().join(',') === 'application_type,redirect_uris' &&
        input.application_type === 'native' && Array.isArray(redirects) && redirects.length > 0 &&
        redirects.length <= 8 && new Set(redirects).size === redirects.length &&
        redirects.every(value => typeof value === 'string' && allowedRedirects.has(value));
      counters.record(null, 'register', valid ? 'accepted' : 'refused');
      if (!valid) return error('invalid_redirect_uri');
      const clientId = `fixture-client-${opaque()}`;
      clients.set(clientId, [...redirects] as string[]);
      return { status: 201, body: { client_id: clientId, redirect_uris: [...redirects],
        application_type: 'native', token_endpoint_auth_method: 'none',
        grant_types: ['authorization_code', 'refresh_token'], response_types: ['code'] } };
    },
    validateAuthorization(input: Record<string, string>): Authorization | null {
      const valid = resource && input.response_type === 'code' &&
        clients.get(input.client_id)?.includes(input.redirect_uri) &&
        input.code_challenge_method === 'S256' && /^[A-Za-z0-9_-]{43}$/.test(input.code_challenge ?? '') &&
        input.resource === resource && input.scope === 'mcp' &&
        typeof input.state === 'string' && input.state.length >= 16 && input.state.length <= 256 &&
        counters.acceptsPrincipal(input.login_hint);
      if (!valid) { counters.record(null, 'authorize', 'refused'); return null; }
      return { clientId: input.client_id, redirect: input.redirect_uri, state: input.state,
        principalId: input.login_hint, resource, scope: input.scope, challenge: input.code_challenge };
    },
    authorize(grant: Authorization, approved: boolean): string | null {
      counters.record(grant.principalId, 'authorize', approved ? 'accepted' : 'refused');
      if (!approved) return null;
      const code = opaque();
      codes.set(code, { ...grant, expires: now() + 600_000 });
      return code;
    },
    token(input: Record<string, string>): OAuthResult {
      if (input.grant_type === 'authorization_code') {
        const grant = codes.get(input.code);
        codes.delete(input.code); // Source behavior: consume even on failed exchange.
        const valid = grant && now() < grant.expires && grant.clientId === input.client_id &&
          grant.redirect === input.redirect_uri && /^[A-Za-z0-9._~-]{43,128}$/.test(input.code_verifier ?? '') &&
          createHash('sha256').update(input.code_verifier).digest('base64url') === grant.challenge;
        if (!valid) { counters.record(grant?.principalId ?? null, 'code', 'refused'); return error('invalid_grant'); }
        if (input.resource && input.resource !== grant.resource) {
          counters.record(grant.principalId, 'code', 'refused'); return error('invalid_target');
        }
        counters.record(grant.principalId, 'code', 'accepted');
        return issue({ ...grant, family: opaque() });
      }
      if (input.grant_type === 'refresh_token') {
        const grant = refresh.get(input.refresh_token);
        if (!grant || grant.clientId !== input.client_id || revoked.has(grant.family)) {
          counters.record(grant?.principalId ?? null, 'refresh', 'refused'); return error('invalid_grant');
        }
        if (grant.used) {
          revoked.add(grant.family); counters.record(grant.principalId, 'refresh', 'refused');
          return error('invalid_grant');
        }
        if (input.resource && input.resource !== grant.resource) {
          counters.record(grant.principalId, 'refresh', 'refused'); return error('invalid_target');
        }
        grant.used = true; // Synchronous transition; concurrent HTTP requests cannot both redeem.
        counters.record(grant.principalId, 'refresh', 'accepted');
        return issue(grant);
      }
      return error('unsupported_grant_type');
    },
    revoke(input: Record<string, string>): OAuthResult {
      const grant = refresh.get(input.token) ?? access.get(input.token);
      if (!clients.has(input.client_id)) {
        counters.record(null, 'revoke', 'refused'); return error('invalid_client');
      }
      const owned = grant?.clientId === input.client_id;
      counters.record(owned ? grant.principalId : null, 'revoke', 'accepted');
      if (owned) revoked.add(grant.family);
      // RFC7009 fixture: unknown/foreign token is deliberately opaque, no leak.
      return { status: 200, body: {} };
    },
    principal(token: string): { principalId: string | null; valid: boolean } {
      const grant = access.get(token);
      return { principalId: grant?.principalId ?? null, valid: !!grant &&
        grant.resource === resource && now() < grant.expires && !revoked.has(grant.family) };
    },
    dispose() { clients.clear(); codes.clear(); refresh.clear(); access.clear(); revoked.clear(); },
  };
}
export type OAuthState = ReturnType<typeof createOAuthState>;
