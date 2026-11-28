import { randomUUID } from 'node:crypto';
import { requireValue, token } from './canonical.mjs';

/** Deterministic barriers, not sleeps. Close rejects waiters so teardown cannot hang. */
export class Barriers {
  #entries = new Map();
  #closed = false;
  #entry(name) {
    token(name, 'barrier');
    requireValue(!this.#closed, 'Barriers closed');
    if (!this.#entries.has(name)) {
      let release; let reject; let reached; let rejectArrival;
      const waiting = new Promise((resolve, fail) => { release = resolve; reject = fail; });
      const arrival = new Promise((resolve, fail) => { reached = resolve; rejectArrival = fail; });
      // Either side may be created before its observer attaches.
      waiting.catch(() => {});
      arrival.catch(() => {});
      this.#entries.set(name, { waiting, arrival, release, reject, reached, rejectArrival, arrivals: 0, released: false });
    }
    return this.#entries.get(name);
  }
  async wait(name) {
    const entry = this.#entry(name);
    entry.arrivals += 1;
    entry.reached();
    await entry.waiting;
  }
  reached(name) { return this.#entry(name).arrival; }
  release(name) {
    const entry = this.#entry(name);
    entry.released = true;
    entry.release();
  }
  state(name) {
    const entry = this.#entry(name);
    return { arrivals: entry.arrivals, released: entry.released };
  }
  close() {
    this.#closed = true;
    for (const entry of this.#entries.values()) {
      const error = new Error('Fixture teardown');
      entry.reject(error);
      entry.rejectArrival(error);
    }
  }
}

/** Test clock to pass through an integration-owned test-image seam, never production SQL. */
export class ScopedClock {
  #now;
  constructor(scope, instant) {
    this.scope = token(scope, 'clock scope');
    this.#now = Date.parse(instant);
    requireValue(Number.isFinite(this.#now), 'Clock instant required');
  }
  now() { return new Date(this.#now).toISOString(); }
  advance(scope, milliseconds) {
    requireValue(scope === this.scope && Number.isSafeInteger(milliseconds) && milliseconds >= 0, 'Invalid scoped clock advance');
    this.#now += milliseconds;
    return this.now();
  }
}

/** Only caller-registered owned resources can be cancelled/terminated. No process discovery/killall. */
export class OwnedResources {
  #resources = new Map();
  #closed = false;
  #probeTimeoutMs;
  constructor(runId, { probeTimeoutMs = 5_000 } = {}) {
    this.runId = token(runId, 'run');
    this.ownerCapability = randomUUID();
    // Four sequential probes per resource must fit the runner's 30-second teardown.
    requireValue(Number.isSafeInteger(probeTimeoutMs) && probeTimeoutMs > 0 && probeTimeoutMs <= 5_000, 'Probe timeout must be between 1 and 5000 ms');
    this.#probeTimeoutMs = probeTimeoutMs;
  }
  async #probe(callback) {
    let timer;
    try {
      return await Promise.race([
        Promise.resolve().then(callback),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Teardown probe deadline')), this.#probeTimeoutMs); }),
      ]);
    } finally { clearTimeout(timer); }
  }
  register({ id, ownerCapability, cancel, settle, terminate, destroyed }) {
    requireValue(!this.#closed && ownerCapability === this.ownerCapability, 'Resource ownership mismatch');
    token(id, 'resource');
    requireValue(!this.#resources.has(id), 'Resource already registered');
    requireValue([cancel, settle, terminate, destroyed].every((fn) => typeof fn === 'function'), 'All teardown probes are required');
    this.#resources.set(id, { cancel, settle, terminate, destroyed });
  }
  async teardown() {
    requireValue(!this.#closed, 'Teardown already performed');
    this.#closed = true;
    // Start every owned resource independently. One outage must not consume the
    // outer deadline before another resource even receives its termination call.
    // Promise.all retains reverse-registration receipt order, not completion order.
    const receipts = await Promise.all([...this.#resources].reverse().map(async ([id, resource]) => {
      const receipt = { id, cancellation: 'unknown', settlement: 'unknown', destruction: 'unknown' };
      try { await this.#probe(() => resource.cancel()); receipt.cancellation = 'requested'; } catch { /* retain uncertainty */ }
      try { receipt.settlement = await this.#probe(() => resource.settle()) === true ? 'settled' : 'unknown'; } catch { /* retain uncertainty */ }
      try { await this.#probe(() => resource.terminate()); } catch { /* destruction probe is authoritative */ }
      try { receipt.destruction = await this.#probe(() => resource.destroyed()) === true ? 'confirmed' : 'unknown'; } catch { /* retain uncertainty */ }
      return receipt;
    }));
    return { receipts, qualified: receipts.every((r) => r.settlement === 'settled' && r.destruction === 'confirmed') };
  }
}
