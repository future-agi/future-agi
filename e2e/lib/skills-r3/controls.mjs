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
      let release; let reject; let reached;
      const waiting = new Promise((resolve, fail) => { release = resolve; reject = fail; });
      // Register rejection handling even before a request reaches the barrier.
      waiting.catch(() => {});
      const arrival = new Promise((resolve) => { reached = resolve; });
      this.#entries.set(name, { waiting, arrival, release, reject, reached, arrivals: 0, released: false });
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
    for (const entry of this.#entries.values()) entry.reject(new Error('Fixture teardown'));
    this.#closed = true;
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
  constructor(runId) { this.runId = token(runId, 'run'); this.ownerCapability = randomUUID(); }
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
    const receipts = [];
    for (const [id, resource] of [...this.#resources].reverse()) {
      const receipt = { id, cancellation: 'unknown', settlement: 'unknown', destruction: 'unknown' };
      try { await resource.cancel(); receipt.cancellation = 'requested'; } catch { /* retain uncertainty */ }
      try { receipt.settlement = await resource.settle() === true ? 'settled' : 'unknown'; } catch { /* retain uncertainty */ }
      try { await resource.terminate(); } catch { /* destruction probe is authoritative */ }
      try { receipt.destruction = await resource.destroyed() === true ? 'confirmed' : 'unknown'; } catch { /* retain uncertainty */ }
      receipts.push(receipt);
    }
    return { receipts, qualified: receipts.every((r) => r.settlement === 'settled' && r.destruction === 'confirmed') };
  }
}
