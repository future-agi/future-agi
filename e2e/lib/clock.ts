// A fresh per-minute window on the gateway's own clock. Rate limits and
// Throttle count per wall-clock minute of the gateway (plugins/ratelimit fixed
// windows; plugins/budget/budget.go takeThrottleSlots: now.Truncate(time.Minute)),
// and the stack's VM clock can drift from the host's, so the phase is read from
// the gateway's `Date` header (GET /healthz, server/server.go; Go's net/http sets
// it on every response, 1 s resolution), never from the host clock. Only the
// phase comes from the gateway; the waits are host durations, which skew does not
// change.
import type { APIRequestContext } from '@playwright/test';
import { E2E } from './env';

// RFC 9110 IMF-fixdate, the only form net/http writes: "Wed, 22 Oct 2026 10:59:20 GMT".
const IMF_FIXDATE = /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4} \d{2}:\d{2}:\d{2} GMT$/;

/** Parses a `Date` header; throws when it is absent or not an IMF-fixdate (no host-clock fallback). */
export function parseGatewayDate(header: string | null | undefined): Date {
  if (!header || !IMF_FIXDATE.test(header)) throw new Error(`gateway clock: no usable Date header (${JSON.stringify(header ?? null)})`);
  const at = new Date(header);
  if (Number.isNaN(at.getTime())) throw new Error(`gateway clock: unparseable Date header ${JSON.stringify(header)}`);
  return at;
}

/** The gateway's time now, to the second. */
export async function gatewayNow(request: APIRequestContext): Promise<Date> {
  const res = await request.get(`${E2E.gatewayUrl}/healthz`);
  if (res.status() !== 200) throw new Error(`gateway clock: GET /healthz answered ${res.status()}`);
  return parseGatewayDate(res.headers()['date']);
}

export interface WindowOptions { notBeforeSec?: number; notAfterSec?: number }

/** freshWindow with its clock and sleep injected (its offline test drives these). */
export async function freshWindowWith(now: () => Promise<Date>, sleep: (ms: number) => Promise<void>,
  { notBeforeSec = 5, notAfterSec = 35 }: WindowOptions = {}): Promise<{ second: number }> {
  if (!(notBeforeSec >= 0 && notBeforeSec <= notAfterSec && notAfterSec <= 59)) throw new Error('gateway clock: bad window');
  // Two passes at most: the second only absorbs a read that landed a second early.
  for (let pass = 0; pass < 2; pass++) {
    const s = (await now()).getUTCSeconds();
    if (s >= notBeforeSec && s <= notAfterSec) return { second: s };
    await sleep((s > notAfterSec ? 60 - s + notBeforeSec : notBeforeSec - s) * 1000);
  }
  const s = (await now()).getUTCSeconds();
  if (s < notBeforeSec || s > notAfterSec) throw new Error(`gateway clock: second ${s} still outside [${notBeforeSec}, ${notAfterSec}]`);
  return { second: s };
}

/**
 * Waits until the gateway's clock is between `notBeforeSec` and `notAfterSec`
 * seconds into its minute (defaults 5 and 35), so at least 25 s of the minute
 * remain: with s the gateway's second, it waits 60 - s + notBeforeSec s when
 * s > notAfterSec, notBeforeSec - s s when s < notBeforeSec, else not at all;
 * then re-reads and returns the second.
 */
export function freshWindow(request: APIRequestContext, options: WindowOptions = {}): Promise<{ second: number }> {
  return freshWindowWith(() => gatewayNow(request), (ms) => new Promise((r) => setTimeout(r, ms)), options);
}
