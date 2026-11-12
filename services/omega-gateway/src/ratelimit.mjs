// Rate limits for the gateway (CP P5, READINESS L03).
//
// Token buckets keyed on the Future AGI identity in the caller's checked assertion, chosen
// by the manifest's rate class:
//
//   read            30/s, burst 60, per (user, workspace)
//   write            5/s, burst 10, per (user, workspace)
//   model-start     20/min per (user, workspace) AND 120/min per workspace
//   sandbox-create  the write bucket of (user, workspace) AND 30/min per workspace
//   public-share    60/min per client IP (closed until CP wave 3)
//   channel         40/s, burst 120, per (user, workspace): the conversation computer's terminal
//                   stream and input (CX-WALL, X-1), which a shell sends about once per keystroke;
//                   the identity is the verified key that minted the channel token
//
// "N/min" is a bucket of N that refills N a minute: an idle workspace may create 30 sandboxes
// at once, then one every 2 s (READINESS L03's provisional sandbox number; the same reading as
// model-start's 20/min). A strict "at most 30 in any 60 s" would need a sliding window, which
// these buckets and the Redis script do not implement. `sandbox-create` is a gateway class the
// route manifest cannot name (its rate classes are shared with EE's copy); rate-class.mjs
// says which route draws on it. Its user bucket is the write bucket itself (`bucket: "write"`),
// so a create is also a write and never gets a second write burst of its own.
//
// A request consumes one token from every bucket of its class or from none, so a refusal
// by the workspace bucket never spends the user's token. Over the limit the gateway answers
// 429 with `Retry-After`, the whole seconds until the most limiting bucket has a token.
//
// Buckets live in Redis when the gateway runs as more than one replica (ratelimit-redis.mjs,
// required in cloud mode), so a limit holds whichever replica a request reaches. If Redis
// fails or is slow, each class does what owner decision OD-07 says (READINESS CH-08):
//
//   read, public-share   fall back to this replica's own buckets (reads keep working; the
//                        limit weakens to per replica for the outage)
//   channel              falls back too, so an open shell keeps working: one token's requests
//                        reach only the replica that carried its mint (channel-token/bindings.mjs),
//                        but one person with open terminals in conversations pinned to N replicas
//                        gets up to N times the budget for the outage (at most one per conversation
//                        whose computer they run; X-1 review F-4)
//   write, model-start,  refused (`unavailable`, the server answers 503 with Retry-After), so
//   sandbox-create       N replicas never turn a user's budget into N times the budget
//
// Any class not named as a fallback class is refused (fail closed). Every fallback and every
// refusal is counted, and logged at most once a minute each.
import crypto from "node:crypto";
import { TRIGGER_BUDGETS } from "./trigger-budget-contract.mjs";

const WRITE_USER = Object.freeze({ scope: "user", capacity: 10, perSecond: 5 });

export const RATE_CLASSES = Object.freeze({
  read: Object.freeze([{ scope: "user", capacity: 60, perSecond: 30 }]),
  write: Object.freeze([WRITE_USER]),
  "model-start": Object.freeze([
    { scope: "user", capacity: 20, perSecond: 20 / 60 },
    { scope: "workspace", capacity: 120, perSecond: 120 / 60 },
  ]),
  "sandbox-create": Object.freeze([
    Object.freeze({ ...WRITE_USER, bucket: "write" }),
    { scope: "workspace", capacity: 30, perSecond: 30 / 60 },
  ]),
  // Anonymous signed-event verification: never key this on an unverified trigger URL.
  "trigger-ingress": Object.freeze([
    {
      scope: "ip",
      capacity: TRIGGER_BUDGETS.sourceIpPerMinute,
      perSecond: TRIGGER_BUDGETS.sourceIpPerMinute / 60,
    },
  ]),
  "public-share": Object.freeze([
    { scope: "ip", capacity: 60, perSecond: 60 / 60 },
  ]),
  channel: Object.freeze([{ scope: "user", capacity: 120, perSecond: 40 }]),
});

// OD-07: the classes that may fall back to per-replica buckets when the shared store fails.
export const FALLBACK_CLASSES = Object.freeze([
  "read",
  "public-share",
  "channel",
]);
// What a refused write or model start tells the browser to wait: about the Redis client's
// reconnect backoff (500 ms) plus one command deadline, rounded up.
export const STORE_OUTAGE_RETRY_SECONDS = 2;

const KEY_PREFIX = "omega-gw:rl:v1";
const MAX_MEMORY_BUCKETS = 200_000;

function digest(value) {
  return crypto
    .createHash("sha256")
    .update(String(value))
    .digest("base64url")
    .slice(0, 22);
}

/**
 * The buckets one request draws on. Raw identifiers never become keys; the hash tag
 * (`{...}`) keeps every bucket of one request in one Redis Cluster slot.
 */
