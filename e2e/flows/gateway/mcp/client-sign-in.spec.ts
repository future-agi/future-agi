import { request } from '@playwright/test';
import type { APIRequestContext, APIResponse } from '@playwright/test';
import { createHash, randomBytes, webcrypto } from 'node:crypto';
import { test, expect } from '../../../lib/scope-actors';
import { E2E } from '../../../lib/env';
import { flowAnnotation } from '../../../lib/flow-meta';
import { profileTag } from '../../../lib/profile';
import { MCP_MOCK } from '../../../lib/public-network';
import { POLL } from '../../../lib/state-probe';

// An MCP client (Claude, Cursor, VS Code) is given one gateway URL, a virtual
// server's /mcp/v/<slug>, and signs a person in through the gateway's
// authorization server (M24, M24x; agentcc/mcp_oauth). This flow is that
// client, driven over HTTP exactly as such a client drives it, with the
// console's consent page played by the signed-in person's own API calls (the
// calls the page makes: agentcc/mcp_oauth/consent_api.py).
//
// The leg through the gateway itself is the client's first and last move: it
// calls the virtual server's URL with no credential and is sent, by the
// gateway's challenge and protected-resource metadata (M23), to the
// authorization server; once signed in it opens an MCP session at that URL with
// the access token, lists the tools and calls one. The token is also verified
// the way M23 verifies it: ES256 against the published keys, iss, aud = the
// resource, typ, and exp - iat <= 900. The gateway takes the verification keys
// from Django's push (agentcc/mcp_oauth/keys.py push_if_due on every token
// issue; PUT /-/mcp/oauth/keys).
//
// Pinned off the code it drives:
// - discovery: /.well-known/oauth-authorization-server/agentcc/mcp-oauth
//   (agentcc/urls_mcp_wellknown.py; tfc/asgi.py hands it to Django);
// - the resource base: Django's public gateway URL, as GET
//   /agentcc/gateways/default/ reports it (base_url minus /v1), plus /mcp
//   (agentcc/mcp_oauth/config.py resource_bases);
// - registration: the payload native MCP clients send (no application_type,
//   a loopback redirect, token_endpoint_auth_method none);
// - the seed: update-mcp-server and update-config {mcp: {virtual_servers}} as
//   AddMCPServerDialog.jsx and virtualServerPatch (virtualServers.js) send them;
// - the gateway leg: a 401 with no credential whose WWW-Authenticate names
//   resource_metadata at /.well-known/oauth-protected-resource/mcp/v/<slug> on
//   the resource's origin (mcp principal.go unauthorized, oauthrs Resource
//   MetadataURL), which serves {resource, authorization_servers} without a
//   credential (server routes_mcp_oauth.go); exposed names <server>__<tool>
//   under the default prefixed naming (vserver/names.go); echo's answer
//   (scenarios fixtures/mcp/common). The client reaches the gateway at the
//   harness port: Django reports the gateway at http://localhost:8090, the
//   address the profile names as the gateway's public URL, which on this host
//   is published as E2E.gatewayUrl.
// Runs on the mcp-signin gateway profile (@gw-profile-mcp-signin): org MCP on,
// and the gateway an OAuth resource server for Django's authorization server
// (lib/profile.ts MCP_SIGNIN_DELTA).
const UPDATE_SERVER = '/agentcc/gateways/default/update-mcp-server/';
const UPDATE_CONFIG = '/agentcc/gateways/default/update-config/';
const GATEWAY = '/agentcc/gateways/default/';
const METADATA = '/.well-known/oauth-authorization-server/agentcc/mcp-oauth';
const REDIRECT = 'http://127.0.0.1:47123/callback';
const PRM_PREFIX = '/.well-known/oauth-protected-resource';
// scopeActors provisioning 120 + the sign-in round trips 60 + GATEWAY_SYNC 75
// (the keys and the virtual server reach the gateway) + MCP calls 20 + headroom 85.
const FLOW_TIMEOUT = 360_000;

interface Envelope<T> { result: T }
interface TokenAnswer {
  access_token: string; token_type: string; expires_in: number; refresh_token: string; scope: string;
}
interface Consent {
  client: { name: string; kind: string; verified: boolean };
  organizations: Array<{ id: string; name: string }>;
  scopes: Array<{ name: string }>;
  resource: string; redirect_host: string; pre_approved: boolean;
}

const b64url = (buf: Buffer) => buf.toString('base64url');

