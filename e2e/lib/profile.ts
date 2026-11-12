// H5: which gateway config the stack runs, declared once in
// E2E_GATEWAY_PROFILE and read by bin/e2e (the mounted file), the managed-mock
// inspector (which checks the running mount and the file) and the flows (which
// read their expectations from the declaration, never from the stack).
//   stock        (unset or `stock`) e2e/stack/gateway.e2e.yaml, every stack
//   r7alt        e2e/stack/gateway.r7alt.yaml = stock deep-merged with R7ALT_DELTA,
//                Standalone only: node settings the stock e2e gateway does not have
//                (request bodies logged, the cache on, other timeouts), so a flow that
//                displays node values can tell the two apart.
//   mcp          stock + MCP_DELTA: the gateway serves MCP with a fixed test state
//                key and names no MCP upstream; mcp-mock is the one upstream, saved by
//                flows as an org server (MCP line's definition, unchanged).
//   mcp-catalog  stock + MCP_CATALOG_DELTA: MCP served over the catalogue the build
//                embeds, no upstream, no mock.
//   mcp-signin   stock + MCP_SIGNIN_DELTA: mcp, and the gateway an OAuth resource
//                server for MCP clients of the console's own authorization server.
//   mcp-otel     stock + MCP_OTEL_DELTA (walk W13): mcp, OTel on with bodies, content
//                mode full, the exporter at the otlp-sink capture service.
//   pricefeed    stock + PRICEFEED_DELTA (walk W05): the public price feed on for one
//                synthetic source.
//   onekey-cat   walk W06: the CAT-2 opt-in. Its delta is owed: no head names the
//                operator setting yet (e2e/stack/gateway-profiles.json).
// Every profile but stock is Standalone only. What a profile needs from the head
// (gateway config keys, overlays, mock services) is in gateway-profiles.json, which
// bin/e2e checks before anything starts. Flows that need a profile carry the tag
// `@gw-profile-<name>`; lib/quarantine.ts leaves out every other profile's tag.
import { parseDocument } from 'yaml';

/** Product flows have exhaustive tables over these three profiles. Keep this
 * contract independent of the walk harness's additional configurations. */
export type GatewayProfile = 'stock' | 'r7alt' | 'mcp';
export const PRODUCT_GATEWAY_PROFILES: readonly GatewayProfile[] = ['stock', 'r7alt', 'mcp'];
export type WalkGatewayProfile = GatewayProfile | 'mcp-catalog' | 'mcp-signin' | 'mcp-otel' | 'pricefeed'
  | 'onekey-cat' | 'cloud-mode';
export const GATEWAY_PROFILES: readonly WalkGatewayProfile[] = ['stock', 'r7alt', 'mcp', 'mcp-catalog', 'mcp-signin',
  'mcp-otel', 'pricefeed', 'onekey-cat', 'cloud-mode'];

/** Repo-relative config file per profile (bin/e2e exports it as AGENTCC_CONFIG_PATH). */
export const PROFILE_FILES: Record<WalkGatewayProfile, string> = {
  'cloud-mode': 'e2e/stack/gateway.e2e.yaml', // attach only; NOT ACCEPTED (contract 713)
  stock: 'e2e/stack/gateway.e2e.yaml',
  r7alt: 'e2e/stack/gateway.r7alt.yaml',
  mcp: 'e2e/stack/gateway.mcp.yaml',
  'mcp-catalog': 'e2e/stack/gateway.mcp-catalog.yaml',
  'mcp-signin': 'e2e/stack/gateway.mcp-signin.yaml',
  'mcp-otel': 'e2e/stack/gateway.mcp-otel.yaml',
  pricefeed: 'e2e/stack/gateway.pricefeed.yaml',
  'onekey-cat': 'e2e/stack/gateway.onekey-cat.yaml',
};

export const PROFILE_MCP_SIGN_IN: Record<WalkGatewayProfile, boolean> = {
  "stock": false,
  "r7alt": false,
  "mcp": false,
  "mcp-catalog": false,
  "mcp-signin": true,
  "mcp-otel": false,
  "pricefeed": false,
  "onekey-cat": false,
  "cloud-mode": false
};

