import { expect } from '@playwright/test';
import type { GatewayAdmin } from '../../../lib/gateway-admin';

// Pinned at 7543e2d995fe58b620745aa81ce1a76c8855ef0e:
// agentcc-gateway/internal/server/routes_models.go:registerModelsRoutes and
// model_prices.go:modelPricesHandler (disabled => 404, not 401/empty 200).
export const PRICE_FEED_PATH = '/public/model-prices/v1.json';

export interface PublicationState {
  enabled: boolean;
  sources: { provider_id: string; catalog_source: string }[];
}

/** GET /-/config public_price_feed, model_prices.go:pricePublicationReport.
 * config.go:PublicPriceFeedConfig is a startup snapshot; reload is not restart.
 * Do not export the rest of the admin config or print malformed response values.
 * This is NOT a price-feed body/schema validator or proof of allowed catalogue
 * contents. The schema/check.py invocation and enabled profile remain owed.
 */
export async function readPublicationState(admin: Pick<GatewayAdmin, 'config'>): Promise<PublicationState> {
  const config = await admin.config();
  const value: unknown = config.public_price_feed;
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Missing public-price publication state');
  }
  const data = value as Record<string, unknown>;
  if (Object.keys(data).sort().join(',') !== 'enabled,sources' ||
      typeof data.enabled !== 'boolean' || !Array.isArray(data.sources)) {
    throw new Error('Unexpected public-price publication schema');
  }
  const sources = data.sources.map((source: unknown) => {
    if (!source || typeof source !== 'object' || Array.isArray(source)) {
      throw new Error('Unexpected publication source schema');
    }
    const entry = source as Record<string, unknown>;
    if (Object.keys(entry).sort().join(',') !== 'catalog_source,provider_id' ||
        typeof entry.provider_id !== 'string' || entry.provider_id.length === 0 ||
        typeof entry.catalog_source !== 'string' || entry.catalog_source.length === 0) {
      throw new Error('Unexpected publication source fields');
    }
    return { provider_id: entry.provider_id, catalog_source: entry.catalog_source };
  });
  return { enabled: data.enabled, sources };
}

export function assertStockPublication(state: PublicationState, anonymousStatus: number): void {
  // config/config.go: zero-value operator publication = disabled, no sources.
  // Booleans/counts/status only in failures, never server-provided strings.
  expect(state.enabled, 'W05.A01 stock publication disabled').toBe(false);
  expect(state.sources.length, 'W05.A01 stock allowlist empty').toBe(0);
  expect(anonymousStatus, 'W05.A01 stock anonymous route absent').toBe(404);
}
