import { request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { MANAGED_SETTINGS } from '../../lib/managed-mock';

// Pinned off the running system: the Models page (frontend/src/sections/
// gateway/models/ModelsSection.jsx, ManagedModelsCard.jsx, ModelCatalog.jsx,
// ModelDetailDrawer.jsx), the two endpoints it reads
// (agentcc/views/managed_models.py: list -> managed-models status, catalog ->
// the gateway's catalog, cached 300 s) and the gateway's own admin catalog
// (agentcc-gateway/internal/server: GET /-/catalog) with the admin token
// docker-compose.yml gives the gateway. The catalog is the OpenRouter snapshot
// bundled with the gateway, served by e2e/stack/gateway.e2e.yaml's
// fagi-managed provider on the deterministic mock (MANAGED_SETTINGS:
// catalog_refresh 0s, so no live fetch).
const MODELS_PATH = '/dashboard/gateway/models';
const STATUS_PATH = '/agentcc/managed-models/';
const CATALOG_PATH = '/agentcc/managed-models/catalog/';
const ADMIN_TOKEN =
  process.env.AGENTCC_ADMIN_TOKEN || 'local-dev-only-admin-token-replace-me';
const MARKUP = MANAGED_SETTINGS.markup_percent;
// The gateway rounds billed prices to the micro-dollar per 1M tokens.
const PRICE_TOLERANCE = 1e-6;
// ModelCatalog.jsx CatalogFreshness, for a catalog with no live provider read.
const BUNDLED_NOTE = 'Using the bundled price list; live prices are unavailable';
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors provisioning 120 + three UI steps of STEP_BUDGET 75 (225) +
// API reads 15 + headroom 30.
const FLOW_TIMEOUT = 390_000;

interface CatalogModel {
  id: string;
  name?: string;
  vendor?: string;
  dynamic_price?: boolean;
  input_per_mtok?: number;
  output_per_mtok?: number;
  billed_input_per_mtok?: number;
  billed_output_per_mtok?: number;
}
interface Catalog {
  markup_percent: number;
  providers: { provider: string; source: string; models: number }[];
  models: CatalogModel[];
}

// models/catalog.js formatPerMillion, hasListPrice, modelDisplayName and
// vendorLabel's fallback: the expected side, computed from the API's numbers.
function formatPerMillion(value: number): string {
  if (value === 0) return 'Free';
  if (value < 0.0001) return '<$0.0001';
  if (value >= 1) return `$${value.toFixed(2)}`;
  const [whole, decimals = ''] = value.toFixed(4).replace(/0+$/, '').split('.');
  return `$${whole}.${decimals.padEnd(2, '0')}`;
}
const hasListPrice = (m: CatalogModel) =>
  !m.dynamic_price && Number.isFinite(m.billed_input_per_mtok) && Number.isFinite(m.billed_output_per_mtok);
const displayName = (m: CatalogModel) => {
  const name = String(m.name || m.id);
  const idx = name.indexOf(': ');
  return idx > 0 ? name.slice(idx + 2) : name;
};
const haystack = (m: CatalogModel) => [m.id, m.name ?? '', m.vendor ?? ''].join(' ').toLowerCase();

// scopeActors signs its users up before the body runs, so its setup falls
// under the file's timeout, not the body's test.setTimeout: without this
// line it gets the 120 s project default.
test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-006: the Models page lists the gateway managed catalog at provider price plus the managed markup',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-006',
      area: 'gateway',
      userGoal:
        'A developer browses the managed model catalog and sees what each model will be billed before using it',
      steps: [
        'open Models',
        'read the managed models card and the catalog\'s model count',
        'search the catalog for one model id',
        'open that model\'s details',
        'clear the search and filter the catalog by that model\'s vendor',
      ],
      backendChecks: [
        'the managed-models status API reports managed models available and off for this org, at the gateway\'s 5% markup',
        'the console\'s catalog API serves the gateway\'s own catalog: the same model ids as the gateway admin catalog, each list price billed at provider price plus 5%',
        'the page counts exactly the catalog API\'s models and says the prices come from the bundled list',
        'a search for one model id leaves exactly that model\'s row, with its billed input and output prices',
        'the details drawer shows that model\'s provider and managed prices',
        'the vendor filter leaves exactly the API\'s model count for that vendor',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;

    const status = (await owner.api.get<{ result: Record<string, unknown> }>(STATUS_PATH)).result;
    const catalog = (await owner.api.get<{ result: Catalog }>(CATALOG_PATH)).result;
    const admin = await request.newContext();
    let gatewayCatalog: Catalog;
    try {
      const res = await admin.get(`${E2E.gatewayUrl}/-/catalog`, {
        headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
      });
      expect(res.status(), await res.text()).toBe(200);
      gatewayCatalog = (await res.json()) as Catalog;
    } finally {
      await admin.dispose();
    }

    // The searched model: the first plain id (code-point order; OpenRouter's
    // "~vendor/..-latest" aliases left out) with a list price that no other
    // model's id, name or vendor contains, so the search must narrow the table
    // to exactly one row (models/catalog.js filterModels).
    const chosen = [...catalog.models]
      .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
      .find((m) => /^[a-z0-9]/.test(m.id) && hasListPrice(m) &&
        catalog.models.filter((o) => haystack(o).includes(m.id.toLowerCase())).length === 1)!;
    const vendorCount = catalog.models.filter((m) => m.vendor === chosen.vendor).length;
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ organizationId: owner.organizationId, models: catalog.models.length,
        providers: catalog.providers, chosen, vendorCount }, null, 2),
    });

    await test.step('API: managed models are offered, off for this org, at the gateway markup', async () => {
      expect(status).toEqual({ available: true, enabled: false, balance_usd: 0, markup_percent: MARKUP });
    });

    await test.step('API: the console catalog is the gateway catalog, billed at provider price plus the markup', async () => {
      expect(catalog.markup_percent).toBe(MARKUP);
      expect(catalog.providers.map(({ provider, source }) => ({ provider, source })))
        .toEqual([{ provider: 'fagi-managed', source: 'bundled' }]);
      expect(catalog.models.map((m) => m.id).sort()).toEqual(gatewayCatalog.models.map((m) => m.id).sort());
      expect(catalog.models.length).toBeGreaterThan(0);
      const mispriced = catalog.models.filter(hasListPrice).flatMap((m) =>
        ([['input_per_mtok', 'billed_input_per_mtok'], ['output_per_mtok', 'billed_output_per_mtok']] as const)
          .filter(([up, billed]) =>
            Math.abs((m[billed] as number) - (m[up] as number) * (1 + MARKUP / 100)) > PRICE_TOLERANCE)
          .map(([up, billed]) => `${m.id}: ${m[up]} -> ${m[billed]}`));
      expect(mispriced).toEqual([]);
    });

    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    const table = page.getByRole('table', { name: 'Model catalog' });
    const rows = table.locator('tbody tr');
    const total = catalog.models.length;

    try {
      await test.step('UI: the card and the catalog count what the API serves', async () => {
        await page.goto(MODELS_PATH, { waitUntil: 'domcontentloaded' });
        await expect(page.getByText(`${total} models`, { exact: true })).toBeVisible({ timeout: UI_READY });
        await expect(page.getByText(BUNDLED_NOTE, { exact: true })).toBeVisible({ timeout: UI_READY });
        await expect(page.getByLabel('Use managed models')).not.toBeChecked({ timeout: UI_READY });
        await expect(page.getByText('Off', { exact: true })).toBeVisible({ timeout: UI_READY });
        await expect(page.getByText(`${MARKUP}%`, { exact: true })).toBeVisible({ timeout: UI_READY });
        // The first page shows 50 rows (ModelCatalog.jsx pageSize default).
        await expect(rows).toHaveCount(Math.min(50, total), { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('UI: a search for one id leaves exactly that model, then its details', async () => {
        await page.getByRole('textbox', { name: 'Search models' }).fill(chosen.id);
        await expect(rows).toHaveCount(1, { timeout: UI_READY });
        await expect(page.getByText(`1 of ${total} models`, { exact: true })).toBeVisible({ timeout: UI_READY });
        const row = rows.first();
        await expect(row).toHaveAttribute('aria-label', `${displayName(chosen)} details`, { timeout: UI_READY });
        await expect(row.locator('td').nth(0)).toContainText(chosen.id);
        // Columns: Model, Vendor, Context, Input / 1M, Output / 1M, Capabilities.
        await expect(row.locator('td').nth(3)).toHaveText(formatPerMillion(chosen.billed_input_per_mtok!));
        await expect(row.locator('td').nth(4)).toHaveText(formatPerMillion(chosen.billed_output_per_mtok!));

        await row.click();
        const pricing = page.getByRole('table', { name: 'Pricing per 1M tokens' });
        await expect(pricing.locator('tbody tr').nth(0).locator('td')).toHaveText([
          'Input', formatPerMillion(chosen.input_per_mtok!), formatPerMillion(chosen.billed_input_per_mtok!),
        ], { timeout: UI_READY });
        await expect(pricing.locator('tbody tr').nth(1).locator('td')).toHaveText([
          'Output', formatPerMillion(chosen.output_per_mtok!), formatPerMillion(chosen.billed_output_per_mtok!),
        ], { timeout: UI_READY });
        await page.getByRole('button', { name: 'Close' }).click();
        await expect(pricing).toBeHidden({ timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('UI: the vendor filter keeps exactly that vendor\'s models', async () => {
        await page.getByRole('textbox', { name: 'Search models' }).fill('');
        await expect(page.getByText(`${total} models`, { exact: true })).toBeVisible({ timeout: UI_READY });
        await page.getByRole('combobox', { name: /^Vendor/ }).click();
        // Each option reads "<label><count>" (ModelCatalog.jsx vendor menu).
        await page.getByRole('option', { name: new RegExp(`${vendorCount}$`) })
          .filter({ hasText: new RegExp(`^${vendorLabelOf(chosen.vendor)}`) }).click();
        await expect(page.getByText(`${vendorCount} of ${total} models`, { exact: true }))
          .toBeVisible({ timeout: UI_READY });
        await expect(rows).toHaveCount(Math.min(50, vendorCount), { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });
    } finally {
      await context.close();
    }
  },
);

// models/catalog.js vendorLabel: known slugs have fixed labels; the rest are
// title-cased on '-' and '_'. Only the fallback is needed for runtime choices
// outside that table, so known slugs are listed for the ones a snapshot may hold.
const VENDOR_LABELS: Record<string, string> = {
  ai21: 'AI21', amazon: 'Amazon', anthropic: 'Anthropic', cohere: 'Cohere', deepseek: 'DeepSeek',
  google: 'Google', 'meta-llama': 'Meta', microsoft: 'Microsoft', minimax: 'MiniMax', mistralai: 'Mistral',
  moonshotai: 'Moonshot AI', nousresearch: 'Nous Research', nvidia: 'NVIDIA', openai: 'OpenAI',
  perplexity: 'Perplexity', qwen: 'Qwen', 'x-ai': 'xAI', 'z-ai': 'Z.ai',
};
function vendorLabelOf(slug?: string): string {
  if (!slug) return 'Other';
  return VENDOR_LABELS[slug] ?? slug.split(/[-_]/).filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}