export const PROFILE_REAL_CATALOG: Record<WalkGatewayProfile, boolean> = {
  "stock": true,
  "r7alt": true,
  "mcp": false,
  "mcp-catalog": true,
  "mcp-signin": false,
  "mcp-otel": false,
  "pricefeed": true,
  "onekey-cat": true,
  "cloud-mode": true
};

export const PROFILE_RUNS_MCP_MOCK: Record<WalkGatewayProfile, boolean> = {
  "stock": false,
  "r7alt": false,
  "mcp": true,
  "mcp-catalog": false,
  "mcp-signin": true,
  "mcp-otel": false,
  "pricefeed": false,
  "onekey-cat": false,
  "cloud-mode": false
};

export const R7ALT_DELTA = {
  cache: { enabled: true },
  logging: { level: 'warn', request_logging: { enabled: true, include_bodies: true } },
  server: { read_timeout: '7s', idle_timeout: '90s' },
} as const;

/** The MCP profiles' state key: a fixed test value (config.go: at least 32 bytes)
 * that seals nothing outside this stack. */
export const MCP_STATE_KEY = 'e2e-only-mcp-state-key-not-a-secret-0000';
export const MCP_DELTA = {
  mcp: { enabled: true, state_keys: [MCP_STATE_KEY] },
  connectors: { catalog_dir: '/opt/e2e/mcp/catalog' },
} as const;
export const MCP_CATALOG_DELTA = {
  mcp: { enabled: true, state_keys: [MCP_STATE_KEY] },
} as const;
export const MCP_SIGNIN_ISSUER = 'http://localhost:8100/agentcc/mcp-oauth';
export const MCP_SIGNIN_PUBLIC_URL = 'http://localhost:8090';
export const MCP_SIGNIN_DELTA = {
  mcp: { enabled: true, state_keys: [MCP_STATE_KEY], public_url: MCP_SIGNIN_PUBLIC_URL,
    oauth: { enabled: true, issuer: MCP_SIGNIN_ISSUER } },
  connectors: { catalog_dir: '/opt/e2e/mcp/catalog' },
} as const;
/** The OTLP capture sink the mcp-otel exporter sends to (gw/E2E-WALK-MOCKS-1
 * service `otlp-sink`, on the stack's default network; its read API is the
 * mocks lane's). Content mode full and bodies on; retention is the gateway's own
 * default until G16 names a retention setting (owed). */
export const OTLP_SINK_ENDPOINT = 'http://otlp-sink:4318';
export const MCP_OTEL_DELTA = {
  ...MCP_DELTA,
  otel: { enabled: true, service_name: 'agentcc-gateway-e2e', exporter: 'otlp', endpoint: OTLP_SINK_ENDPOINT,
    sample_rate: 1, include_bodies: true },
  privacy: { default_content_mode: 'full' },
} as const;
/** One synthetic allowlisted source (config public_price_feed_test.go on 12d24e74da). */
export const PRICEFEED_DELTA = {
  public_price_feed: { enabled: true, sources: [{ provider_id: 'e2e-synthetic', catalog_source: 'openrouter' }] },
} as const;

/** Each profile's whole difference from stock; null = owed (no head names it yet). */
export const PROFILE_DELTAS: Record<WalkGatewayProfile, Record<string, unknown> | null> = {
  stock: {}, r7alt: R7ALT_DELTA, mcp: MCP_DELTA, 'mcp-catalog': MCP_CATALOG_DELTA, 'mcp-signin': MCP_SIGNIN_DELTA,
  'mcp-otel': MCP_OTEL_DELTA, pricefeed: PRICEFEED_DELTA, 'onekey-cat': null, 'cloud-mode': null,
};

/** Whether the profile's gateway serves MCP (the console's MCP status then has to agree). */
export const PROFILE_SERVES_MCP: Record<WalkGatewayProfile, boolean> = {
  stock: false, r7alt: false, mcp: true, 'mcp-catalog': true, 'mcp-signin': true, 'mcp-otel': true,
  pricefeed: false, 'onekey-cat': false, 'cloud-mode': false,
};

/** Where an MCP upstream, a process or an outbound fetch could be named in the
 * gateway's mcp section (config.go MCPConfig): refused in every profile unless
 * that profile's own delta declares it (mcp-signin: oauth and public_url). */
