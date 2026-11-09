// CX-WALL (X-1): what this gateway knows about each channel token, learned from EE's own mint answer.
//
// A `channel` route is admitted only for a token this gateway saw EE mint, for the same bound path
// values (the conversation), before it expires. Each entry also keeps the verified claims of the key
// that minted it (principal, org, workspace, role, products), so the role floor, the product wall and
// the rate limit apply to every channel request as they did to the mint.
//
// Held in this process's memory, by the token's SHA-256 only: the value itself is never stored, logged
// or echoed. The expiry follows EE's: its mint answer, then each `event: channel` renewal that passes
// through on the token's own terminal stream; EE's refusal (401, or 409 `computer_stopped`, or an
// `event: end` for either) forgets the entry. EE stays authoritative for every check (the person's
// access, the computer generation, the terminal): this is an earlier, stricter door, never a looser one.
//
// One replica's memory: with several gateway replicas, a channel request must reach the replica that
// carried its mint (sticky on the conversation id, as EE's own single-replica token store already
// requires); a miss is refused 401 `channel_token_invalid` and the client mints again.
//
// Bounded twice: at most `perPrincipal` live entries for one principal (sub, org, workspace) and one
// computer, as EE keeps (its ninth mint retires the oldest, so the gateway forgets the same one), and
// `maxEntries` in all. A person minting at the mint's rate therefore holds 8 entries per computer here,
// not one per mint, and cannot push other people's bindings out of the global bound (X-1 review F-6).
import crypto from "node:crypto";

export const DEFAULT_MAX_ENTRIES = 50_000;
/** EE's own bound: live grants per principal and computer (CX-BRIDGE channel-tokens.ts). */
export const DEFAULT_PER_PRINCIPAL = 8;

const digest = (token) => crypto.createHash("sha256").update(token).digest("hex");

/** The claims a channel request is judged by, copied from the verified key that minted its token. */
function keptClaims(claims) {
  const kept = {};
  for (const name of ["sub", "org_id", "workspace_id", "role", "products", "rollout_epoch"]) {
    if (claims?.[name] !== undefined) kept[name] = claims[name];
  }
  return Object.freeze(kept);
}

/** The principal-and-computer group a binding counts against. */
const groupOf = (claims, computerId) => JSON.stringify([claims?.sub ?? null, claims?.org_id ?? null, claims?.workspace_id ?? null, computerId ?? null]);

/**
 * @param {{ now?: () => number, maxEntries?: number, perPrincipal?: number }} [options]
 */
export function createChannelBindings({ now = Date.now, maxEntries = DEFAULT_MAX_ENTRIES, perPrincipal = DEFAULT_PER_PRINCIPAL } = {}) {
  const entries = new Map();
  // group -> digests in mint order. Every removal goes through drop(), so the two maps never disagree.
  const groups = new Map();

  const drop = (key) => {
    const entry = entries.get(key);
    if (entry === undefined) return;
    entries.delete(key);
    const members = groups.get(entry.group);
    members?.delete(key);
    if (members?.size === 0) groups.delete(entry.group);
  };

  const prune = () => {
    const at = now();
    for (const [key, entry] of entries) if (entry.expiresAtMs <= at) drop(key);
    // Still full: the oldest entries go first (a Map keeps insertion order); their clients mint again.
    while (entries.size >= maxEntries) drop(entries.keys().next().value);
  };

  return {
    /**
     * Learn a token from EE's mint answer. `bound` holds the mint path's parameter values.
     * Returns false (and learns nothing) for an answer that is not a usable token.
     */
    remember({ token, prefix, bound, conversationId, computerId, generation, expiresAt, claims }) {
      const expiresAtMs = Date.parse(expiresAt);
      if (typeof token !== "string" || !Number.isFinite(expiresAtMs) || expiresAtMs <= now()) return false;
      const key = digest(token);
      drop(key);
      const kept = keptClaims(claims);
      const group = groupOf(kept, computerId);
      const members = groups.get(group) ?? new Set();
      // The same principal's oldest entries for this computer go first, as EE retires them.
      while (members.size >= perPrincipal) drop(members.values().next().value);
      if (entries.size >= maxEntries) prune();
      entries.set(key, {
        prefix, bound: Object.freeze({ ...bound }), conversationId, computerId, generation, expiresAtMs, claims: kept, group,
      });
      members.add(key);
      groups.set(group, members);
      return true;
    },
    /** The live entry for this token, or undefined (unknown or expired; an expired one is dropped). */
    find(token) {
      if (typeof token !== "string") return undefined;
      const key = digest(token);
      const entry = entries.get(key);
      if (entry === undefined) return undefined;
      if (entry.expiresAtMs <= now()) {
        drop(key);
        return { expired: true };
      }
      return entry;
    },
    /** EE renewed it (an `event: channel` on its own stream). */
    renew(token, expiresAt) {
      const entry = entries.get(digest(token));
      const expiresAtMs = Date.parse(expiresAt);
      if (entry !== undefined && Number.isFinite(expiresAtMs) && expiresAtMs > entry.expiresAtMs) entry.expiresAtMs = expiresAtMs;
    },
    /** EE refused it, or its computer ended. */
    forget(token) {
      drop(digest(token));
    },
    size: () => entries.size,
    /** Live and expired-but-not-yet-dropped entries one principal holds for one computer (tests, metrics). */
    heldBy: (claims, computerId) => groups.get(groupOf(keptClaims(claims), computerId))?.size ?? 0,
  };
}
