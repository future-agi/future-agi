import { request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { GatewayGridReader, GRID_VIEWPORT } from '../../lib/gateway-grid';
import { MANAGED_SETTINGS } from '../../lib/managed-mock';

// Pinned off the running system: the Models page (frontend/src/sections/
// gateway/models/ModelsSection.jsx, ManagedModelsCard.jsx,
// catalog/ModelCatalogPage.jsx: the managed models strip, a search box
// "Search models", the "<n> models" / "<shown> of <n> models" count, the
// price-list note, the Filters facets of catalog/CatalogFacets.jsx and one
// gateway grid of every catalog model, unpaged, columns by
// catalog/columns.js colId "name" (a link to the model's page, the id
// beneath), "input" and "output"), the model page a row opens
// (models/modelLinks.js modelPath: /models/<provider>/<model>, the id split
// at its first "/"; detail/ModelOverview.jsx, its "Pricing" section a
// gateway grid of detail/pricingColumns.jsx label and one price: what a request
// on Future AGI credits is charged), the two endpoints it reads
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
// ManagedModelsCard.jsx current/legacy names, not CatalogFacets.jsx's Access
// checkbox "Future AGI credits (<count>)", which remains available while OFF.
const CREDITS_CONTROL_NAME = /^Future AGI credits(?:: one key for every model)?$/;
const ADMIN_TOKEN =
  process.env.AGENTCC_ADMIN_TOKEN || 'local-dev-only-admin-token-replace-me';
const MARKUP = MANAGED_SETTINGS.markup_percent;
// The gateway rounds billed prices to the micro-dollar per 1M tokens.
const PRICE_TOLERANCE = 1e-6;
// models/catalog.js catalogFreshness, for a catalog whose every provider is bundled.
const BUNDLED_NOTE = 'Bundled price list';
// CatalogFacets.jsx: the Provider group lists its busiest PROVIDERS_SHOWN vendors
// until "Show all <n>"; each option is a checkbox named "<label> (<count>)".
const PROVIDERS_SHOWN = 8;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors provisioning 120 + three UI steps of STEP_BUDGET 75 (225) +
// API reads 15 + headroom 30.
const FLOW_TIMEOUT = 390_000;

interface CatalogModel {
  id: string;
  name?: string;
  vendor?: string;
  endpoint_type?: string;
  output_modalities?: string[];
  managed_allowed?: boolean;
  state?: string;
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
// vendorLabel's fallback, and catalogRows.js filterRows' search haystack: the
// expected side, computed from the API's numbers.
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
// catalogRows.js filterRows matches every search word against the row's id, display
// name, vendor slug, vendor label and type label (Chat, Image, ...; none of those holds
// a "/", so a "vendor/model" id never matches one).
const haystack = (m: CatalogModel) =>
  [m.id, displayName(m), vendorSlug(m.vendor), vendorLabelOf(m.vendor)].join(' ').toLowerCase();
// catalogRows.js managedVerdict: a model whose managed price the catalog shows (a chat
// model that outputs text only, with a fixed list price, not refused or blocked).
const soldManaged = (m: CatalogModel) =>
  hasListPrice(m) && [undefined, 'chat'].includes(m.endpoint_type) &&
  (m.output_modalities ?? []).every((o) => o === 'text') && m.managed_allowed !== false && m.state !== 'blocked';
// models/modelLinks.js modelPath.
const modelPagePath = (id: string) => {
  const at = id.indexOf('/');
  return `${MODELS_PATH}/${encodeURIComponent(id.slice(0, at))}/${encodeURIComponent(id.slice(at + 1))}`;
};

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
        'read the neutral managed-OFF notice, absence of enable controls and the catalog\'s model count',
        'search the catalog for one model id',
        'open that model\'s page from its row and read its Pricing',
        'go back, clear the search and tick that model\'s provider in the Provider filter',
      ],
      backendChecks: [
        'the managed-models status API reports managed models available and off for this org, at the gateway\'s 5% markup',
        'the loaded Models page shows the neutral organization-OFF notice with no credits switch, enable dialog or balance statistic',
        'the console\'s catalog API serves the gateway\'s own catalog: the same model ids as the gateway admin catalog, each list price billed at provider price plus 5%',
        'the page counts exactly the catalog API\'s models, lists every one as a grid row, and says the prices come from the bundled list',
        'a search for one model id leaves exactly that model\'s row, linked to its page, with its billed input and output prices',
        'the model page\'s Pricing table shows one price per row, that model\'s billed price on Future AGI credits, never the provider price or a markup beside it',
        'the Provider filter leaves exactly the API\'s model count for that vendor',
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

    // The searched model: the first plain "vendor/model" id (code-point order;
    // OpenRouter's "~vendor/..-latest" aliases left out) that the catalog sells
    // managed at a list price and that no other model's searchable text contains, so
    // the search must narrow the grid to exactly one row (catalogRows.js filterRows).
    const chosen = [...catalog.models]
      .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
      .find((m) => /^[a-z0-9][^/]*\/./.test(m.id) && soldManaged(m) &&
        catalog.models.filter((o) => haystack(o).includes(m.id.toLowerCase())).length === 1)!;
    const vendorOf = (m: CatalogModel) => vendorSlug(m.vendor);
    const vendorCount = catalog.models.filter((m) => vendorOf(m) === vendorOf(chosen)).length;
    // CatalogFacets.jsx / catalogRows.js catalogFacets: vendors busiest first, then by label.
    const vendorTotals = new Map<string, number>();
    for (const m of catalog.models) vendorTotals.set(vendorOf(m), (vendorTotals.get(vendorOf(m)) ?? 0) + 1);
    const vendorRank = [...vendorTotals.entries()]
      .sort(([va, a], [vb, b]) => b - a || vendorLabelOf(va).localeCompare(vendorLabelOf(vb)))
      .findIndex(([v]) => v === vendorOf(chosen));
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ organizationId: owner.organizationId, models: catalog.models.length,
        providers: catalog.providers, chosen, vendorCount, vendorRank, vendors: vendorTotals.size }, null, 2),
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
    await page.setViewportSize(GRID_VIEWPORT);
    const grid = GatewayGridReader.in(page);
    const total = catalog.models.length;

    try {
      await test.step('UI: the neutral OFF notice and the catalog count match the API', async () => {
        await page.goto(MODELS_PATH, { waitUntil: 'domcontentloaded' });
        await expect(page.getByText(`${total} models`, { exact: true })).toBeVisible({ timeout: UI_READY });
        await expect(page.getByText(BUNDLED_NOTE, { exact: true })).toBeVisible({ timeout: UI_READY });
        // ManagedModelsCard.jsx at 3c20e189ea: catalog stays browsable while
        // organization credits are OFF. Match old AND new switch names on absence.
        await expect(page.getByRole('alert').filter({ hasText: /^Future AGI credits are off for this organization\.$/ }))
          .toBeVisible({ timeout: UI_READY });
        await expect(page.getByRole('checkbox', { name: CREDITS_CONTROL_NAME, includeHidden: true })).toHaveCount(0, { timeout: UI_READY });
        await expect(page.getByRole('switch', { name: CREDITS_CONTROL_NAME, includeHidden: true })).toHaveCount(0, { timeout: UI_READY });
        await expect(page.getByRole('dialog', { name: 'Turn on Future AGI credits?' })).toHaveCount(0, { timeout: UI_READY });
        await expect(page.getByText('Credit balance', { exact: true })).toHaveCount(0, { timeout: UI_READY });
        // One grid, unpaged: every catalog model is a displayed row (rendered or not).
        await expect.poll(() => grid.displayedRowCount(), { timeout: UI_READY }).toBe(total);
      }, { timeout: STEP_BUDGET });

      await test.step('UI: a search for one id leaves exactly that model, then its page', async () => {
        await page.getByRole('textbox', { name: 'Search models' }).fill(chosen.id);
        await expect(page.getByText(`1 of ${total} models`, { exact: true })).toBeVisible({ timeout: UI_READY });
        await expect(grid.rows).toHaveCount(1, { timeout: UI_READY });
        expect(await grid.displayedRowCount()).toBe(1);
        const nameCell = grid.cell(0, 'name');
        await expect(nameCell.getByRole('link', { name: displayName(chosen), exact: true }))
          .toHaveAttribute('href', modelPagePath(chosen.id), { timeout: UI_READY });
        await expect(nameCell).toContainText(chosen.id);
        await expect(grid.cell(0, 'input')).toHaveText(formatPerMillion(chosen.billed_input_per_mtok!));
        await expect(grid.cell(0, 'output')).toHaveText(formatPerMillion(chosen.billed_output_per_mtok!));

        // A click on the row, off the name link, opens the model's page.
        await grid.cell(0, 'context').click();
        await expect(page).toHaveURL((url) => url.pathname === modelPagePath(chosen.id), { timeout: UI_READY });
        const pricing = GatewayGridReader.in(page.getByRole('region', { name: 'Pricing', exact: true }));
        await expect.poll(() => pricing.rowTexts(0), { timeout: UI_READY }).toEqual([
          'Input', formatPerMillion(chosen.billed_input_per_mtok!),
        ]);
        await expect.poll(() => pricing.rowTexts(1), { timeout: UI_READY }).toEqual([
          'Output', formatPerMillion(chosen.billed_output_per_mtok!),
        ]);
        await expect(page.getByRole('region', { name: 'Pricing', exact: true }))
          .not.toContainText(/markup|provider price|future agi price|%/i);
        await page.goBack({ waitUntil: 'domcontentloaded' });
        await expect(page).toHaveURL((url) => url.pathname === MODELS_PATH, { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('UI: the Provider filter keeps exactly that vendor\'s models', async () => {
        await page.getByRole('textbox', { name: 'Search models' }).fill('');
        await expect(page.getByText(`${total} models`, { exact: true })).toBeVisible({ timeout: UI_READY });
        const facets = page.getByRole('region', { name: 'Filters', exact: true });
        if (vendorRank >= PROVIDERS_SHOWN) {
          await facets.getByRole('button', { name: `Show all ${vendorTotals.size}`, exact: true }).click();
        }
        await facets.getByRole('checkbox', { name: `${vendorLabelOf(chosen.vendor)} (${vendorCount})`, exact: true })
          .check({ timeout: UI_READY });
        await expect(page.getByText(`${vendorCount} of ${total} models`, { exact: true }))
          .toBeVisible({ timeout: UI_READY });
        await expect.poll(() => grid.displayedRowCount(), { timeout: UI_READY }).toBe(vendorCount);
      }, { timeout: STEP_BUDGET });
    } finally {
      await context.close();
    }
  },
);

// models/catalog.js vendorLabel: known slugs have fixed labels; the rest are
// title-cased on '-' and '_', acronyms upper-cased. Only the fallback is needed for runtime choices
// outside that table, so known slugs are listed for the ones a snapshot may hold.
const VENDOR_LABELS: Record<string, string> = {
  ai21: 'AI21', amazon: 'Amazon', anthropic: 'Anthropic', cohere: 'Cohere', deepseek: 'DeepSeek',
  google: 'Google', 'meta-llama': 'Meta', microsoft: 'Microsoft', minimax: 'MiniMax', mistralai: 'Mistral',
  moonshotai: 'Moonshot AI', nousresearch: 'Nous Research', nvidia: 'NVIDIA', openai: 'OpenAI',
  perplexity: 'Perplexity', qwen: 'Qwen', 'x-ai': 'xAI', 'z-ai': 'Z.ai',
};
// models/catalog.js vendorSlug: an alias entry's "~" does not name another vendor.
function vendorSlug(slug?: string): string {
  return String(slug ?? '').replace(/^~+/, '');
}
// models/catalog.js ACRONYMS: words a slug spells in lower case that read upper case.
const ACRONYMS = new Set(['ai', 'agi', 'llm', 'ml', 'nlp', 'api']);
function vendorLabelOf(slug?: string): string {
  const key = vendorSlug(slug);
  if (!key) return 'Other';
  return VENDOR_LABELS[key] ?? key.split(/[-_]/).filter(Boolean)
    .map((part) => (ACRONYMS.has(part.toLowerCase()) ? part.toUpperCase()
      : part.charAt(0).toUpperCase() + part.slice(1))).join(' ');
}