const MCP_UPSTREAM_KEYS = ['servers', 'managed_servers', 'stdio', 'egress', 'oauth', 'oauth_adapters', 'django',
  'static', 'public_url', 'allow_anonymous_dev', 'dev_insecure_state'];

/** The product declaration: never silently treat a walk-only profile as stock.
 * Callers with exhaustive product expectations must fail before indexing them. */
export function gatewayProfile(value = process.env.E2E_PROFILE === 'cloud-mode' ? 'cloud-mode' : process.env.E2E_GATEWAY_PROFILE): GatewayProfile {
  const profile = value === undefined || value === '' ? 'stock' : value;
  if (!(PRODUCT_GATEWAY_PROFILES as readonly string[]).includes(profile)) {
    throw new Error(`E2E_GATEWAY_PROFILE=${JSON.stringify(value)} is not one of ${PRODUCT_GATEWAY_PROFILES.join(', ')}`);
  }
  return profile as GatewayProfile;
}

/** The full harness declaration, including walk-only profiles. */
export function walkGatewayProfile(value = process.env.E2E_PROFILE === 'cloud-mode' ? 'cloud-mode' : process.env.E2E_GATEWAY_PROFILE): WalkGatewayProfile {
  const profile = value === undefined || value === '' ? 'stock' : value;
  if (!(GATEWAY_PROFILES as readonly string[]).includes(profile)) {
    throw new Error(`E2E_GATEWAY_PROFILE=${JSON.stringify(value)} is not one of ${GATEWAY_PROFILES.join(', ')}`);
  }
  return profile as WalkGatewayProfile;
}

/** The tag a flow carries when it runs on `profile` only. */
export const profileTag = (profile: WalkGatewayProfile) => `@gw-profile-${profile}`;

// The new-org default, declared apart from the gateway profile because it is the
// API's setting, not the gateway's: E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS=1 on `up`
// gives the API AGENTCC_MANAGED_MODELS_ON_FOR_NEW_ORGS=1
// (agentcc/services/managed_models_default.py: a new organization's first org
// config turns Future AGI models on), through e2e/stack/docker-compose.e2e.yml
// and docker-compose.standalone-e2e.yml. Export the same value for `test`: flows
// tagged NEW_ORG_DEFAULT_TAG run only then (lib/quarantine.ts), and the inspector
// accepts the setting only then (lib/managed-mock.ts). Unset, the stack keeps
// the shipped default (off). A flow that needs a new org to start with Future AGI
// models off (GW-E2E-007: an admin turns the switch on) carries
// NEW_ORG_DEFAULT_OFF_TAG and never runs on a declared stack (lib/quarantine.ts).
export const NEW_ORG_DEFAULT_TAG = '@new-org-default';
export const NEW_ORG_DEFAULT_OFF_TAG = '@new-org-default-off';
export const newOrgDefaultDeclared = (value = process.env.E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS) => value === '1';

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

/** The content a profile's committed file must have: stock plus its delta. */
export function profileConfig(profile: WalkGatewayProfile, stockSource: string): unknown {
  const delta = PROFILE_DELTAS[profile];
  if (delta === null) throw new Error(`STOP: managed mock gateway profile ${profile} has no declared delta yet (owed)`);
  return deepMerge(strictYaml(stockSource, 'stock'), delta);
}

/** A profile's file must be exactly stock plus its delta. An MCP upstream its delta
 * does not declare, an OTel exporter other than the capture sink, or a price-feed
 * source other than the synthetic one is STOP by name; any other difference is
 * STOP as not stock plus the declared delta. */
