// HR-JRN-03: a single closed matrix shared by W00 and its result validator.
// Routes: frontend/src/routes/paths.js; viewport/scheme contract: J00.L1.
import { configSwitchVerdicts, ZERO_ROW_TABLES } from './census';

// A missing backend feature is partial even when another row of its lane exists.
export const CENSUS_BACKEND_CHECKS = [
  ...(['storage', 'gateway'] as const).flatMap(lane => configSwitchVerdicts(null, lane, '').map(({ feature }) => ({ lane, feature }))),
  ...ZERO_ROW_TABLES.map(({ feature }) => ({ lane: 'storage', feature })),
  { lane: 'storage', feature: 'org config versions' },
  { lane: 'storage', feature: 'admission caps (AgentccCapActivation)' },
  { lane: 'storage', feature: 'trace content consent audit' },
  { lane: 'redis', feature: 'managed credits Redis state' },
  { lane: 'gateway', feature: 'MCP gateway served state (operator profile)' },
  { lane: 'api', feature: 'CAT-2 exact 34 IDs default-off' },
  { lane: 'gateway', feature: 'CAT-2 exact 34 IDs default-off' },
  { lane: 'gateway', feature: 'public_price_feed' },
  { lane: 'gateway', feature: 'public_price_feed route' },
] as const;
export const CENSUS_PHASES = ['before-key', 'after-key'] as const;
export const CENSUS_ROLES = ['owner', 'admin', 'member', 'viewer'] as const;
export const CENSUS_PAGES = ['models', 'billing', 'budgets', 'mcp', 'security', 'integrations', 'guardrails'] as const;
export const CENSUS_VIEWPORTS = [{ width: 1440, height: 900 }, { width: 390, height: 844 }] as const;
export const CENSUS_SCHEMES = ['light', 'dark'] as const;
export type CensusPhase = typeof CENSUS_PHASES[number];
export type CensusRole = typeof CENSUS_ROLES[number];
export type CensusPage = typeof CENSUS_PAGES[number];

// gw/ONEKEY-CAT-2 @ a86870b86bd448f3abfe30e80f5d0a3e314bb664:
// agentcc-gateway/internal/catalog/inclusive_family.go:15-34 (32 exact image
// identities) and :43-51 (two Lyria identities). Routers are NOT in this 34.
// No regex/family matching: aliases and batch variants are separate identities.
export const CAT2_IDS = [
  'google/gemini-4-argon', 'google/gemini-4-argon:batch',
  'google/gemini-3.8-flash', 'google/gemini-3.8-flash:batch',
  'google/gemini-3.7-flash', 'google/gemini-3.7-flash:batch',
  'google/gemini-3.6-flash', 'google/gemini-3.6-flash:batch',
  'google/gemini-3.5-flash-lite', 'google/gemini-3.5-flash-lite:batch',
  'google/gemini-3-pro-image', 'google/gemini-3.5-flash', 'google/gemini-3.5-flash:batch',
  'google/gemini-3.1-flash-lite', 'google/gemini-3.1-flash-lite:batch',
  '~google/gemini-pro-latest', '~google/gemini-flash-latest',
  'google/gemini-3.1-flash-lite-preview', 'google/gemini-3.1-pro-preview-customtools',
  'google/gemini-3.1-pro-preview', 'google/gemini-3.1-pro-preview:batch',
  'google/gemini-3-flash-preview', 'google/gemini-3-flash-preview:batch',
  'google/gemini-3-pro-image-preview', 'google/gemini-2.5-flash-image',
  'google/gemini-2.5-flash-lite', 'google/gemini-2.5-flash-lite:batch',
  'google/gemini-2.5-flash', 'google/gemini-2.5-flash:batch',
  'google/gemini-2.5-pro', 'google/gemini-2.5-pro:batch', 'google/gemini-2.5-pro-preview',
  'google/lyria-3-pro-preview', 'google/lyria-3-clip-preview',
] as const;

/** Product rule at 3184d9212bb2e7c18acd8fa2d981f61d395fc7a3:
 * futureagi/agentcc/services/managed_models_default.py:79-106, especially
 * :90-93 (no write unless declared, fresh AND offered) and :98-103 (one write).
 * All current default-off gateway profiles expect zero. A declared-on profile
 * must also offer credits to the fresh org; one is then expected, not <=1.
 * W00 remains default-OFF; GW-E2E-020 covers the default-on contract. */
export const expectedFirstConfigVersions = (declared: boolean): 0 | 1 => declared ? 1 : 0;

// catalog_admin.go:23-34,75-97: unavailable models deliberately remain visible
// for BYOK, but cannot carry any displayed input/output/cache/billed MTok price.
export const CAT2_PRICE_FIELDS = ['input_per_mtok', 'output_per_mtok', 'cache_read_per_mtok',
  'billed_input_per_mtok', 'billed_output_per_mtok'] as const;
export interface CensusCatalogModel { id: string; state?: string; [field: string]: unknown }
export interface Cat2Observation {
  id: typeof CAT2_IDS[number]; disposition: 'absent' | 'blocked-unpriced';
  state: string | null; prices: Record<typeof CAT2_PRICE_FIELDS[number], unknown>;
}
export interface Cat2CreditsRefusal {
  id: typeof CAT2_IDS[number]; verdict: 'refused'; status: number; code: string;
  upstreamBefore: number; upstreamAfter: number;
}
export function cat2ObservationIsOff(row: Cat2Observation): boolean {
  return !!row && !!row.prices && CAT2_PRICE_FIELDS.every(field => row.prices[field] === null) &&
    ((row.disposition === 'absent' && row.state === null) ||
     (row.disposition === 'blocked-unpriced' && row.state === 'blocked'));
}
// managed_resolution.go:31-48 + managed.go:84 + models/errors.go: explicit
// managed-provider request on an org key without opt-in is 403 permission_denied.
// Authentication errors, unknown-model errors and a served call are NOT proof.
export function cat2CreditsRefused(row: Cat2CreditsRefusal): boolean {
  return !!row && row.verdict === 'refused' && row.status === 403 && row.code === 'permission_denied' &&
    row.upstreamBefore === 0 && row.upstreamAfter === 0;
}
export function observeCat2(models: readonly CensusCatalogModel[], lane: 'api' | 'gateway'): Cat2Observation[] {
  if (!Array.isArray(models) || models.some(m => !m || typeof m.id !== 'string')) {
    throw new Error(`CAT2_INVALID_CATALOG: ${lane}`);
  }
  return CAT2_IDS.map(id => {
    const matches = models.filter(m => m.id === id);
    // Validate EVERY occurrence: duplicate provider rows cannot hide an offer.
    const safe = matches.every(m => m.state === 'blocked' && CAT2_PRICE_FIELDS.every(field => m[field] == null));
    if (!safe) throw new Error(`CAT2_OFFERED: ${lane}: ${id}`);
    return { id, disposition: matches.length ? 'blocked-unpriced' : 'absent', state: matches.length ? 'blocked' : null,
      prices: Object.fromEntries(CAT2_PRICE_FIELDS.map(field => [field, null])) as Cat2Observation['prices'] };
  });
}
