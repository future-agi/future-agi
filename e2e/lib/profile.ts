// Which gateway config the stack runs, declared once in
// E2E_GATEWAY_PROFILE and read by bin/e2e (the mounted file), the managed-mock
// inspector (which checks the running mount and the file) and the flows (which
// read their expectations from the declaration, never from the stack).
//   stock  (unset or `stock`) e2e/stack/gateway.e2e.yaml, every stack
//   alt    e2e/stack/gateway.alt.yaml = stock deep-merged with ALT_DELTA,
//          Standalone only: node settings the stock e2e gateway does not have
//          (request bodies logged, the cache on, other timeouts), so a flow that
//          displays node values can tell the two apart.
// Flows that need a profile carry the tag `@gw-profile-<name>`; lib/quarantine.ts
// leaves out every other profile's tag.
import { parseDocument } from 'yaml';

export type GatewayProfile = 'stock' | 'alt';
export const GATEWAY_PROFILES: readonly GatewayProfile[] = ['stock', 'alt'];

/** Repo-relative config file per profile (bin/e2e exports it as AGENTCC_CONFIG_PATH). */
export const PROFILE_FILES: Record<GatewayProfile, string> = {
  stock: 'e2e/stack/gateway.e2e.yaml',
  alt: 'e2e/stack/gateway.alt.yaml',
};

export const ALT_DELTA = {
  cache: { enabled: true },
  logging: { level: 'warn', request_logging: { enabled: true, include_bodies: true } },
  server: { read_timeout: '7s', idle_timeout: '90s' },
} as const;

/** The declared profile; anything but the known names throws (no silent stock). */
export function gatewayProfile(value = process.env.E2E_GATEWAY_PROFILE): GatewayProfile {
  const profile = value === undefined || value === '' ? 'stock' : value;
  if (!(GATEWAY_PROFILES as readonly string[]).includes(profile)) {
    throw new Error(`E2E_GATEWAY_PROFILE=${JSON.stringify(value)} is not one of ${GATEWAY_PROFILES.join(', ')}`);
  }
  return profile as GatewayProfile;
}

/** The tag a flow carries when it runs on `profile` only. */
export const profileTag = (profile: GatewayProfile) => `@gw-profile-${profile}`;

function deepMerge(base: unknown, delta: unknown): unknown {
  if (!delta || typeof delta !== 'object' || Array.isArray(delta) || !base || typeof base !== 'object' || Array.isArray(base)) {
    return structuredClone(delta);
  }
  const out: Record<string, unknown> = structuredClone(base as Record<string, unknown>);
  for (const [key, value] of Object.entries(delta)) out[key] = deepMerge(out[key], value);
  return out;
}

/** Parsed strictly: duplicate keys, aliases and `$` (env expansion) are refused. */
function strictYaml(source: string, name: string): unknown {
  const doc = parseDocument(source, { uniqueKeys: true });
  if (doc.errors.length) throw new Error(`STOP: managed mock gateway profile ${name} is invalid YAML`);
  let value: unknown;
  try { value = doc.toJS({ maxAliasCount: 0 }); }
  catch { throw new Error(`STOP: managed mock gateway profile ${name} uses YAML aliases`); }
  if (JSON.stringify(value).includes('$')) throw new Error(`STOP: managed mock gateway profile ${name} is environment-dependent`);
  return value;
}

const canonical = (value: unknown): unknown => (Array.isArray(value) ? value.map(canonical)
  : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort()
    .map((k) => [k, canonical((value as Record<string, unknown>)[k])])) : value);

/** The alt file must be exactly the stock file plus ALT_DELTA: an extra
 * section, another level or any other key is STOP. */
export function validateAltProfile(stockSource: string, altSource: string): void {
  const expected = deepMerge(strictYaml(stockSource, 'stock'), ALT_DELTA);
  const actual = strictYaml(altSource, 'alt');
  if (JSON.stringify(canonical(actual)) !== JSON.stringify(canonical(expected))) {
    throw new Error('STOP: managed mock gateway profile alt is not stock plus the declared delta');
  }
}

/** The alt file's content, as committed (its offline test checks the file equals this). */
export function altConfig(stockSource: string): unknown {
  return deepMerge(strictYaml(stockSource, 'stock'), ALT_DELTA);
}

/** What GET /-/config (agentcc-gateway server.go configHandler) shows of a profile:
 * every field differs between the two, so a run on the wrong stack fails loudly.
 * Read from futureagi/agentcc-gateway:e2esa started on each file (2026-10-22). */
export interface NodeFingerprint {
  logLevel: string; includeBodies: boolean; requestLogging: boolean;
  readTimeoutMs: number; idleTimeoutMs: number; cacheEnforced: boolean;
}
export const PROFILE_FINGERPRINTS: Record<GatewayProfile, NodeFingerprint> = {
  stock: { logLevel: 'info', includeBodies: false, requestLogging: true, readTimeoutMs: 5000, idleTimeoutMs: 120000,
    cacheEnforced: false },
  alt: { logLevel: 'warn', includeBodies: true, requestLogging: true, readTimeoutMs: 7000, idleTimeoutMs: 90000,
    cacheEnforced: true },
};

export function nodeFingerprint(config: Record<string, any>): NodeFingerprint {
  return { logLevel: config.logging?.level, includeBodies: config.logging?.include_bodies,
    requestLogging: config.logging?.request_logging, readTimeoutMs: config.server?.read_timeout_ms,
    idleTimeoutMs: config.server?.idle_timeout_ms,
    cacheEnforced: Array.isArray(config.org_sections_enforced) && config.org_sections_enforced.includes('cache') };
}
