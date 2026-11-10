import { createHash } from 'node:crypto';
import type { APIRequestContext } from '@playwright/test';

// Transport budget, not a storage poll or a Playwright test retry. The collector
// refuses before enqueue: ingest.go writes Retry-After for busy/unavailable,
// but deliberately omits it for monthly quota 429s and auth infrastructure 503s.
const DELIVERY_MS = 30_000;
const MAX_ATTEMPTS = 4;

export interface OtlpRetryDiagnostic {
  event: 'otlp-delivery-retry';
  payloadDigest: string;
  attempt: number;
  nextAttempt: number;
  status: number;
  delayMs: number;
  elapsedMs: number;
  remainingMs: number;
}
export interface OtlpDeliveryRuntime {
  monotonicNow(): number;
  wallNow(): number;
  sleep(ms: number): Promise<void>;
  diagnostic(entry: OtlpRetryDiagnostic): void;
}
const runtime: OtlpDeliveryRuntime = {
  monotonicNow: () => performance.now(),
  wallNow: () => Date.now(),
  sleep: ms => new Promise(resolve => setTimeout(resolve, ms)),
  // Playwright retains stdout in the report, including recovered refusals.
  diagnostic: entry => console.info(JSON.stringify(entry)),
};

/** Delta-seconds or HTTP-date only. Date.parse alone accepts malformed hints
 * such as '-1', '1.5', and bare years; those must not turn into immediate retries.
 * HTTP's obsolete RFC850/asctime date forms remain valid for recipients. */
export function retryAfterMs(header: string | undefined, wallNow: number): number | undefined {
  if (header === undefined) return undefined;
  const value = header.trim();
  if (/^\d+$/.test(value)) {
    const ms = Number(value) * 1000;
    return Number.isSafeInteger(ms) ? ms : undefined;
  }
  const day = '(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)';
  const month = '(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)';
  const time = '\\d{2}:\\d{2}:\\d{2}';
  const httpDate = new RegExp(`^(?:${day}, \\d{2} ${month} \\d{4} ${time} GMT|`
    + `(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), \\d{2}-${month}-\\d{2} ${time} GMT|`
    + `${day} ${month} (?: \\d|\\d{2}) ${time} \\d{4})$`);
  if (!httpDate.test(value)) return undefined;
  const parsed = Date.parse(value);
  const delay = Math.max(0, parsed - wallNow);
  return Number.isSafeInteger(delay) ? delay : undefined;
}

/** Deliver already-serialized bytes. Only an explicit retryable refusal may be
 * replayed; ambiguous transport failures and partial acceptance are terminal.
 * Runtime injection is for pure harness tests, never a per-flow retry knob. */
export async function deliverOtlp(req: APIRequestContext,
  request: { url: string; headers: Record<string, string>; data: string },
  assertSuccess: (body: string) => void, clock: OtlpDeliveryRuntime = runtime): Promise<void> {
  const { url, data } = request;
  const headers = { ...request.headers };
  const payloadDigest = createHash('sha256').update(data).digest('hex');
  const started = clock.monotonicNow();
  const deadline = started + DELIVERY_MS;
  let attempts = 0;
  let lastStatus: number | undefined;
  const exhausted = () => new Error(`OTLP delivery budget exhausted: attempts=${attempts} `
    + `elapsedMs=${Math.ceil(clock.monotonicNow() - started)} lastStatus=${lastStatus ?? 'none'}`);

  while (true) {
    const remaining = deadline - clock.monotonicNow();
    if (remaining <= 0 || attempts >= MAX_ATTEMPTS) throw exhausted();
    attempts++;
    // Do not reflect transport exception text: Playwright may include headers
    // or server-controlled content. Never retry an ambiguous transport outcome.
    const res = await req.post(url, { headers: { ...headers }, data,
      timeout: remaining, maxRetries: 0, failOnStatusCode: false, maxRedirects: 0,
    }).catch(() => { throw new Error(`OTLP transport failed: attempt=${attempts}`); });
    let delay: number;
    let notBefore: number;
    try {
      lastStatus = res.status();
      if (clock.monotonicNow() >= deadline) throw exhausted();
      if (lastStatus >= 200 && lastStatus < 300) {
        const body = await res.text().catch(() => { throw new Error('OTLP receipt read failed'); });
        if (clock.monotonicNow() >= deadline) throw exhausted();
        assertSuccess(body);
        return;
      }
      const hint = retryAfterMs(res.headers()['retry-after'], clock.wallNow());
      if ((lastStatus !== 503 && lastStatus !== 429) || hint === undefined) {
        throw new Error(`collector HTTP ${lastStatus}`);
      }
      delay = hint;
      notBefore = clock.monotonicNow() + delay;
      if (attempts >= MAX_ATTEMPTS || notBefore >= deadline) throw exhausted();
    } finally {
      // Release every response, but never dispose the caller-owned context or
      // replace the primary receipt/refusal error with a cleanup failure.
      await res.dispose().catch(() => {});
    }
    if (clock.monotonicNow() >= deadline) throw exhausted();
    clock.diagnostic({ event: 'otlp-delivery-retry', payloadDigest, attempt: attempts,
      nextAttempt: attempts + 1, status: lastStatus, delayMs: delay,
      elapsedMs: clock.monotonicNow() - started, remainingMs: deadline - clock.monotonicNow() });
    // Recheck after scheduling (including an early timer wake); never shorten
    // the collector's not-before time to squeeze into the delivery budget.
    while (clock.monotonicNow() < notBefore) {
      await clock.sleep(notBefore - clock.monotonicNow());
    }
  }
}