export function validateProfileSource(profile: WalkGatewayProfile, stockSource: string, source: string): void {
  if (profile === 'stock') return;
  // r7alt predates walk profiles: retain its exact stock-plus-delta contract
  // (including error messages), rather than applying the new profile guards.
  if (profile === 'r7alt') return validateR7altProfile(stockSource, source);
  const expected = profileConfig(profile, stockSource);
  const actual = strictYaml(source, profile) as Record<string, any> | null;
  const mcp = actual && typeof actual === 'object' ? actual.mcp : undefined;
  const declared = Object.keys(((PROFILE_DELTAS[profile] as Record<string, any>)?.mcp ?? {}));
  if (mcp && typeof mcp === 'object' && MCP_UPSTREAM_KEYS.some((key) => !declared.includes(key) && key in mcp)) {
    throw new Error(`STOP: managed mock gateway profile ${profile} names an MCP upstream: the mock is the only one, saved as an org server`);
  }
  const otel = actual?.otel;
  if (otel && typeof otel === 'object' && otel.endpoint !== undefined && otel.endpoint !== OTLP_SINK_ENDPOINT) {
    throw new Error(`STOP: managed mock gateway profile ${profile} exports spans somewhere other than the capture sink`);
  }
  const feed = actual?.public_price_feed;
  if (feed && typeof feed === 'object' && JSON.stringify(canonical(feed.sources)) !==
      JSON.stringify(canonical(PRICEFEED_DELTA.public_price_feed.sources))) {
    throw new Error(`STOP: managed mock gateway profile ${profile} allowlists a price-feed source that is not the synthetic one`);
  }
  if (JSON.stringify(canonical(actual)) !== JSON.stringify(canonical(expected))) {
    throw new Error(`STOP: managed mock gateway profile ${profile} is not stock plus the declared delta`);
  }
}

/** The r7alt file must be exactly the stock file plus R7ALT_DELTA. */
export function validateR7altProfile(stockSource: string, r7altSource: string): void {
  const expected = deepMerge(strictYaml(stockSource, 'stock'), R7ALT_DELTA);
  const actual = strictYaml(r7altSource, 'r7alt');
  if (JSON.stringify(canonical(actual)) !== JSON.stringify(canonical(expected))) {
    throw new Error('STOP: managed mock gateway profile r7alt is not stock plus the declared delta');
  }
}

/** The r7alt file's content, as committed (its offline test checks the file equals this). */
export function r7altConfig(stockSource: string): unknown {
  return profileConfig('r7alt', stockSource);
}

/** The mcp file must be exactly the stock file plus MCP_DELTA. A server, process,
 * egress allowance, sign-in or other upstream setting in its mcp section is STOP
 * as an upstream that is not the mock (the mock is saved by flows as an org
 * server, never named here); any other difference is STOP as not stock plus the
 * declared delta. */
export function validateMcpProfile(stockSource: string, mcpSource: string): void {
  const expected = deepMerge(strictYaml(stockSource, 'stock'), MCP_DELTA);
  const actual = strictYaml(mcpSource, 'mcp') as Record<string, any> | null;
  const mcp = actual && typeof actual === 'object' ? actual.mcp : undefined;
  if (mcp && typeof mcp === 'object' && MCP_UPSTREAM_KEYS.some((key) => key in mcp)) {
    throw new Error('STOP: managed mock gateway profile mcp names an MCP upstream: the mock is the only one, saved as an org server');
  }
  if (JSON.stringify(canonical(actual)) !== JSON.stringify(canonical(expected))) {
    throw new Error('STOP: managed mock gateway profile mcp is not stock plus the declared delta');
  }
}

/** The mcp file's content, as committed (its offline test checks the file equals this). */
export function mcpConfig(stockSource: string): unknown {
  return deepMerge(strictYaml(stockSource, 'stock'), MCP_DELTA);
}

/** The mcp-signin file must be exactly the stock file plus MCP_SIGNIN_DELTA. Its
 * sign-in (mcp.oauth: enabled and the issuer only) and public URL are the declared
 * ones; any other MCP upstream, process, egress allowance, token channel or
 * sign-in setting in its mcp section is STOP as an upstream that is not the mock;
 * any other difference is STOP as not stock plus the declared delta. */
export function validateMcpSigninProfile(stockSource: string, source: string): void {
  const expected = deepMerge(strictYaml(stockSource, 'stock'), MCP_SIGNIN_DELTA);
  const actual = strictYaml(source, 'mcp-signin') as Record<string, any> | null;
  const mcp = actual && typeof actual === 'object' ? actual.mcp : undefined;
  const declared = ['oauth', 'public_url'];
  const oauth = mcp && typeof mcp === 'object' ? mcp.oauth : undefined;
  if (mcp && typeof mcp === 'object' && (MCP_UPSTREAM_KEYS.some((key) => !declared.includes(key) && key in mcp) ||
      (oauth && typeof oauth === 'object' && Object.keys(oauth).some((key) => !['enabled', 'issuer'].includes(key))))) {
    throw new Error('STOP: managed mock gateway profile mcp-signin names an MCP upstream: the mock is the only one, saved as an org server');
  }
  if (JSON.stringify(canonical(actual)) !== JSON.stringify(canonical(expected))) {
    throw new Error('STOP: managed mock gateway profile mcp-signin is not stock plus the declared delta');
  }
}