export function bucketsFor(rateClass, identity, classes = RATE_CLASSES) {
  const specs = classes[rateClass];
  if (!specs) throw new Error(`Unknown rate class ${rateClass}`);
  return specs.map((spec) => {
    let tag;
    let subject;
    if (spec.scope === "ip") {
      tag = `ip:${digest(identity.ip)}`;
      subject = "-";
    } else {
      tag = `ws:${digest(`${identity.orgId}\u0000${identity.workspaceId}`)}`;
      subject = spec.scope === "user" ? digest(identity.sub) : "-";
    }
    return {
      // `bucket` names a bucket another class owns, so both classes draw on the same tokens.
      key: `${KEY_PREFIX}:{${tag}}:${spec.bucket ?? rateClass}:${spec.scope}:${subject}`,
      capacity: spec.capacity,
      perMs: spec.perSecond / 1000,
    };
  });
}

/** This replica's own buckets: the local-mode store and the fallback when Redis fails. */
export function createMemoryStore({ maxBuckets = MAX_MEMORY_BUCKETS } = {}) {
  const buckets = new Map();
  let takes = 0;
  function sweep(now) {
    for (const [key, state] of buckets) {
      if (state.tokens + (now - state.ts) * state.perMs >= state.capacity)
        buckets.delete(key);
    }
    // Still over the bound after dropping full buckets: drop the oldest.
    for (const key of buckets.keys()) {
      if (buckets.size <= maxBuckets) break;
      buckets.delete(key);
    }
  }
  return {
    kind: "memory",
    size: () => buckets.size,
    async take(requested, now = Date.now()) {
      if (++takes % 1_000 === 0 || buckets.size > maxBuckets) sweep(now);
      let waitMs = 0;
      const levels = requested.map((bucket) => {
        const state = buckets.get(bucket.key);
        const tokens =
          state === undefined
            ? bucket.capacity
            : Math.min(
                bucket.capacity,
                state.tokens + Math.max(0, now - state.ts) * bucket.perMs,
              );
        if (tokens < 1)
          waitMs = Math.max(waitMs, Math.ceil((1 - tokens) / bucket.perMs));
        return tokens;
      });
      if (waitMs > 0) return { allowed: false, retryAfterMs: waitMs };
      requested.forEach((bucket, index) => {
        buckets.delete(bucket.key);
        buckets.set(bucket.key, {
          tokens: levels[index] - 1,
          ts: now,
          capacity: bucket.capacity,
          perMs: bucket.perMs,
        });
      });
      return { allowed: true, retryAfterMs: 0 };
    },
    close() {},
  };
}

/**
 * @param {{ store?: { kind: string, take(buckets: object[], now?: number): Promise<{allowed: boolean, retryAfterMs: number}>, close(): void, warm?(): void },
 *           fallback?: object, classes?: object, fallbackClasses?: readonly string[], log?: (event: object) => void }} [options]
 */
export function createRateLimiter({
  store = createMemoryStore(),
  fallback = createMemoryStore(),
  classes = RATE_CLASSES,
  fallbackClasses = FALLBACK_CLASSES,
  log = () => {},
} = {}) {
  const stats = { allowed: 0, refused: 0, fallbacks: 0, unavailable: 0 };
  const lastLog = { fallback: 0, refuse: 0 };
  const logOutage = (action, error) => {
    const now = Date.now();
    if (now - lastLog[action] <= 60_000) return;
    lastLog[action] = now;
    log({
      event:
        action === "fallback"
          ? "omega-gateway.ratelimit.fallback"
          : "omega-gateway.ratelimit.store-unavailable",
      store: store.kind,
      error: error instanceof Error ? error.message : String(error),
      fallbacks: stats.fallbacks,
      unavailable: stats.unavailable,
    });
  };
  // Open the shared store's connection now, so a fresh replica's first write is not refused
  // while the connection is still opening.
  try {
    store.warm?.();
  } catch {}
  return {
    stats,
    storeKind: store.kind,
    /**
     * @param {string} rateClass manifest rate class
     * @param {{ sub?: string, orgId?: string, workspaceId?: string, ip?: string }} identity
     * @returns {Promise<{ allowed: boolean, unavailable?: true, retryAfterSeconds: number, store: string }>}
     */
    async take(rateClass, identity) {
      const buckets = bucketsFor(rateClass, identity, classes);
      let result;
      let used = store.kind;
      try {
        result = await store.take(buckets);
      } catch (error) {
        if (!fallbackClasses.includes(rateClass)) {
          // OD-07: writes and model starts fail closed; no token is spent anywhere.
          stats.unavailable += 1;
          logOutage("refuse", error);
          return {
            allowed: false,
            unavailable: true,
            retryAfterSeconds: STORE_OUTAGE_RETRY_SECONDS,
            store: "unavailable",
          };
        }
        stats.fallbacks += 1;
        used = `${fallback.kind}-fallback`;
        logOutage("fallback", error);
        result = await fallback.take(buckets);
      }
      if (result.allowed) stats.allowed += 1;
      else stats.refused += 1;
      return {
        allowed: result.allowed,
        retryAfterSeconds: result.allowed
          ? 0
          : Math.max(1, Math.ceil(result.retryAfterMs / 1000)),
        store: used,
      };
    },
    close() {
      store.close();
      if (fallback !== store) fallback.close();
    },
  };
}
