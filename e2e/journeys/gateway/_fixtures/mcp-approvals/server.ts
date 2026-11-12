import { randomBytes } from 'node:crypto';
import { createCounters } from './counters.ts';
import { listenLocal, json } from './local-http.ts';
import { createOAuthState } from './oauth-state.ts';
import { createOAuthHandler } from './oauth-http.ts';
import { createMcpHandler } from './mcp-http.ts';

export interface FixtureOptions { principalIds: readonly string[]; redirectUris: readonly string[] }

/** Callable fixture factory for the E6 named-fixture slot. Import has no effects.
 * Future caller runs inside the accepted stack/worker network namespace with
 * inherited socket guard/observers. Never starts a process or a second runner.
 * Local upstream bearer != gateway personal key: F09 still supplies the latter.
 * Issued values are VM-memory-only and MUST NOT enter attachments/logs/traces.
 */
export async function startMcpOAuthFixture(options: FixtureOptions) {
  const counters = createCounters(options.principalIds);
  const oauth = createOAuthState(counters, options.redirectUris);
  const bearers = new Map<string, { principalId: string; active: boolean }>();
  const oauthHandler = createOAuthHandler(oauth, counters);
  const identify = (token: string) => {
    const bearer = bearers.get(token);
    return bearer ? { principalId: bearer.principalId, valid: bearer.active } : oauth.principal(token);
  };
  const mcpHandler = createMcpHandler(counters, identify);
  const local = await listenLocal(async (request, response, url) => {
    if (await oauthHandler(request, response, url)) return;
    if (await mcpHandler(request, response, url)) return;
    counters.record(null, 'transport', 'refused');
    json(response, 404, { error: 'not_found' });
  }, request => {
    const token = /^Bearer ([^\s]+)$/i.exec(request.headers.authorization ?? '')?.[1] ?? '';
    counters.record(identify(token).principalId, 'arrival', 'received');
  });
  const resource = `${local.origin}/mcp`;
  oauth.bindResource(resource);
  let closed = false;
  return {
    origin: local.origin, resource,
    /** Caller supplies a minted principal, never accepts header-claimed identity. */
    issueBearer(principalId: string): string {
      if (closed || !counters.acceptsPrincipal(principalId)) throw new Error('Unknown or closed fixture principal');
      const token = randomBytes(32).toString('base64url');
      bearers.set(token, { principalId, active: true }); return token;
    },
    revokeBearer(token: string): void {
      const bearer = bearers.get(token);
      if (!bearer || closed) throw new Error('Unknown or closed fixture bearer');
      bearer.active = false;
    },
    counters: counters.snapshot,
    async close() {
      closed = true;
      try { await local.close(); } finally { oauth.dispose(); bearers.clear(); }
    },
  };
}
export type LocalMcpOAuth = Awaited<ReturnType<typeof startMcpOAuthFixture>>;