function pkce() {
  const verifier = b64url(randomBytes(48));
  return { verifier, challenge: b64url(createHash('sha256').update(verifier).digest()) };
}

function decodePart(part: string) {
  return JSON.parse(Buffer.from(part, 'base64url').toString('utf8'));
}

// ES256 over the published JWK, as the gateway's verifier checks it.
async function verifiedClaims(req: APIRequestContext, jwksUri: string, token: string) {
  const [h, p, s] = token.split('.');
  const header = decodePart(h);
  expect(header.alg).toBe('ES256');
  const jwks = await (await req.get(jwksUri)).json() as { keys: Array<Record<string, string>> };
  const jwk = jwks.keys.find(k => k.kid === header.kid);
  expect(jwk, 'the token\'s kid is published').toBeTruthy();
  expect(Object.keys(jwk!).filter(k => ['d', 'p', 'q', 'k'].includes(k))).toEqual([]);
  const key = await webcrypto.subtle.importKey('jwk', jwk as webcrypto.JsonWebKey,
    { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
  const ok = await webcrypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, key,
    Buffer.from(s, 'base64url'), Buffer.from(`${h}.${p}`));
  expect(ok, 'the signature verifies against the published key').toBe(true);
  return decodePart(p) as Record<string, string | number>;
}

function form(data: Record<string, string>) {
  return { headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    data: new URLSearchParams(data).toString() };
}

async function json<T>(res: APIResponse): Promise<T> {
  return await res.json() as T;
}

// One JSON-RPC message to the virtual server's URL, as an MCP client sends it
// (Streamable HTTP: a JSON or an SSE answer), with the bearer it holds, if any.
async function mcp(req: APIRequestContext, path: string, bearer: string, session: string | undefined, message: object) {
  const res = await req.post(`${E2E.gatewayUrl}${path}`, {
    headers: {
      ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
      'Content-Type': 'application/json',
      Accept: 'application/json, text/event-stream',
      ...(session ? { 'Mcp-Session-Id': session } : {}),
    },
    data: message,
  });
  const text = await res.text();
  const sse = (res.headers()['content-type'] ?? '').includes('text/event-stream');
  const payload = sse
    ? text.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).pop() ?? ''
    : text;
  return { status: res.status(), session: res.headers()['mcp-session-id'], challenge: res.headers()['www-authenticate'] ?? '',
    body: payload ? JSON.parse(payload) : null };
}

