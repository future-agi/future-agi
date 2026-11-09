import type { ApiClient } from '../../../lib/api-client';

// Source pins at 7543e2d995fe58b620745aa81ce1a76c8855ef0e:
// frontend/src/utils/axios.js:endpoints.gateway.managedModels{,Catalog};
// futureagi/agentcc/urls.py:AgentccManagedModelsViewSet registration;
// futureagi/agentcc/views/managed_models.py:list/catalog/disabled_catalog;
// futureagi/agentcc/serializers/managed_models.py:*ResponseSerializer.
// The catalog endpoint forwards server/catalog_admin.go:handleCatalog unchanged
// inside { status, result }. It has no pagination. This is NOT /v1/models and
// its full listing includes blocked models; listing is not a managed offer.
export const MANAGED_STATUS_PATH = '/agentcc/managed-models/';
export const MANAGED_CATALOGUE_PATH = '/agentcc/managed-models/catalog/';

type Reader = Pick<ApiClient, 'get' | 'baseURL'>;
type ObjectValue = Record<string, unknown>;

function object(value: unknown): ObjectValue {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('J31 catalog read: expected an object');
  }
  return value as ObjectValue;
}
function text(value: unknown): string {
  if (typeof value !== 'string' || !value.length) throw new Error('J31 catalog read: missing identity');
  return value;
}
function flag(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('J31 catalog read: expected a boolean');
  return value;
}
function nullableNumber(value: unknown): number | null {
  if (value === null) return null;
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error('J31 catalog read: missing or invalid numeric field');
  }
  return value;
}
function optionalStrings(row: ObjectValue, field: string): string[] | null {
  if (!Object.hasOwn(row, field)) return null; // omitted, not inferred empty
  if (!Array.isArray(row[field])) throw new Error('J31 catalog read: invalid modality list');
  return row[field].map(text);
}
function optionalFlag(row: ObjectValue, field: string): boolean | null {
  return Object.hasOwn(row, field) ? flag(row[field]) : null;
}
function local(reader: Reader): void {
  const url = new URL(reader.baseURL);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) {
    throw new Error('J31 readers require the accepted loopback application API');
  }
}
function result(envelope: unknown): ObjectValue {
  const body = object(envelope);
  if (body.status !== true) throw new Error('J31 application read did not succeed');
  return object(body.result);
}

export interface ManagedStatus {
  available: boolean; enabled: boolean; balance_usd: number | null; markup_percent: number | null;
}

export async function readManagedStatus(api: Reader): Promise<ManagedStatus> {
  local(api);
  const value = result(await api.get<unknown>(MANAGED_STATUS_PATH));
  return { available: flag(value.available), enabled: flag(value.enabled),
    balance_usd: nullableNumber(value.balance_usd), markup_percent: nullableNumber(value.markup_percent) };
}

export interface CatalogueModel {
  id: string; provider: string; vendor: string;
  state: string | null; input_modalities: string[] | null; output_modalities: string[] | null;
  dynamic_price: boolean | null; unpriced: boolean | null; stale_price: boolean | null;
}
export type CatalogueState =
  { available: false; markup_percent: null; snapshot_id: null; models: [] } |
  { available: true; markup_percent: number; snapshot_id: string; models: CatalogueModel[] };

// Field provenance beyond the Django serializer: internal/server/catalog_admin.go
// catalogFreshness/catalogModelView; internal/catalog/catalog.go:Model. Deliberately
// omit descriptions, provider source URLs and all JSON floating-point prices.
// Do NOT derive the independent A08 price oracle from this response or UI text.
export async function readCatalogue(api: Reader): Promise<CatalogueState> {
  local(api);
  const value = result(await api.get<unknown>(MANAGED_CATALOGUE_PATH));
  if (!Array.isArray(value.models) || !Array.isArray(value.providers)) {
    throw new Error('J31 catalog read: full models/providers arrays required');
  }
  if (value.available === false) {
    if (value.markup_percent !== null || value.models.length || value.providers.length) {
      throw new Error('J31 catalog read: malformed disabled catalog');
    }
    return { available: false, markup_percent: null, snapshot_id: null, models: [] };
  }
  // handleCatalog has NO available field; only disabled_catalog supplies false.
  // Never turn a contradictory/novel envelope into an enabled catalog silently.
  if (Object.hasOwn(value, 'available')) throw new Error('J31 catalog read: unpinned available field');
  const markup = nullableNumber(value.markup_percent);
  if (markup === null) throw new Error('J31 catalog read: offered catalog has no markup');
  const models = value.models.map(raw => {
    const row = object(raw);
    if (typeof row.vendor !== 'string') throw new Error('J31 catalog read: missing vendor');
    return { id: text(row.id), provider: text(row.provider), vendor: row.vendor,
      state: Object.hasOwn(row, 'state') ? text(row.state) : null,
      input_modalities: optionalStrings(row, 'input_modalities'),
      output_modalities: optionalStrings(row, 'output_modalities'),
      dynamic_price: optionalFlag(row, 'dynamic_price'), unpriced: optionalFlag(row, 'unpriced'),
      stale_price: optionalFlag(row, 'stale_price') };
  });
  // Keep multiplicity: duplicated model/provider identities must reach exact-set
// assertions, never be silently deduplicated here.
  return { available: true, markup_percent: markup, snapshot_id: text(value.snapshot_id), models };
}

// These readers supply only portions of A04/A05/A06/A12. H5 exact launch and
// router-alias sets, actual credits-on refusals, Redis, ledger, independent
// price snapshots and accepted Stage A identity bindings remain OWED in AUTHOR.json.
// No HTTP mutation, provisioning, provider call, runner or activation is added.
