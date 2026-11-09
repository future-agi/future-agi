// H12 + H1: the browser side of a walk's network boundary. The stack publishes
// its ports on the VM's loopback (e2e/stack/docker-compose.walk-pins.yml) and the
// compose networks stay as the product ships them; the boundary is here, in
// Playwright, on every walk context before its first page:
//   - a request to loopback (localhost, 127.0.0.1, [::1]) on any port is continued
//     unchanged (the app, the API, the gateway, the local sinks);
//   - data:, blob: and about: never leave the browser;
//   - everything else is aborted before dispatch and recorded by origin only
//     (scheme, host, port: never a path, query, header or body);
//   - a WebSocket to a non-local origin is closed and recorded; a local one is
//     connected through unchanged;
//   - a request the route never saw (a redirect hop: Playwright routes only the
//     first request of a chain) is recorded as observed-unrouted.
// Classification (GATEWAY-CLOUD-RECIPE, "Browser walks: asset classification"):
// an aborted request to one of ASSETS_PENDING_HOSTS is ASSETS-PENDING, a known
// no-egress artifact until main's offline-assets wrapper lands, counted and
// listed but not a product failure. Any other non-local origin is FAIL: assert()
// throws, so the walk fails even when every UI assertion passed.
// Service workers are blocked by the context option the walk fixture sets.
import type { BrowserContext, Request } from '@playwright/test';

/** The four asset hosts the recipe names, exactly. */
export const ASSETS_PENDING_HOSTS: readonly string[] = ['fonts.googleapis.com', 'api.iconify.design',
  'api.simplesvg.com', 'api.unisvg.com'];
const ICON_HOSTS = new Set(['api.iconify.design', 'api.simplesvg.com', 'api.unisvg.com']);
const LOOPBACK = new Set(['localhost', '127.0.0.1', '[::1]']);

export type BoundaryClass = 'ASSETS-PENDING' | 'FAIL';
export type BoundaryPhase = 'aborted-before-dispatch' | 'observed-unrouted' | 'websocket-refused';
export interface BoundaryAttempt {
  origin: string; host: string; phase: BoundaryPhase; resourceType: string; method: string;
  navigation: boolean; redirect: boolean; classification: BoundaryClass; context: string;
}
export interface BoundaryReceipt {
  label: string; assetsPendingHosts: readonly string[]; continuedLocal: number; localOrigins: string[];
  attempts: BoundaryAttempt[];
  /** Per classification and origin: how many times. */
  ledger: { classification: BoundaryClass; origin: string; count: number }[];
}

/** Scheme, host and port only. */
export function originOf(url: string): { origin: string; host: string } {
  try {
    const u = new URL(url);
    return u.host ? { origin: `${u.protocol}//${u.host}`, host: u.hostname } : { origin: u.protocol, host: '' };
  } catch {
    return { origin: 'unparseable', host: '' };
  }
}

/** Whether a URL stays on this machine (or never leaves the browser). */
export function isLocal(url: string): boolean {
  let u: URL;
  try { u = new URL(url); } catch { return false; }
  if (u.protocol === 'data:' || u.protocol === 'blob:' || u.protocol === 'about:') return true;
  return ['http:', 'https:', 'ws:', 'wss:'].includes(u.protocol) && LOOPBACK.has(u.hostname);
}

export const classify = (host: string): BoundaryClass => (ASSETS_PENDING_HOSTS.includes(host) ? 'ASSETS-PENDING' : 'FAIL');

export class WalkBoundary {
  private readonly installed = new WeakSet<BrowserContext>();
  private readonly aborted = new WeakSet<Request>();
  private readonly seen = new WeakSet<Request>();
  private readonly attempts: BoundaryAttempt[] = [];
  private readonly local = new Set<string>();
  private continued = 0;

  constructor(readonly label: string) {}

  /** Before any page exists: an already-used context cannot be made safe afterwards. */
  async install(context: BrowserContext, contextLabel: string): Promise<void> {
    if (this.installed.has(context)) throw new Error('STOP: walk boundary is already installed on this context');
    if (context.pages().length) throw new Error('STOP: walk boundary must be installed before any page opens');
    this.installed.add(context);
    context.on('request', (req) => this.observe(req, contextLabel, 'observed-unrouted'));
    await context.routeWebSocket(() => true, async (ws) => {
      if (isLocal(ws.url())) { ws.connectToServer(); return; }
      this.record(ws.url(), contextLabel, 'websocket-refused', 'websocket', 'GET', false, false);
      await ws.close({ code: 1008, reason: 'e2e walk boundary' });
    });
    await context.route(() => true, async (route, req) => {
      if (isLocal(req.url())) {
        this.continued += 1;
        this.local.add(originOf(req.url()).origin);
        await route.continue();
        return;
      }
      this.aborted.add(req);
      this.observe(req, contextLabel, 'aborted-before-dispatch');
      await route.abort('blockedbyclient');
    });
  }

  private observe(req: Request, context: string, phase: BoundaryPhase): void {
    if (isLocal(req.url()) || this.seen.has(req)) return;
    // The route records its own aborts; the request event fires first for those.
    if (phase === 'observed-unrouted' && !req.redirectedFrom()) return;
    this.seen.add(req);
    this.record(req.url(), context, this.aborted.has(req) ? 'aborted-before-dispatch' : phase, req.resourceType(),
      req.method(), req.isNavigationRequest(), req.redirectedFrom() !== null);
  }

  private record(url: string, context: string, phase: BoundaryPhase, resourceType: string, method: string,
    navigation: boolean, redirect: boolean): void {
    const { origin, host } = originOf(url);
    this.attempts.push({ origin, host, phase, resourceType, method, navigation, redirect,
      classification: classify(host), context });
  }

  /** Whether an icon host was aborted in this context (empty icons there are ASSETS-PENDING). */
  iconHostsAborted(context?: string): boolean {
    return this.attempts.some((a) => ICON_HOSTS.has(a.host) && (context === undefined || a.context === context));
  }

  receipt(): BoundaryReceipt {
    const counts = new Map<string, { classification: BoundaryClass; origin: string; count: number }>();
    for (const a of this.attempts) {
      const k = `${a.classification} ${a.origin}`;
      const row = counts.get(k) ?? { classification: a.classification, origin: a.origin, count: 0 };
      row.count += 1;
      counts.set(k, row);
    }
    return { label: this.label, assetsPendingHosts: ASSETS_PENDING_HOSTS, continuedLocal: this.continued,
      localOrigins: [...this.local].sort(), attempts: [...this.attempts],
      ledger: [...counts.values()].sort((a, b) => a.origin.localeCompare(b.origin)) };
  }

  /** Fails on any non-local attempt that is not ASSETS-PENDING, even one the app caught. */
  assert(): void {
    const failing = this.receipt().ledger.filter((row) => row.classification === 'FAIL');
    if (failing.length) {
      throw new Error(`STOP: walk boundary ${this.label} saw non-local traffic outside ASSETS-PENDING: ` +
        failing.map((row) => `${row.origin} x${row.count}`).join('; '));
    }
  }
}
