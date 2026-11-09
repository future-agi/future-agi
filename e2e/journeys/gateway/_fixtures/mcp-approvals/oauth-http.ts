import { randomBytes } from 'node:crypto';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { body, cookie, escapeHtml, html, json, parseJson, singleValues } from './local-http.ts';
import type { Authorization, OAuthState } from './oauth-state.ts';
import type { Counters } from './counters.ts';

/** Source pin da4c81cc62: mcpmock/oauth.go registerOAuth + authorize.go
 * consentPage/redirect: PRM, RFC8414 metadata, actual HTML consent POST and
 * RFC9207 iss/state redirect. No browser request interception or fake callback.
 * /register and /revoke are fixture-only additions (see oauth-state.ts).
 * Consent cookie checking is fixture hardening, NOT product cookie proof.
 */
export function createOAuthHandler(state: OAuthState, counters: Counters) {
  const consents = new Map<string, { grant: Authorization; expires: number }>();
  return async (request: IncomingMessage, response: ServerResponse, url: URL): Promise<boolean> => {
    const origin = url.origin, resource = `${origin}/mcp`;
    if (request.method === 'GET' && ['/.well-known/oauth-protected-resource',
      '/.well-known/oauth-protected-resource/mcp'].includes(url.pathname)) {
      json(response, 200, { resource, authorization_servers: [origin],
        bearer_methods_supported: ['header'], scopes_supported: ['mcp'] });
      return true;
    }
    if (request.method === 'GET' && url.pathname === '/.well-known/oauth-authorization-server') {
      json(response, 200, { issuer: origin, authorization_endpoint: `${origin}/authorize`,
        token_endpoint: `${origin}/token`, registration_endpoint: `${origin}/register`,
        revocation_endpoint: `${origin}/revoke`, response_types_supported: ['code'],
        grant_types_supported: ['authorization_code', 'refresh_token'],
        code_challenge_methods_supported: ['S256'], token_endpoint_auth_methods_supported: ['none'],
        scopes_supported: ['mcp'], authorization_response_iss_parameter_supported: true });
      return true;
    }
    if (url.pathname === '/register' && request.method === 'POST') {
      if (request.headers['content-type']?.split(';')[0] !== 'application/json') {
        json(response, 415, { error: 'invalid_request' }); return true;
      }
      const result = state.register(parseJson(await body(request)));
      json(response, result.status, result.body); return true;
    }
    if (['/token', '/revoke'].includes(url.pathname) && request.method === 'POST') {
      if (request.headers['content-type']?.split(';')[0] !== 'application/x-www-form-urlencoded') {
        json(response, 415, { error: 'invalid_request' }); return true;
      }
      const fields = singleValues(new URLSearchParams(await body(request)));
      const result = url.pathname === '/token' ? state.token(fields) : state.revoke(fields);
      json(response, result.status, result.body); return true;
    }
    if (url.pathname !== '/authorize') return false;
    if (request.method === 'GET') {
      const grant = state.validateAuthorization(singleValues(url.searchParams));
      if (!grant) { json(response, 400, { error: 'invalid_request' }); return true; }
      for (const [id, pending] of consents) if (pending.expires <= Date.now()) consents.delete(id);
      const nonce = randomBytes(32).toString('base64url');
      consents.set(nonce, { grant, expires: Date.now() + 600_000 });
      response.setHeader('Set-Cookie', `j14_consent=${nonce}; HttpOnly; SameSite=Lax; Path=/authorize`);
      // Grant is bound server-side. Only nonce and decision are submitted; no
      // caller-editable principal/redirect gets trusted on consent completion.
      html(response, `<!doctype html><html><head><title>Fixture consent</title></head><body>
        <h1>Local MCP fixture consent</h1><p>Synthetic principal ${escapeHtml(grant.principalId)}</p>
        <form method="post" action="/authorize"><input type="hidden" name="consent" value="${nonce}">
        <button type="submit" name="decision" value="approve">Approve</button>
        <button type="submit" name="decision" value="deny">Deny</button></form></body></html>`, grant.redirect);
      return true;
    }
    if (request.method !== 'POST') { json(response, 405, { error: 'invalid_request' }); return true; }
    if (request.headers['content-type']?.split(';')[0] !== 'application/x-www-form-urlencoded' ||
        (request.headers.origin !== undefined && request.headers.origin !== origin)) {
      json(response, 400, { error: 'invalid_request' }); return true;
    }
    const fields = singleValues(new URLSearchParams(await body(request)));
    const nonce = cookie(request, 'j14_consent');
    const pending = nonce && consents.get(nonce);
    if (!nonce || fields.consent !== nonce || !pending || pending.expires <= Date.now() ||
        !['approve', 'deny'].includes(fields.decision) || Object.keys(fields).sort().join(',') !== 'consent,decision') {
      counters.record(null, 'authorize', 'refused');
      json(response, 400, { error: 'invalid_request' }); return true;
    }
    consents.delete(nonce);
    response.setHeader('Set-Cookie', 'j14_consent=; HttpOnly; SameSite=Lax; Path=/authorize; Max-Age=0');
    const code = state.authorize(pending.grant, fields.decision === 'approve');
    const redirect = new URL(pending.grant.redirect);
    redirect.searchParams.set('state', pending.grant.state);
    redirect.searchParams.set('iss', origin);
    redirect.searchParams.set(code ? 'code' : 'error', code ?? 'access_denied');
    response.writeHead(302, { Location: redirect.href, 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer' });
    response.end();
    return true;
  };
}
