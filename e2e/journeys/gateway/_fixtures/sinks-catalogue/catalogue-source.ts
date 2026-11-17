import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { isDeepStrictEqual } from 'node:util';
import { parseDocument } from 'yaml';

export const SOURCE_REF = 'da4c81cc623f909ce38f53e01b557ee8374d5adb';
const SOURCE_ROOT = new URL('../../../../../', import.meta.url);
// One root digest, not a shipped per-file hash inventory. Framing is
// path UTF8 + NUL + decimal byte length + NUL + bytes, in PATHS order.
// Runner SourceInputs still binds the exact repo/ref; this verifies these reads
// independently without git/subprocess/lazy fetch or trusting a caller's set.
const CLOSURE_SHA256 = 'aa6d86ec8cd040266f9fb9ec610252ad4c344f81b26500e6ccd30b31394b6d95';
const PATHS = ['e2e/stack/gateway.e2e.yaml',
  'agentcc-gateway/internal/catalog/openrouter_snapshot.json',
  'agentcc-gateway/internal/catalog/aliases.json'] as const;
const fail = (reason: string): never => { throw new Error(`CATALOGUE_${reason}`); };
const record = (v: unknown): Record<string, unknown> => {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return fail('SOURCE_SHAPE');
  return v as Record<string, unknown>;
};
function id(v: unknown): string {
  // openrouter_snapshot.json at SOURCE_REF includes 19 IDs with one leading '~'
  // (e.g. ~unbiased/pareto-latest). Keep that prefix and the 255-character bound;
  // this syntax check does not confer managed availability or launch eligibility.
  if (typeof v !== 'string' || v.length > 255 ||
      /^~?[a-zA-Z0-9][a-zA-Z0-9/_.:+-]*$/.exec(v)?.[0] !== v) return fail('SOURCE_ID');
  return v;
}
function ids(v: unknown): string[] {
  if (!Array.isArray(v)) return fail('SOURCE_SHAPE');
  const result = v.map(id);
  if (new Set(result).size !== result.length) return fail('SOURCE_DUPLICATE');
  return result.sort();
}
async function sources() {
  const bodies = await Promise.all(PATHS.map(p => readFile(new URL(p, SOURCE_ROOT))));
  const digest = createHash('sha256');
  bodies.forEach((body, i) => { digest.update(`${PATHS[i]}\0${body.length}\0`); digest.update(body); });
  if (digest.digest('hex') !== CLOSURE_SHA256) return fail('SOURCE_DRIFT');
  const yaml = parseDocument(bodies[0].toString('utf8'), { uniqueKeys: true });
  if (yaml.errors.length) return fail('SOURCE_YAML');
  const config = record(yaml.toJS({ maxAliasCount: 0 }));
  const snapshot = record(JSON.parse(bodies[1].toString('utf8')));
  if (!Array.isArray(snapshot.data)) return fail('SOURCE_SHAPE');
  const models = snapshot.data.map(record);
  ids(models.map(m => m.id));
  return { config, models, aliases: record(record(JSON.parse(bodies[2].toString('utf8'))).aliases) };
}

/** Pinned INVENTORY, not reimplementation of managed billability or proof an
 * offer is enabled. gateway.e2e.yaml supplies explicit BYOK IDs; openrouter.go
 * embeds the snapshot; managed_names.go resolves exact/bare spellings and
 * managed_identity.go consults aliases.json. No API output becomes an oracle.
 * H5/CAT-2 launch/stock opt-in configuration is absent in this source tuple.
 */
export async function readCatalogueSource() {
  const { config, models, aliases } = await sources();
  const providers = record(config.providers);
  const stockByokIds: string[] = [];
  const managedProviders: string[] = [];
  for (const [providerId, raw] of Object.entries(providers)) {
    const provider = record(raw);
    if (provider.managed === true) {
      if (provider.type !== 'openrouter' || provider.auto_discover !== false) return fail('PROFILE_DRIFT');
      managedProviders.push(id(providerId));
    } else stockByokIds.push(...ids(provider.models));
  }
  const managed = record(config.managed);
  if (managed.catalog_refresh !== '0s' || managed.markup_percent !== 5) return fail('PROFILE_DRIFT');
  const bundledModelIds = ids(models.map(m => m.id));
  // Source namespace inventory includes openrouter/free even though the old
  // billability.go special-cases it as explicitly free. This MUST NOT be sold
  // as the 746g exclusion gate or resurrected as a router positive.
  const bundledRouterIds = bundledModelIds.filter(m => m.startsWith('openrouter/'));
  const declaredAliases = Object.entries(aliases).filter(([, target]) => bundledRouterIds.includes(id(target)))
    .map(([alias]) => id(alias));
  const routerSpellings = ids([...bundledRouterIds, ...bundledRouterIds.map(m => m.slice(m.indexOf('/') + 1)), ...declaredAliases]);
  return {
    sourceRef: SOURCE_REF, stockByokIds: ids(stockByokIds), managedProviders: ids(managedProviders),
    bundledModelIds, bundledRouterIds, routerSpellings,
    launch: { state: 'OWED' as const,
      reason: 'H5 retained CAT-2 launch IDs, opt-in/stock profiles and complete 746g policy alias set are not present at the prep tuple.' },
  };
}

// endpoint.go:Prices and openrouter.go:pricingKeys. Notably image means an INPUT
// image, not a generated image; request means a request, not a Lyria generation.
const UNITS: Record<string, string> = {
  prompt: 'USD/input-token', completion: 'USD/output-token', input_cache_read: 'USD/cache-read-token',
  input_cache_write: 'USD/cache-write-token', internal_reasoning: 'USD/reasoning-token',
  request: 'USD/request', image: 'USD/input-image', web_search: 'USD/web-search',
};
function perMillion(value: string): string {
  const [whole, fraction = ''] = value.split('.');
  const numerator = BigInt(whole + fraction) * 1_000_000n;
  const denominator = 10n ** BigInt(fraction.length);
  const remainder = numerator % denominator;
  const tail = remainder.toString().padStart(fraction.length, '0').replace(/0+$/, '');
  return `${numerator / denominator}${tail ? `.${tail}` : ''}`;
}
export async function readPinnedPrice(modelId: string, dimension: string) {
  id(modelId);
  if (!Object.hasOwn(UNITS, dimension)) return fail('PRICE_UNIT_OWED');
  const { models } = await sources();
  const matches = models.filter(m => m.id === modelId);
  if (matches.length !== 1) return fail('PRICE_ID');
  const pricing = record(matches[0].pricing);
  if (Object.values(pricing).some(v => typeof v === 'string' && v.startsWith('-'))) return fail('DYNAMIC_PRICE');
  if (Object.hasOwn(pricing, 'overrides')) return fail('TIERED_PRICE_OWED');
  const value = pricing[dimension];
  // The checked snapshot quotes decimals as strings; never convert through a
  // JS float, fill absent dimensions with zero or infer a per-generation unit.
  if (typeof value !== 'string' || value.length > 40 || !/^(0|[1-9]\d*)(?:\.\d{1,18})?$/.test(value)) return fail('PRICE_MISSING');
  return { sourceRef: SOURCE_REF, modelId, dimension, unit: UNITS[dimension], decimalUsd: value,
    perMillionUsd: UNITS[dimension].endsWith('-token') ? perMillion(value) : null };
}
export function assertIdentitySet(actual: readonly string[], expected: readonly string[]): void {
  if (!expected.length || new Set(expected).size !== expected.length) return fail('EXPECTATION_INVALID');
  if (!isDeepStrictEqual(actual.map(id).sort(), expected.map(id).sort())) return fail('EXACT_IDS');
}