const INITIALIZE = { jsonrpc: '2.0', id: 1, method: 'initialize',
  params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'e2e-gw27-client', version: '1' } } };

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-034: an MCP client given a virtual server\'s URL signs a person in, and gets a 15-minute token for that server only',
  {
    tag: ['@flow', profileTag('mcp-signin')],
    annotation: flowAnnotation({
      id: 'GW-E2E-034',
      area: 'gateway',
      userGoal:
        'A person pastes a virtual MCP server\'s URL into an MCP client, signs in once, approves the client for their organization, and the client gets a short-lived token for that server',
      steps: [
        'an org admin seeds an MCP server and a virtual server over it',
        'the MCP client calls the virtual server\'s URL with no credential and follows the answer to the authorization server',
        'the MCP client discovers the authorization server, registers itself and starts the sign-in with PKCE S256 and the virtual server\'s URL as the resource',
        'the signed-in person opens the consent request, sees the client, the organization and the scopes, and approves',
        'the client exchanges the code, opens an MCP session at the virtual server\'s URL with the access token, lists its tools and calls one',
        'the client refreshes once, and the replayed refresh token is refused',
      ],
      backendChecks: [
        'with no credential the virtual server\'s URL answers 401 whose challenge names its protected-resource metadata, which names that URL as the resource and this issuer as its only authorization server',
        'the metadata names this issuer, S256 only, iss in the redirect, registration and the client ID metadata document',
        'dynamic registration answers a public native client with no secret',
        'a person of another organization cannot open a request for this virtual server',
        'the redirect carries the code, the state and iss',
        'the access token verifies against the published keys with aud = the virtual server\'s URL, sub = the person, org_id = their organization, and lives 900 seconds',
        'with the access token the client opens an MCP session at the virtual server\'s URL, is offered exactly the server\'s three tools and its echo call answers the minted text',
        'the same access token opens neither another virtual server of the organization nor the model API',
        'refresh rotates the refresh token; the old one is refused and takes its successor down with it',
        'the person sees the consent and revoking it stops refresh',
      ],
    }),
  },
  async ({ scopeActors: scopes }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const other = scopes.ownerB;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const serverId = `e2e-gw26-${suffix}`;
    const slug = `e2e-gw26-${suffix}`;
    const otherSlug = `e2e-gw26-other-${suffix}`;
    const marker = `e2e-gw27-echo-${suffix}`;

    await test.step('API: seed the org\'s MCP server and a virtual server over it', async () => {
      await owner.api.post(UPDATE_SERVER, {
        server_id: serverId,
        config: { transport: 'http', url: MCP_MOCK.url, auth: { type: 'bearer', token: MCP_MOCK.bearer } },
      });
      await owner.api.post(UPDATE_CONFIG, {
        mcp: { enabled: true, virtual_servers: {
          [slug]: { title: `Sign-in ${suffix}`, members: [serverId] },
          [otherSlug]: { title: `Other ${suffix}`, members: [serverId] },
        } },
      });
    });

    const gateway = await owner.api.get<Envelope<{ base_url: string }>>(GATEWAY);
    const resource = `${gateway.result.base_url.replace(/\/v1\/?$/, '')}/mcp/v/${slug}`;
    const req = await request.newContext({ baseURL: E2E.apiUrl, maxRedirects: 0 });
    try {
      const issuer = `${E2E.apiUrl}/agentcc/mcp-oauth`;
      await test.step('MCP: with no credential the URL answers 401, which sends the client to this issuer', async () => {
        const answer = await mcp(req, `/mcp/v/${slug}`, '', undefined, INITIALIZE);
        expect(answer.status, JSON.stringify(answer.body)).toBe(401);
        expect(answer.session).toBeUndefined();
        const metadataPath = `${PRM_PREFIX}/mcp/v/${slug}`;
        expect(answer.challenge).toContain(`resource_metadata="${new URL(resource).origin}${metadataPath}"`);
        const prm = await req.get(`${E2E.gatewayUrl}${metadataPath}`);
        expect(prm.status()).toBe(200);
        expect(await prm.json()).toMatchObject({ resource, authorization_servers: [issuer] });
      });
      const meta = await json<Record<string, any>>(await req.get(METADATA));
      await test.step('API: discovery names this issuer and what a client needs', async () => {
        expect(meta.issuer).toBe(issuer);
        expect(meta.code_challenge_methods_supported).toEqual(['S256']);
        expect(meta.authorization_response_iss_parameter_supported).toBe(true);
        expect(meta.client_id_metadata_document_supported).toBe(true);
        expect(meta.registration_endpoint).toBe(`${issuer}/register`);
      });

      const registered = await json<Record<string, any>>(await req.post(meta.registration_endpoint, {
        data: {
          client_name: `E2E MCP client ${suffix}`, redirect_uris: [REDIRECT],
          grant_types: ['authorization_code', 'refresh_token'], response_types: ['code'],
          token_endpoint_auth_method: 'none',
        },
      }));
      await test.step('API: dynamic registration answers a public native client', async () => {
        expect(registered.client_id).toMatch(/^mcpc_/);
        expect(registered.application_type).toBe('native');
        expect(registered.client_secret).toBeUndefined();
      });

      const startSignIn = async (challenge: string, state: string) => {
        const res = await req.get(meta.authorization_endpoint, {
          params: {
            response_type: 'code', client_id: registered.client_id, redirect_uri: REDIRECT,
            code_challenge: challenge, code_challenge_method: 'S256', resource, scope: 'mcp:tools', state,
          },
        });
        expect(res.status(), await res.text()).toBe(302);
        expect(res.headers()['x-frame-options']).toBe('DENY');
        const consentPage = new URL(res.headers().location);
        const requestId = consentPage.searchParams.get('request');
        expect(requestId).toBeTruthy();
        return `/agentcc/mcp-oauth/consent/${requestId}/`;
      };

      await test.step('API: a person of another organization cannot open the request', async () => {
        const consentPath = await startSignIn(pkce().challenge, 'foreign');
        const foreign = await scopes.send(other, 'GET', consentPath);
        expect(foreign.status).toBe(403);
      });

      const { verifier, challenge } = pkce();
      const state = `st-${suffix}`;
      const consentPath = await startSignIn(challenge, state);
      let code = '';
      await test.step('API: the person sees the client, the organization and the scopes, and approves', async () => {
        const shown = await scopes.send<Envelope<Consent>>(owner, 'GET', consentPath);
        expect(shown.status).toBe(200);
        const consent = shown.body.result;
        expect(consent.organizations.map(o => o.id)).toEqual([owner.organizationId]);
        expect(consent.scopes.map(s => s.name)).toEqual(['mcp:tools']);
        expect(consent.client).toMatchObject({ name: `E2E MCP client ${suffix}`, kind: 'dcr', verified: false });
        expect(consent.resource).toBe(resource);
        expect(consent.redirect_host).toBe('127.0.0.1');
        expect(consent.pre_approved).toBe(false);
        const decided = await scopes.send<Envelope<{ redirect_to: string }>>(
          owner, 'POST', consentPath, { decision: 'approve' });
        expect(decided.status).toBe(200);
        const back = new URL(decided.body.result.redirect_to);
        expect(`${back.origin}${back.pathname}`).toBe(REDIRECT);
        expect(back.searchParams.get('state')).toBe(state);
        expect(back.searchParams.get('iss')).toBe(issuer);
        code = back.searchParams.get('code') ?? '';
        expect(code.length).toBeGreaterThanOrEqual(32);
      });

      let tokens!: TokenAnswer;
      await test.step('API: the code becomes a 15-minute token for this virtual server only', async () => {
        const res = await req.post(meta.token_endpoint, form({
          grant_type: 'authorization_code', code, redirect_uri: REDIRECT,
          client_id: registered.client_id, code_verifier: verifier,
        }));
        expect(res.status(), await res.text()).toBe(200);
        expect(res.headers()['cache-control']).toBe('no-store');
        tokens = await res.json() as TokenAnswer;
        expect(tokens).toMatchObject({ token_type: 'Bearer', expires_in: 900, scope: 'mcp:tools' });
        const claims = await verifiedClaims(req, meta.jwks_uri, tokens.access_token);
        expect(claims).toMatchObject({
          typ: 'futureagi-mcp-access', iss: issuer, aud: resource, sub: owner.userId,
          org_id: owner.organizationId, client_id: registered.client_id, scope: 'mcp:tools',
        });
        expect(Number(claims.exp) - Number(claims.iat)).toBe(900);
        const again = await req.post(meta.token_endpoint, form({
          grant_type: 'authorization_code', code, redirect_uri: REDIRECT,
          client_id: registered.client_id, code_verifier: verifier,
        }));
        expect(again.status()).toBe(400);
        expect((await again.json()).error).toBe('invalid_grant');
      });

      await test.step('API: a second exchange of the code took the first grant down; sign in again', async () => {
        const stale = await req.post(meta.token_endpoint, form({
          grant_type: 'refresh_token', refresh_token: tokens.refresh_token, client_id: registered.client_id,
        }));
        expect(stale.status()).toBe(400);
        const fresh = pkce();
        const path = await startSignIn(fresh.challenge, `${state}-2`);
        const decided = await scopes.send<Envelope<{ redirect_to: string }>>(owner, 'POST', path, { decision: 'approve' });
        const freshCode = new URL(decided.body.result.redirect_to).searchParams.get('code') ?? '';
        const res = await req.post(meta.token_endpoint, form({
          grant_type: 'authorization_code', code: freshCode, redirect_uri: REDIRECT,
          client_id: registered.client_id, code_verifier: fresh.verifier,
        }));
        expect(res.status()).toBe(200);
        tokens = await res.json() as TokenAnswer;
      });

      await test.step('MCP: the access token opens the virtual server, lists its tools and calls echo', async () => {
        let session: string | undefined;
        await expect.poll(async () => {
          const opened = await mcp(req, `/mcp/v/${slug}`, tokens.access_token, undefined, INITIALIZE);
          session = opened.session;
          if (opened.status !== 200) return `initialize ${opened.status}`;
          await mcp(req, `/mcp/v/${slug}`, tokens.access_token, session, { jsonrpc: '2.0', method: 'notifications/initialized' });
          const list = await mcp(req, `/mcp/v/${slug}`, tokens.access_token, session, { jsonrpc: '2.0', id: 2, method: 'tools/list' });
          return ((list.body?.result?.tools ?? []) as Array<{ name: string }>).map((t) => t.name).sort().join(',');
        }, POLL.GATEWAY_SYNC).toBe(['delete_file', 'echo', 'get_weather'].map((t) => `${serverId}__${t}`).join(','));
        const call = await mcp(req, `/mcp/v/${slug}`, tokens.access_token, session, {
          jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: `${serverId}__echo`, arguments: { text: marker } },
        });
        expect(call.status, JSON.stringify(call.body)).toBe(200);
        expect(call.body?.result?.isError ?? false, JSON.stringify(call.body)).toBe(false);
        const texts = ((call.body?.result?.content ?? []) as Array<{ type: string; text?: string }>)
          .filter((part) => part.type === 'text').map((part) => part.text ?? '');
        expect(texts).toEqual([`echo ok: ${JSON.stringify({ text: marker })}`]);
      });

      await test.step('MCP: the same token opens no other virtual server and not the model API', async () => {
        const elsewhere = await mcp(req, `/mcp/v/${otherSlug}`, tokens.access_token, undefined, INITIALIZE);
        expect(elsewhere.status, JSON.stringify(elsewhere.body)).toBe(401);
        expect(elsewhere.challenge).toContain(`${PRM_PREFIX}/mcp/v/${otherSlug}"`);
        const models = await req.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
          headers: { Authorization: `Bearer ${tokens.access_token}`, 'Content-Type': 'application/json' },
          data: { model: 'gpt-4o-mini', messages: [{ role: 'user', content: marker }] },
        });
        expect(models.status()).toBe(401);
      });

      await test.step('API: refresh rotates; the replayed refresh token is refused and takes its successor down', async () => {
        const first = tokens.refresh_token;
        const rotated = await req.post(meta.token_endpoint, form({
          grant_type: 'refresh_token', refresh_token: first, client_id: registered.client_id,
        }));
        expect(rotated.status()).toBe(200);
        const next = await rotated.json() as TokenAnswer;
        expect(next.refresh_token).not.toBe(first);
        const claims = await verifiedClaims(req, meta.jwks_uri, next.access_token);
        expect(claims.aud).toBe(resource);
        const replayed = await req.post(meta.token_endpoint, form({
          grant_type: 'refresh_token', refresh_token: first, client_id: registered.client_id,
        }));
        expect(replayed.status()).toBe(400);
        const successor = await req.post(meta.token_endpoint, form({
          grant_type: 'refresh_token', refresh_token: next.refresh_token, client_id: registered.client_id,
        }));
        expect(successor.status()).toBe(400);
      });

      await test.step('API: the person sees the consent, and revoking it stops refresh', async () => {
        const fresh = pkce();
        const path = await startSignIn(fresh.challenge, `${state}-3`);
        const decided = await scopes.send<Envelope<{ redirect_to: string }>>(owner, 'POST', path, { decision: 'approve' });
        const freshCode = new URL(decided.body.result.redirect_to).searchParams.get('code') ?? '';
        const issued = await json<TokenAnswer>(await req.post(meta.token_endpoint, form({
          grant_type: 'authorization_code', code: freshCode, redirect_uri: REDIRECT,
          client_id: registered.client_id, code_verifier: fresh.verifier,
        })));
        const listed = await scopes.send<Envelope<Array<{ id: number; client_id: string; resource: string }>>>(
          owner, 'GET', '/agentcc/mcp-oauth/consents/');
        const mine = listed.body.result.find(c => c.client_id === registered.client_id && c.resource === resource);
        expect(mine, 'the consent is listed for its person').toBeTruthy();
        const foreign = await scopes.send<Envelope<unknown[]>>(other, 'GET', '/agentcc/mcp-oauth/consents/');
        expect(foreign.body.result.map(c => (c as { client_id: string }).client_id)).not.toContain(registered.client_id);
        const revoked = await scopes.send(owner, 'DELETE', `/agentcc/mcp-oauth/consents/${mine!.id}/`);
        expect(revoked.status).toBe(200);
        const after = await req.post(meta.token_endpoint, form({
          grant_type: 'refresh_token', refresh_token: issued.refresh_token, client_id: registered.client_id,
        }));
        expect(after.status()).toBe(400);
        expect((await after.json()).error).toBe('invalid_grant');
      });

      await testInfo.attach('sign-in', {
        contentType: 'application/json',
        body: JSON.stringify({ issuer, resource, clientId: registered.client_id, slug, serverId,
          organizationId: owner.organizationId }, null, 2),
      });
    } finally {
      await req.dispose();
    }
  },
);
