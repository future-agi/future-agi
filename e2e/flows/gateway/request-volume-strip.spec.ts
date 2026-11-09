import { request } from '@playwright/test';
import type { Page, Response } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { GatewayGridReader, GRID_VIEWPORT } from '../../lib/gateway-grid';
import { PROFILE_FINGERPRINTS, walkGatewayProfile } from '../../lib/profile';

// Pinned off the source:
// - the Logs page's volume strip (frontend/src/sections/gateway/logs/RequestVolumeStrip.jsx,
//   data-testid "request-volume"): a chart whose accessible name is
//   "<title>: <n> requests, <e> errors." (role img), from
//   GET /agentcc/request-logs/volume/ (hooks/useRequestLogs.js useRequestVolume:
//   the table's own filters plus start, end, granularity), answered
//   { result: { granularity, time_zone, series: [{ bucket, request_count, error_count }] } }
//   (agentcc/serializers/request_log.py AgentccRequestVolumeSerializer); with no
//   start time the window runs from the oldest matching request to now
//   (logs/requestVolume.js volumeWindow).
// - the Filters panel (logs/FilterPanel.jsx): the Application picker (placeholder
//   "Select applications...") and the Provider picker (placeholder "e.g., openai,
//   anthropic"), "Apply" and "Close filters"; the URL keeps the filters
//   (application=, provider=), and "Clear all" drops them. The Provider picker's
//   one listed option is "Future AGI" (utils/providerLabel.js
//   MANAGED_PROVIDER_LABEL), offered where the deployment offers managed models or
//   a row the table lists is a managed call (FilterPanel managedLogged, from
//   RequestExplorerSection's rows by billing_mode); otherwise it lists none.
// - whether the deployment offers managed models: GET /agentcc/managed-models/
//   `available` (agentcc/views/managed_models.py list), as in try-it-stream.spec.ts
//   and hidden-pages.spec.ts.
// - the table: the gateway grid of logs/RequestTable.jsx, read through
//   lib/gateway-grid.ts; its Provider cell names a managed call "Future AGI"
//   (utils/providerLabel.js providerLabel), whatever upstream served it.
// - the API filter that matches it: provider=Future AGI selects the
//   managed rows (agentcc/services/provider_label.py provider_filter).
// - the logs webhook the gateway flushes to and its secret (agentcc/views/webhook.py
//   GatewayWebhookView, e2e/stack/*.env AGENTCC_WEBHOOK_SECRET), as in
//   request-log-columns.spec.ts; billing_mode and credential_type are log entry v2
//   fields (api_contracts/gateway/examples/log-entry-v2.json).
const LOGS_WEBHOOK_PATH = '/agentcc/webhook/logs/';
const WEBHOOK_SECRET = process.env.AGENTCC_WEBHOOK_SECRET || 'e2e-agentcc-webhook-secret';
const REQUEST_LOGS_PATH = '/agentcc/request-logs/';
const MANAGED_STATUS_PATH = '/agentcc/managed-models/';
const VOLUME_PATH = /\/agentcc\/request-logs\/volume\/$/;
const MANAGED_LABEL = 'Future AGI';
const APPLICATION_PLACEHOLDER = 'Select applications...';
const PROVIDER_PLACEHOLDER = 'e.g., openai, anthropic';
const LOG_VISIBLE = POLL.ASYNC_JOB;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors provisioning 120 + seeding 15 + LOG_VISIBLE 60 + four UI steps of
// STEP_BUDGET 75 (300) + headroom 30 = 525 s.
// One additional Models UI/API step (75s) keeps the original log steps intact.
const FLOW_TIMEOUT = 600_000;
// 824c4ea31a: frontend/src/sections/gateway/analytics/AnalyticsDashboardSection.jsx:25-43;
// utils/axios.js:2538 and analytics/hooks/useAnalyticsModels.js:4-18.
const MODELS_PATH = '/dashboard/gateway/analytics/models';
const MODELS_API = '/agentcc/analytics/model-comparison/';
// analytics/ModelComparison.jsx:165-175: present regardless of node cache state.
const CACHE_HITS_COLUMN = 'cache_hit_rate';
const CACHE_HITS_NOTE = 'Response caching is off on this gateway. Cache hits reflect the selected period.';
// AnalyticsDashboardSection.jsx:63-67 obtains current enforcement from this read.
const CONFIG_PATH = '/agentcc/gateways/default/config/';

interface VolumePoint { bucket: string; request_count: number; error_count: number }
interface Paginated<T> { count: number; results: T[] }

