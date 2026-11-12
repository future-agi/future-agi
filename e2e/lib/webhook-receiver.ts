// A real HTTP receiver for the webhooks flows (webhooks DESIGN.md section 17.1),
// running inside the Playwright worker. The product's dispatcher sends to it
// over the network, exactly as it would to a customer's endpoint; each flow
// scripts its answers and asserts on the requests it recorded.
//
// Where it listens:
//   - E2E_WEBHOOK_RECEIVER_HOST is the address the product is given. It must be
//     reachable from the worker that sends and must be one the install allows
//     (FI_WEBHOOKS_PRIVATE_CIDRS / FI_WEBHOOKS_ALLOWED_PRIVATE_HOSTS and
//     FI_WEBHOOKS_PRIVATE_PORTS). 127.0.0.1 never qualifies: loopback is in the
//     product's NEVER table, so the managed stack uses host.docker.internal and
//     a process stack uses its Docker bridge address.
//   - The port is E2E_WEBHOOK_RECEIVER_PORT_BASE + the worker's parallel index,
//     so parallel workers never share a receiver.
import { createHmac, timingSafeEqual } from 'node:crypto';
import { createServer, type IncomingHttpHeaders, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { isIP } from 'node:net';

export interface ReceivedRequest {
  at: number;
  method: string;
  path: string;
  headers: IncomingHttpHeaders;
  body: Buffer;
}

export interface ScriptedAnswer { status: number; body?: string; contentType?: string }

export const RECEIVER = {
  host: process.env.E2E_WEBHOOK_RECEIVER_HOST ?? 'host.docker.internal',
  portBase: Number(process.env.E2E_WEBHOOK_RECEIVER_PORT_BASE ?? 28400),
};

export class WebhookReceiver {
  readonly requests: ReceivedRequest[] = [];
  private answers: ScriptedAnswer[] = [];
  private fallback: ScriptedAnswer = { status: 200, body: '{"ok":true}' };

  private constructor(private server: Server, readonly url: string) {}

  /** Start on the worker's own port; `path` makes the URL unique to the flow. */
  static async start(parallelIndex: number, path: string): Promise<WebhookReceiver> {
    const port = RECEIVER.portBase + parallelIndex;
    const bind = isIP(RECEIVER.host) ? RECEIVER.host : '0.0.0.0';
    let receiver!: WebhookReceiver;
    const server = createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on('data', (c: Buffer) => chunks.push(c));
      req.on('end', () => {
        receiver.requests.push({ at: Date.now(), method: req.method ?? '', path: req.url ?? '',
          headers: req.headers, body: Buffer.concat(chunks) });
        const a = receiver.answers.shift() ?? receiver.fallback;
        res.writeHead(a.status, { 'content-type': a.contentType ?? 'application/json' });
        res.end(a.body ?? '');
      });
    });
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject);
      server.listen(port, bind, () => resolve());
    });
    const actual = (server.address() as AddressInfo).port;
    receiver = new WebhookReceiver(server, `http://${RECEIVER.host}:${actual}${path}`);
    return receiver;
  }

  /** Answer the next requests in order, then `fallback` (200 unless changed). */
  script(answers: ScriptedAnswer[], fallback?: ScriptedAnswer) {
    this.answers = [...answers];
    if (fallback) this.fallback = fallback;
  }

  json(i: number): Record<string, unknown> {
    return JSON.parse(this.requests[i].body.toString('utf8'));
  }

  async stop() {
    await new Promise<void>((resolve) => this.server.close(() => resolve()));
  }
}

/** The DESIGN.md section 5.4 ten-line recipe, in TypeScript: true when any `v1,` item verifies with `secret`. */
export function verifiesWith(secret: string, req: ReceivedRequest, toleranceS = 300): boolean {
  const key = Buffer.from(secret.replace(/^whsec_/, ''), 'base64');
  const id = String(req.headers['webhook-id'] ?? '');
  const ts = String(req.headers['webhook-timestamp'] ?? '');
  if (!id || !ts || Math.abs(Date.now() / 1000 - Number(ts)) > toleranceS) return false;
  const expected = createHmac('sha256', key)
    .update(Buffer.concat([Buffer.from(`${id}.${ts}.`), req.body])).digest();
  return String(req.headers['webhook-signature'] ?? '').split(' ')
    .filter((s) => s.startsWith('v1,'))
    .some((s) => {
      const got = Buffer.from(s.slice(3), 'base64');
      return got.length === expected.length && timingSafeEqual(got, expected);
    });
}

export function signatureCount(req: ReceivedRequest): number {
  return String(req.headers['webhook-signature'] ?? '').split(' ').filter((s) => s.startsWith('v1,')).length;
}
