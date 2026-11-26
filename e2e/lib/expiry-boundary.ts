// Observing a stored expiry instant from the test runner. Pure and clock-injected
// so scripts/expiry-boundary.test.mjs can drive late wakes and stalled clocks.

export interface BoundaryClock {
  /** Epoch milliseconds, the scale stored expiry instants use. */
  wallNow(): number;
  /** Monotonic milliseconds; bounds the wait even if the wall clock stalls or steps. */
  monotonicNow(): number;
  sleep(ms: number): Promise<void>;
}

export const REAL_CLOCK: BoundaryClock = {
  wallNow: () => Date.now(),
  monotonicNow: () => performance.now(),
  sleep: ms => new Promise(resolve => setTimeout(resolve, ms)),
};

/**
 * Latest acceptable dispatch of the first post-expiry probe, measured from the
 * stored instant to the moment the request is handed to the client. Over ten
 * runs across the distributed and standalone stacks this wait dispatched 2-50 ms
 * after the target (the previous clock poll: 121-609 ms), so 1 s leaves room for
 * runner jitter while still failing a wake that has slept through the boundary.
 */
export const DISPATCH_LIMIT_MS = 1000;

/**
 * Sleep for the remaining real time, recheck the wall clock on every wake and
 * return the first wall-clock reading at or after `target`. Throws once
 * `budgetMs` of monotonic time has passed without the wall clock crossing.
 */
export async function waitForWallClock(target: number, budgetMs: number,
  clock: BoundaryClock = REAL_CLOCK): Promise<number> {
  const deadline = clock.monotonicNow() + budgetMs;
  for (;;) {
    const now = clock.wallNow();
    if (now >= target) return now;
    const budget = deadline - clock.monotonicNow();
    if (budget <= 0) {
      throw new Error(`wall clock read ${now}, still before expiry ${target}, after the ${budgetMs} ms wait budget`);
    }
    await clock.sleep(Math.min(target - now, budget));
  }
}

/** Null when the dispatch observes the boundary, otherwise why it does not. */
export function boundaryDispatchError(dispatchedAt: number, target: number,
  limitMs: number = DISPATCH_LIMIT_MS): string | null {
  if (dispatchedAt < target) return `probe dispatched ${target - dispatchedAt} ms before expiry ${target}`;
  if (dispatchedAt - target > limitMs) {
    return `probe dispatched ${dispatchedAt - target} ms after expiry ${target}, over the ${limitMs} ms limit`;
  }
  return null;
}

/**
 * Wait for `target`, then dispatch the first probe with no await in between.
 * Returns the dispatch wall-clock time and the probe's result; the caller
 * asserts on `boundaryDispatchError` and the result.
 */
export async function probeAtBoundary<T>(target: number, budgetMs: number,
  probe: () => Promise<T>, clock: BoundaryClock = REAL_CLOCK): Promise<{ dispatchedAt: number; result: T }> {
  await waitForWallClock(target, budgetMs, clock);
  const dispatchedAt = clock.wallNow();
  const result = await probe();
  return { dispatchedAt, result };
}