/** The mcp-signin file's content, as committed (its offline test checks the file equals this). */
export function mcpSigninConfig(stockSource: string): unknown {
  return deepMerge(strictYaml(stockSource, 'stock'), MCP_SIGNIN_DELTA);
}

/** The mcp-catalog file must be exactly the stock file plus MCP_CATALOG_DELTA. Any
 * MCP upstream, process, egress allowance or sign-in in its mcp section is STOP as
 * an upstream (this profile has none), and a connectors section is STOP as a
 * catalogue that is not the one the gateway embeds; any other difference is STOP
 * as not stock plus the declared delta. */
export function validateMcpCatalogProfile(stockSource: string, source: string): void {
  const expected = deepMerge(strictYaml(stockSource, 'stock'), MCP_CATALOG_DELTA);
  const actual = strictYaml(source, 'mcp-catalog') as Record<string, any> | null;
  const mcp = actual && typeof actual === 'object' ? actual.mcp : undefined;
  if (mcp && typeof mcp === 'object' && MCP_UPSTREAM_KEYS.some((key) => key in mcp)) {
    throw new Error('STOP: managed mock gateway profile mcp-catalog names an MCP upstream: it has none');
  }
  if (actual && typeof actual === 'object' && 'connectors' in actual) {
    throw new Error('STOP: managed mock gateway profile mcp-catalog names a connector catalogue: it reads the one the gateway embeds');
  }
  if (JSON.stringify(canonical(actual)) !== JSON.stringify(canonical(expected))) {
    throw new Error('STOP: managed mock gateway profile mcp-catalog is not stock plus the declared delta');
  }
}

/** The mcp-catalog file's content, as committed (its offline test checks the file equals this). */
export function mcpCatalogConfig(stockSource: string): unknown {
  return deepMerge(strictYaml(stockSource, 'stock'), MCP_CATALOG_DELTA);
}

/** What GET /-/config (agentcc-gateway server.go configHandler) shows of a profile.
 * Only r7alt changes these node values; every other profile's delta leaves server,
 * logging and cache as stock's, so they read as stock here and are told apart by
 * PROFILE_SERVES_MCP and the census's own reads. stock and r7alt: read from
 * futureagi/agentcc-gateway:e2esa started on each file (2026-10-22). */
export interface NodeFingerprint {
  logLevel: string; includeBodies: boolean; requestLogging: boolean;
  readTimeoutMs: number; idleTimeoutMs: number; cacheEnforced: boolean;
}
const STOCK_NODE: NodeFingerprint = { logLevel: 'info', includeBodies: false, requestLogging: true, readTimeoutMs: 5000,
  idleTimeoutMs: 120000, cacheEnforced: false };
export const PROFILE_FINGERPRINTS: Record<WalkGatewayProfile, NodeFingerprint> = {
  stock: STOCK_NODE,
  r7alt: { logLevel: 'warn', includeBodies: true, requestLogging: true, readTimeoutMs: 7000, idleTimeoutMs: 90000,
    cacheEnforced: true },
  mcp: STOCK_NODE, 'mcp-catalog': STOCK_NODE, 'mcp-signin': STOCK_NODE, 'mcp-otel': STOCK_NODE,
  pricefeed: STOCK_NODE, 'onekey-cat': STOCK_NODE, 'cloud-mode': STOCK_NODE,
};

export function nodeFingerprint(config: Record<string, any>): NodeFingerprint {
  return { logLevel: config.logging?.level, includeBodies: config.logging?.include_bodies,
    requestLogging: config.logging?.request_logging, readTimeoutMs: config.server?.read_timeout_ms,
    idleTimeoutMs: config.server?.idle_timeout_ms,
    cacheEnforced: Array.isArray(config.org_sections_enforced) && config.org_sections_enforced.includes('cache') };
}