const totals = (series: VolumePoint[]) => ({
  requests: series.reduce((n, p) => n + p.request_count, 0),
  errors: series.reduce((n, p) => n + p.error_count, 0),
});

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-020: an engineer reconciles filtered Logs counts and Models Cache hits for the same requests',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-020',
      area: 'gateway',
      userGoal:
        'A platform engineer reconciles the same requests across filtered Logs and Models: volume counts follow every table filter including Future AGI, and Models retains selected-period Cache hits even when node response caching is off',
      steps: [
        'read whether the deployment offers managed models',
        'deliver six gateway requests on the logs webhook in an organization of its own: four from one application (one of them a 500), two from another (one of them managed)',
        'open Request Logs and read the volume strip',
        'pick the first application in the Filters panel and apply it',
        'open the Filters panel again and open the Provider picker, then close the panel',
        'clear the filters, pick Future AGI as the provider and apply it',
        'open Analytics Models for the same six requests and read Cache hits, including on a cache-off node',
      ],
      backendChecks: [
        'the six requests are stored under the organization, the managed one with billing mode managed',
        'the strip asks the volume API with the table\'s own filters each time, and its answer sums to 6 requests and 1 error, then 4 and 1, then 1 and 0',
        'the list API with each filter returns as many rows as the volume API counts for it',
        'the Models API counts the same six requests; Cache hits cells match its percentages, while the node report and tooltip independently match the declared profile\'s cache state',
      ],
      browserChecks: [
        'the strip reads 6 requests and 1 error, then 4 and 1, then 1 and 0, and the table lists exactly 6, then 4, then 1 rows',
        'with the first application applied (no managed call listed), the Provider picker offers Future AGI where managed models are offered and lists no option where they are not',
        'with every request listed (the managed one among them), the Provider picker offers Future AGI in both shapes',
        'the managed request\'s Provider cell reads Future AGI',
        'Models retains its Cache hits column on stock and r7alt; only cache-off nodes show the selected-period tooltip',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const appA = `e2e-gw20-a-${suffix}`;
    const appB = `e2e-gw20-b-${suffix}`;
    const created = await owner.api.post<{ result: { gateway_key_id: string } }>(
      '/agentcc/api-keys/', { name: `e2e-gw20-${suffix}` });
    const gatewayKeyId = created.result.gateway_key_id;
    // The shape this deployment must show: with managed models offered, or with none.
    const managed = (await owner.api.get<{ result: { available: boolean } }>(MANAGED_STATUS_PATH))
      .result.available === true;

    // Oldest first, a few seconds apart and all in the past, so the strip's window
    // (oldest matching request to now) holds every one.
    const seeds = [
      { application: appA, status: 200 },
      { application: appA, status: 200 },
      { application: appA, status: 500 },
      { application: appA, status: 200 },
      { application: appB, status: 200 },
      { application: appB, status: 200, managed: true },
    ];
    const now = Date.now();
    const requestIds = seeds.map((_, i) => `e2e-gw20-${suffix}-${i + 1}`);
    const req = await request.newContext();
    try {
      for (const [i, s] of seeds.entries()) {
        const res = await req.post(`${E2E.apiUrl}${LOGS_WEBHOOK_PATH}`, {
          headers: { 'X-Webhook-Secret': WEBHOOK_SECRET },
          data: { logs: [{
            request_id: requestIds[i], auth_key_id: gatewayKeyId,
            model: 'gpt-4o-mini', provider: s.managed ? 'openrouter' : 'openai',
            status_code: s.status, is_error: s.status >= 400, latency_ms: 120, total_tokens: 42,
            cost: '0.000420', timestamp: new Date(now - (seeds.length - i) * 5000).toISOString(),
            billing_mode: s.managed ? 'managed' : 'byok', credential_type: s.managed ? 'managed' : 'byok',
            metadata: { application: s.application },
          }] },
        });
        expect(res.status(), await res.text()).toBe(200);
      }
    } finally {
      await req.dispose();
    }
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, organizationId: owner.organizationId, gatewayKeyId, appA, appB,
        requestIds, managedRequestId: requestIds[5], managedModelsOffered: managed }, null, 2),
    });

    await test.step('storage: the six requests are the org\'s, the managed one billed managed', async () => {
      await expect.poll(async () => (await owner.api.get<Paginated<unknown>>(REQUEST_LOGS_PATH)).count, LOG_VISIBLE)
        .toBe(seeds.length);
      const rows = await probe.pg<{ request_id: string; billing_mode: string; status_code: number }>(
        'SELECT request_id, billing_mode, status_code FROM agentcc_request_log WHERE organization_id = $1 ORDER BY request_id',
        [owner.organizationId]);
      expect(rows).toEqual(requestIds.map((request_id, i) => ({ request_id,
        billing_mode: seeds[i].managed ? 'managed' : 'byok', status_code: seeds[i].status })));
    });

    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    await page.setViewportSize(GRID_VIEWPORT);
    const grid = GatewayGridReader.in(page);
    const strip = page.getByTestId('request-volume').getByRole('img', { name: / requests, \d+ errors?\.$/ });
    // The strip's own volume read for the filters `want` names (application, provider),
    // and what the list API says for the same filters.
    const volumeAnswer = (p: Page, want: { application?: string; provider?: string }) => p.waitForResponse(
      (r: Response) => {
        const url = new URL(r.url());
        return r.request().method() === 'GET' && VOLUME_PATH.test(url.pathname) && r.ok() &&
          url.searchParams.get('application') === (want.application ?? null) &&
          url.searchParams.get('provider') === (want.provider ?? null);
      },
      { timeout: UI_READY });
    const readVolume = async (response: Response) => {
      const params = Object.fromEntries(new URL(response.url()).searchParams);
      const { result } = (await response.json()) as { result: { series: VolumePoint[] } };
      return { params, ...totals(result.series) };
    };
    const listCount = async (params: Record<string, string>) =>
      (await owner.api.get<Paginated<unknown>>(REQUEST_LOGS_PATH, params)).count;
    const stripSays = (requests: number, errors: number) =>
      new RegExp(`: ${requests} requests, ${errors} errors?\\.$`);
    const pick = async (placeholder: string, option: string) => {
      await page.getByPlaceholder(placeholder, { exact: true }).click();
      await page.getByRole('option', { name: option, exact: true }).click();
      await page.keyboard.press('Escape');
    };

    try {
      await test.step('UI + API: unfiltered, the strip counts the six requests and the one error', async () => {
        const answered = volumeAnswer(page, {});
        await page.goto('/dashboard/gateway/logs', { waitUntil: 'domcontentloaded' });
        const volume = await readVolume(await answered);
        await testInfo.attach('volume-all', { contentType: 'application/json', body: JSON.stringify(volume, null, 2) });
        expect({ requests: volume.requests, errors: volume.errors }).toEqual({ requests: 6, errors: 1 });
        await expect(strip).toHaveAttribute('aria-label', stripSays(6, 1), { timeout: UI_READY });
        await expect(grid.rows).toHaveCount(6, { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('UI + API: one application, the strip and the table both count its four requests', async () => {
        const answered = volumeAnswer(page, { application: appA });
        await page.getByRole('button', { name: 'Filters', exact: true }).click();
        await pick(APPLICATION_PLACEHOLDER, appA);
        await page.getByRole('button', { name: 'Apply', exact: true }).click();
        const volume = await readVolume(await answered);
        await testInfo.attach('volume-application', { contentType: 'application/json', body: JSON.stringify(volume, null, 2) });
        expect({ application: volume.params.application, requests: volume.requests, errors: volume.errors })
          .toEqual({ application: appA, requests: 4, errors: 1 });
        expect(await listCount({ application: appA })).toBe(4);
        await expect(strip).toHaveAttribute('aria-label', stripSays(4, 1), { timeout: UI_READY });
        await expect(grid.rows).toHaveCount(4, { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step(managed
        ? 'UI: with no managed call listed, the Provider picker still offers Future AGI, as managed models are offered'
        : 'UI: with no managed call listed and no managed models offered, the Provider picker lists no option', async () => {
        await page.getByRole('button', { name: 'Filters', exact: true }).click();
        const provider = page.getByPlaceholder(PROVIDER_PLACEHOLDER, { exact: true });
        await provider.click();
        await expect(provider).toBeFocused({ timeout: UI_READY });
        // The picker's list opens only when it has an option (aria-expanded).
        if (managed) {
          await expect(provider).toHaveAttribute('aria-expanded', 'true', { timeout: UI_READY });
          await expect(page.getByRole('option')).toHaveText([MANAGED_LABEL], { timeout: UI_READY });
          // Closes the picker's list; with the list closed, Escape would close the panel.
          await page.keyboard.press('Escape');
        } else {
          await expect(provider).toHaveAttribute('aria-expanded', 'false');
          await expect(page.getByRole('option')).toHaveCount(0);
        }
        const close = page.getByRole('button', { name: 'Close filters', exact: true });
        await close.click();
        await expect(close).toBeHidden({ timeout: UI_READY });
        // Nothing was applied: the table still lists the application's four rows.
        await expect(grid.rows).toHaveCount(4, { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('UI + API: the Future AGI provider, the strip and the table both count its one request', async () => {
        await page.getByRole('button', { name: 'Clear all', exact: true }).click();
        await expect(strip).toHaveAttribute('aria-label', stripSays(6, 1), { timeout: UI_READY });
        // The managed call is listed again, so the picker offers its provider in both shapes.
        await expect(grid.rows).toHaveCount(6, { timeout: UI_READY });
        const answered = volumeAnswer(page, { provider: MANAGED_LABEL });
        await page.getByRole('button', { name: 'Filters', exact: true }).click();
        await pick(PROVIDER_PLACEHOLDER, MANAGED_LABEL);
        await page.getByRole('button', { name: 'Apply', exact: true }).click();
        const volume = await readVolume(await answered);
        await testInfo.attach('volume-managed', { contentType: 'application/json', body: JSON.stringify(volume, null, 2) });
        expect({ provider: volume.params.provider, requests: volume.requests, errors: volume.errors })
          .toEqual({ provider: MANAGED_LABEL, requests: 1, errors: 0 });
        expect(await listCount({ provider: MANAGED_LABEL })).toBe(1);
        await expect(strip).toHaveAttribute('aria-label', stripSays(1, 0), { timeout: UI_READY });
        await expect(grid.rows).toHaveCount(1, { timeout: UI_READY });
        await expect.poll(() => grid.column('builtin:provider'), { timeout: UI_READY }).toEqual([MANAGED_LABEL]);
      }, { timeout: STEP_BUDGET });
      await test.step('UI + API: Models retains Cache hits independently of current cache enforcement', async () => {
        const configRead = page.waitForResponse((r) => r.request().method() === 'GET' &&
          new URL(r.url()).pathname === CONFIG_PATH, { timeout: UI_READY });
        const modelsRead = page.waitForResponse((r) => r.request().method() === 'GET' &&
          new URL(r.url()).pathname === MODELS_API, { timeout: UI_READY });
        await page.goto(MODELS_PATH, { waitUntil: 'domcontentloaded' });
        const configResponse = await configRead;
        expect(configResponse.status()).toBe(200);
        const config = await configResponse.json() as { result: { org_sections_enforced: string[];
          org_sections_not_enforced?: { cache?: string }; cache?: { enabled?: boolean } } };
        expect(Array.isArray(config.result.org_sections_enforced), 'known node report, not loading/error').toBe(true);
        // lib/profile.ts:328-345 pins node caching independently of this response.
        // Compare the report to the declaration; never let it choose the UI oracle.
        const cacheEnforced = PROFILE_FINGERPRINTS[walkGatewayProfile()].cacheEnforced;
        expect(config.result.org_sections_enforced.includes('cache')).toBe(cacheEnforced);
        const response = await modelsRead;
        expect(response.status()).toBe(200);
        const { result } = await response.json() as { result: { models: { request_count: number; cache_hit_rate: number | null }[] } };
        expect(result.models.reduce((n, row) => n + Number(row.request_count), 0)).toBe(6);
        // GatewayGrid renders no grid when empty: do not claim a header check on an empty state.
        await expect(grid.root).toBeVisible({ timeout: UI_READY });
        // AG Grid virtualizes columns; scroll its real viewport rather than assuming
        // all headers are mounted at GRID_VIEWPORT (lib/gateway-grid.ts:21-25).
        await grid.root.locator('.ag-body-horizontal-scroll-viewport').evaluate((el) => { el.scrollLeft = el.scrollWidth; });
        const header = grid.header(CACHE_HITS_COLUMN);
        await expect(header.locator('.ag-header-cell-text')).toHaveText('Cache hits', { timeout: UI_READY });
        // ModelComparison.jsx:20: exact selected-period percentages, not a new node-state value.
        const values = result.models.map((row) => row.cache_hit_rate == null ? '—' : `${Number(row.cache_hit_rate).toFixed(1)}%`).sort();
        await expect.poll(async () => (await grid.column(CACHE_HITS_COLUMN)).sort(), { timeout: UI_READY }).toEqual(values);
        await header.hover();
        if (!cacheEnforced) {
          await expect(page.getByText(CACHE_HITS_NOTE, { exact: true })).toBeVisible({ timeout: UI_READY });
        } else {
          // components/frame/GatewayGrid.jsx:140 delays tooltip display by 400ms.
          // Hold the hover for 3 x that delay before accepting absence: an immediate
          // zero-count check would also pass with an unconditional off-state tooltip.
          await page.waitForTimeout(1_200);
          await expect(page.getByText(CACHE_HITS_NOTE, { exact: true })).toHaveCount(0, { timeout: UI_READY });
        }
      }, { timeout: STEP_BUDGET });
    } finally {
      await context.close();
    }
  },
);
