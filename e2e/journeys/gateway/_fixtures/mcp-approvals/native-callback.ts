import { randomBytes, randomUUID } from 'node:crypto';
import { cookie, html, json, listenLocal, loopbackUrl, singleValues } from './local-http.ts';

type Receipt = { status: 'armed' | 'started' | 'received' | 'denied'; cookieMatched: boolean;
  stateMatched: boolean; issuerMatched: boolean; deliveries: number };
type Attempt = { authorizeUrl: string; issuer: string; state: string; cookie: string;
  expires: number; code?: string; receipt: Receipt };

/** Real loopback HTTP native callback receiver, never page.route()/fulfill().
 * OAuth query contract pinned at da4c81cc62 to mcpmock/authorize.go:redirect
 * (code/error, state, iss). /callback also appears in the synthetic model fixture
 * futureagi/agentcc/tests/test_schema_mcp.py. /start/<id> is a NEW fixture-only
 * browser bootstrap, NOT a guessed product route. Its cookie and one-use checks
 * test this receiver; they do not prove the absent product connect-state model.
 * No token exchange, external redirect or outgoing network request is performed.
 */
export async function startNativeCallback() {
  const attempts = new Map<string, Attempt>();
  const byState = new Map<string, string>();
  let callbackOrigin = '';
  let closed = false;
  const local = await listenLocal((request, response, url) => {
    if (request.method !== 'GET') { json(response, 405, { error: 'invalid_request' }); return; }
    if (url.pathname.startsWith('/start/')) {
      const id = url.pathname.slice('/start/'.length), attempt = attempts.get(id);
      if (!attempt || attempt.receipt.status !== 'armed' || Date.now() >= attempt.expires || url.search) {
        json(response, 400, { error: 'invalid_request' }); return;
      }
      attempt.receipt.status = 'started';
      response.setHeader('Set-Cookie', `j14_native_${id}=${attempt.cookie}; HttpOnly; SameSite=Lax; Path=/callback`);
      response.writeHead(302, { Location: attempt.authorizeUrl, 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer' });
      response.end(); return;
    }
    if (url.pathname !== '/callback') { json(response, 404, { error: 'not_found' }); return; }
    const fields = singleValues(url.searchParams);
    const id = byState.get(fields.state), attempt = id && attempts.get(id);
    const success = typeof fields.code === 'string' && fields.code.length > 0 && fields.code.length <= 2048 &&
      Object.keys(fields).sort().join(',') === 'code,iss,state';
    const denial = fields.error === 'access_denied' && Object.keys(fields).sort().join(',') === 'error,iss,state';
    if (!id || !attempt || attempt.receipt.status !== 'started' || Date.now() >= attempt.expires ||
        cookie(request, `j14_native_${id}`) !== attempt.cookie || fields.iss !== attempt.issuer || !(success || denial)) {
      json(response, 400, { error: 'invalid_request' }); return;
    }
    // Native receipt advances synchronously and only once. No cookie/code/state
    // values leave this closure except the one explicit VM-memory takeCode call.
    attempt.receipt = { status: success ? 'received' : 'denied', cookieMatched: true,
      stateMatched: true, issuerMatched: true, deliveries: 1 };
    if (success) attempt.code = fields.code;
    byState.delete(attempt.state);
    response.setHeader('Set-Cookie', `j14_native_${id}=; HttpOnly; SameSite=Lax; Path=/callback; Max-Age=0`);
    html(response, `<!doctype html><html><head><title>Fixture callback</title></head><body>
      <h1>${success ? 'Fixture callback received' : 'Fixture consent denied'}</h1></body></html>`);
  });
  callbackOrigin = local.origin;
  return {
    redirectUri: `${callbackOrigin}/callback`,
    arm(authorizationUrl: string) {
      if (closed) throw new Error('Native receiver closed');
      const authorize = loopbackUrl(authorizationUrl);
      if (authorize.pathname !== '/authorize' || authorize.searchParams.has('state')) {
        throw new Error('Expected unarmed fixture authorization URL');
      }
      const id = randomUUID(), state = randomBytes(32).toString('base64url');
      authorize.searchParams.set('state', state);
      attempts.set(id, { authorizeUrl: authorize.href, issuer: authorize.origin, state,
        cookie: randomBytes(32).toString('base64url'), expires: Date.now() + 600_000,
        receipt: { status: 'armed', cookieMatched: false, stateMatched: false, issuerMatched: false, deliveries: 0 } });
      byState.set(state, id);
      return { id, startUrl: `${callbackOrigin}/start/${id}` };
    },
    receipt(id: string): Receipt {
      const attempt = attempts.get(id);
      if (!attempt || closed) throw new Error('Unknown native callback attempt');
      return { ...attempt.receipt };
    },
    takeCode(id: string): string {
      const attempt = attempts.get(id);
      if (!attempt?.code || attempt.receipt.status !== 'received' || closed) {
        throw new Error('Native callback has no unconsumed code');
      }
      const code = attempt.code; delete attempt.code; return code;
    },
    async close() {
      closed = true;
      try { await local.close(); } finally { attempts.clear(); byState.clear(); }
    },
  };
}
