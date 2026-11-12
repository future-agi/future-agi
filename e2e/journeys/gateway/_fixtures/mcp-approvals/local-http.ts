import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';

/** New test-fixture transport, not a product route/runner implementation.
 * No process spawning, outbound client, environment, file or Docker access.
 * Listens ONLY on literal IPv4 loopback inside the accepted worker namespace.
 * Product SSRF policy is NOT relaxed here: the runner owes its reviewed same-job
 * mapping; a fixture's existence never authorizes public/private-network bypass.
 */
export function loopbackUrl(raw: string): URL {
  const url = new URL(raw);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port ||
      url.username || url.password || url.hash) throw new Error('Expected explicit IPv4 loopback HTTP fixture URL');
  return url;
}

export class FixtureInputError extends Error {
  constructor() { super('Invalid fixture request'); }
}
export function json(response: ServerResponse, status: number, value: unknown): void {
  response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  response.end(JSON.stringify(value));
}
export async function body(request: IncomingMessage): Promise<string> {
  // Tight fixture bound; unlike mcpmock's 16MiB limit no large content is needed.
  const chunks: Buffer[] = []; let length = 0;
  for await (const chunk of request) {
    length += chunk.length;
    if (length > 16_384) throw new FixtureInputError();
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString('utf8');
}
export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new FixtureInputError();
  return value as Record<string, unknown>;
}
export function parseJson(text: string): Record<string, unknown> {
  try { return object(JSON.parse(text)); } catch { throw new FixtureInputError(); }
}
export function singleValues(params: URLSearchParams): Record<string, string> {
  const out: Record<string, string> = Object.create(null);
  for (const [key, value] of params) {
    if (key in out) throw new FixtureInputError();
    out[key] = value;
  }
  return out;
}
export function cookie(request: IncomingMessage, name: string): string | undefined {
  const matches = (request.headers.cookie ?? '').split(';').map(value => value.trim())
    .filter(value => value.startsWith(`${name}=`));
  return matches.length === 1 ? matches[0].slice(name.length + 1) : undefined;
}
export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;',
    '"': '&quot;', "'": '&#39;' })[char]!);
}
export function html(response: ServerResponse, value: string, nativeCallback?: string): void {
  // A consent POST redirects to its already registered native callback. Chromium
  // checks that redirect against form-action too. Add ONLY the bound callback's
  // canonical origin; callback pages without a form retain the self-only policy.
  let formAction = "'self'";
  if (nativeCallback !== undefined) {
    const callback = loopbackUrl(nativeCallback);
    if (callback.pathname !== '/callback' || callback.search) throw new FixtureInputError();
    formAction += ` ${callback.origin}`;
  }
  response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8',
    'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': `default-src 'none'; form-action ${formAction}; frame-ancestors 'none'` });
  response.end(value);
}

export async function listenLocal(
  handler: (request: IncomingMessage, response: ServerResponse, url: URL) => Promise<void> | void,
  onArrival?: (request: IncomingMessage) => void,
) {
  let origin = '';
  let internalFailures = 0;
  const server = createServer(async (request, response) => {
    try {
      // Count BEFORE Host/path/body validation so malformed attempts cannot hide
      // behind a zero-dispatch witness. No request values are copied to evidence.
      onArrival?.(request);
      if (!origin || request.headers.host !== new URL(origin).host ||
          !request.url?.startsWith('/') || request.url.startsWith('//') || request.url.includes('\\')) {
        throw new FixtureInputError();
      }
      const url = new URL(request.url, origin);
      if (url.origin !== origin) throw new FixtureInputError();
      await handler(request, response, url);
    } catch (error) {
      // No request/error dump: callbacks and token endpoints carry secrets.
      const invalid = error instanceof FixtureInputError;
      if (!invalid) internalFailures++;
      if (!response.headersSent) json(response, invalid ? 400 : 500, { error: invalid ? 'invalid_request' : 'fixture_error' });
      else response.destroy();
    }
  });
  server.requestTimeout = 10_000; // Bounds receipt of a fixture body, not a product SLA.
  server.headersTimeout = 10_000;
  await new Promise<void>((resolve, reject) => {
    const failed = () => reject(new Error('Loopback fixture bind failed'));
    server.once('error', failed);
    server.listen(0, '127.0.0.1', () => { server.removeListener('error', failed); resolve(); });
  });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Fixture listener has no TCP address');
  origin = `http://127.0.0.1:${address.port}`;
  let closed = false;
  return {
    origin,
    async close() {
      if (!closed) {
        closed = true;
        await new Promise<void>((resolve, reject) => {
          server.close(error => error ? reject(new Error('Fixture close failed')) : resolve());
          server.closeAllConnections();
        });
      }
      if (internalFailures) throw new Error('Fixture had unhandled handler failures');
    },
  };
}
