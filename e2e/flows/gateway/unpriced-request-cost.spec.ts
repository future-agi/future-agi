import { request, type Locator, type Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { authInitScript } from '../../lib/auth';
import { provisionActor } from '../../lib/provisioning';

// agentcc/urls.py: provider credentials, org configs, API keys and request logs.
const CREDENTIALS = '/agentcc/provider-credentials/';
const CONFIGS = '/agentcc/org-configs/';
const KEYS = '/agentcc/api-keys/';
const REQUEST_LOGS = '/agentcc/request-logs/';
// paths.dashboard.gateway.logs.
const LOGS_ROUTE = '/dashboard/gateway/logs';
// docker-compose.distributed.yml default admin token, as in GW-E2E-004.
const ADMIN_TOKEN = 'local-dev-only-admin-token-replace-me';
// agentcc-gateway/internal/middleware/requestid.go; stored as agentcc_request_log.request_id.
const REQUEST_ID_HEADER = 'x-agentcc-request-id';
// The gateway posts each request log to the backend webhook; the poll absorbs its flush interval.
const LOG_VISIBLE = POLL.ASYNC_JOB;
const UI_READY = 60_000;
// frontend/src/sections/gateway/utils/formatters.js UNPRICED_LABEL / UNPRICED_TOOLTIP.
const UNPRICED_LABEL = 'Unpriced';
const UNPRICED_TOOLTIP = 'No price is set for this model, so this request was recorded without a cost. Add a model price to track request costs. Budgets use their own pricing and may still count this request.';
// Catalogue-priced control, served by the managed mock like every org-listed model.
const PRICED_MODEL = 'gpt-4o-mini';

type Envelope<T> = { result: T };
type CreatedKey = { id: string; key: string; gateway_key_id: string; name: string };
type LogRow = { id: string; request_id: string; model: string; cost: string | number | null; metadata: Record<string, string> | null };
type StoredLog = { request_id: string; status_code: number; cost: string; cost_source: string | null };
type GatewayConfigs = { data: Record<string, {
  providers?: Record<string, { models?: string[] }>;
  cost_tracking?: { enabled: boolean; custom_pricing?: Record<string, { input_per_mtok: number; output_per_mtok: number }> };
}> };

// frontend/src/sections/gateway/utils/formatters.js formatCost, for the priced control.
function formatCost(value: number) {
  const abs = Math.abs(value);
  if (abs >= 1000) return `$${(value / 1000).toFixed(1)}K`;
  if (abs >= 1) return `$${value.toFixed(2)}`;
  if (abs >= 0.01) return `$${value.toFixed(4)}`;
  if (abs >= 0.001) return `$${value.toFixed(5)}`;
  if (abs > 0) return `$${value.toFixed(7).replace(/0+$/, '0')}`;
  return '$0.00';
}

test('GW-E2E-023: request logs tell an unpriced request apart from a free and a priced one', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'GW-E2E-023',
    area: 'gateway',
    userGoal: 'Someone reading the gateway Request Logs can tell a request whose model has no price apart from a request that really cost $0 and from a priced one, in the table and in the request drawer',
    steps: [
      'provision an organization of the test\'s own, an org provider on the managed mock listing an unpriced, a zero-priced and gpt-4o-mini model, an org config pricing only the zero model at 0, and an org API key',
      'wait for the gateway to hold the pushed org config',
      'send one real chat completion per model with the org key',
      'open Request Logs and read the Cost cell of each of the three rows',
      'hover the Unpriced chip and read its tooltip',
      'open each request\'s drawer and read its Cost',
    ],
    backendChecks: [
      'the gateway answers all three completions with HTTP 200, a request id and the requested model',
      'PG agentcc_request_log holds exactly the three request ids: unpriced cost 0 with cost_source unpriced, free cost 0 with no cost_source, gpt-4o-mini cost above 0 with cost_source catalogue, all status 200',
      'the request-logs list and detail endpoints return the same three request IDs, costs and cost sources as the stored rows',
      'the table shows Unpriced with the explanatory tooltip only for the unpriced row, $0.00 for the free row and the formatted API cost for gpt-4o-mini',
      'each request drawer shows the same Cost as its table row',
    ],
  }),
}, async ({ browser, probe }, testInfo) => {
  // Provision 30 s + config push 60 + completions 10 + log flush 60 + five UI_READY stages 300
  // = 460 s; the rest is headroom so a slow stage fails on its own budget.
  test.setTimeout(540_000);
  const accounts = await request.newContext({ baseURL: E2E.apiUrl });
  const actor = await provisionActor(accounts, `gw-unpriced-${testInfo.workerIndex}`);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const unpricedModel = `e2e-gw23-unpriced-${suffix}`;
  const freeModel = `e2e-gw23-free-${suffix}`;
  const models = [unpricedModel, freeModel, PRICED_MODEL];

  // Bodies pinned from GW-E2E-004/007 and the credential, org-config and key serializers.
  // No base_url: the org provider layers onto gateway.e2e.yaml's `openai` entry (mock-llm).
  const credential = await actor.api.post<Envelope<{ id: string }>>(CREDENTIALS, {
    provider_name: 'openai',
    credentials: { api_key: 'synthetic-local-only' },
    api_format: 'openai',
    models_list: models,
  });
  const config = await actor.api.post<Envelope<{ id: string }>>(CONFIGS, {
    cost_tracking: { enabled: true, custom_pricing: { [freeModel]: { input_per_mtok: 0, output_per_mtok: 0 } } },
    change_description: `e2e-gw23-${suffix}`,
  });
  const key = (await actor.api.post<Envelope<CreatedKey>>(KEYS, { name: `e2e-gw23-key-${suffix}` })).result;

  const gateway = await request.newContext({ baseURL: E2E.gatewayUrl, timeout: 30_000 });
  const context = await browser.newContext({ baseURL: E2E.appUrl, viewport: { width: 1440, height: 900 } });
  await context.addInitScript(authInitScript, {
    access: actor.tokens.access,
    refresh: actor.tokens.refresh,
    organizationId: actor.organizationId,
    workspaceId: actor.workspaceId,
  });
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  try {
    await test.step('the gateway holds the pushed org config', async () => {
      await expect.poll(async () => {
        const res = await gateway.get('/-/orgs/configs', { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } });
        expect(res.status()).toBe(200);
        const org = (await res.json() as GatewayConfigs).data[actor.organizationId];
        return {
          models: [...(org?.providers?.openai?.models ?? [])].sort(),
          enabled: org?.cost_tracking?.enabled,
          free: org?.cost_tracking?.custom_pricing?.[freeModel],
        };
      }, POLL.ASYNC_JOB).toEqual({ models: [...models].sort(), enabled: true, free: { input_per_mtok: 0, output_per_mtok: 0 } });
    });

    const requestIds: Record<string, string> = {};
    await test.step('backend check 1: three real completions through the org key', async () => {
      for (const model of models) {
        const res = await gateway.post('/v1/chat/completions', {
          headers: { Authorization: `Bearer ${key.key}` },
          data: { model: `openai/${model}`, messages: [{ role: 'user', content: `e2e-gw23 ${suffix} ${model}` }] },
        });
        expect(res.status(), await res.text()).toBe(200);
        const id = res.headers()[REQUEST_ID_HEADER];
        expect(id, `${REQUEST_ID_HEADER} for ${model}`).toBeTruthy();
        expect(Object.values(requestIds)).not.toContain(id);
        expect((await res.json()).model).toBe(model);
        requestIds[model] = id;
      }
      await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify({
        organizationId: actor.organizationId, credentialId: credential.result.id, configId: config.result.id,
        keyId: key.id, gatewayKeyId: key.gateway_key_id, models, requestIds,
      }) });
    });

    let storedById: Record<string, StoredLog> = {};
    await test.step('backend check 2: stored cost and cost source per request', async () => {
      const ids = models.map(m => requestIds[m]);
      await expect.poll(async () => {
        const rows = await probe.pg<StoredLog>(
          `SELECT request_id, status_code, cost::text AS cost, metadata->>'cost_source' AS cost_source
           FROM agentcc_request_log WHERE organization_id = $1 AND deleted = false AND request_id = ANY($2::text[])`,
          [actor.organizationId, ids]);
        storedById = Object.fromEntries(rows.map(r => [r.request_id, r]));
        return Object.fromEntries(rows.map(r => [r.request_id, {
          status: r.status_code, cost: Number(r.cost) > 0 ? 'positive' : Number(r.cost), source: r.cost_source,
        }]));
      }, { ...LOG_VISIBLE, message: 'stored request logs' }).toEqual({
        [requestIds[unpricedModel]]: { status: 200, cost: 0, source: 'unpriced' },
        [requestIds[freeModel]]: { status: 200, cost: 0, source: null },
        [requestIds[PRICED_MODEL]]: { status: 200, cost: 'positive', source: 'catalogue' },
      });
    });

    const apiRows: Record<string, LogRow> = {};
    await test.step('backend check 3: list and detail endpoints return the same three rows', async () => {
      const list = await actor.api.get<{ count: number; results: LogRow[] }>(REQUEST_LOGS, { page_size: 50 });
      expect(list.results.map(r => r.request_id).sort()).toEqual(models.map(m => requestIds[m]).sort());
      for (const model of models) {
        const row = list.results.find(r => r.request_id === requestIds[model])!;
        const detail = (await actor.api.get<Envelope<LogRow>>(`${REQUEST_LOGS}${row.id}/`)).result;
        const stored = storedById[requestIds[model]];
        for (const apiRow of [row, detail]) {
          expect(apiRow.request_id).toBe(stored.request_id);
          expect(apiRow.cost).not.toBeNull();
          expect(Number(apiRow.cost)).toBe(Number(stored.cost));
          expect(apiRow.metadata?.cost_source ?? null).toBe(stored.cost_source);
        }
        apiRows[model] = row;
      }
      expect(Number(apiRows[unpricedModel].cost)).toBe(0);
      expect(apiRows[unpricedModel].metadata?.cost_source).toBe('unpriced');
      expect(Number(apiRows[freeModel].cost)).toBe(0);
      expect(apiRows[freeModel].metadata?.cost_source).toBeUndefined();
      expect(Number(apiRows[PRICED_MODEL].cost)).toBeGreaterThan(0);
    });
    const pricedText = () => formatCost(Number(apiRows[PRICED_MODEL].cost));

    let costColumn = -1;
    const row = (model: string) => page.locator('table tbody tr')
      .filter({ has: page.getByRole('cell', { name: apiRows[model].model, exact: true }) });
    const costCell = (model: string) => row(model).locator('td').nth(costColumn);

    await test.step('backend check 4: table Cost cells', async () => {
      const listed = page.waitForResponse(r => new URL(r.url()).pathname === REQUEST_LOGS &&
        r.request().method() === 'GET' && r.ok(), { timeout: UI_READY });
      await page.goto(LOGS_ROUTE, { waitUntil: 'domcontentloaded' });
      const shown = await (await listed).json() as { results: LogRow[] };
      expect(shown.results.map(r => r.request_id).sort()).toEqual(models.map(m => requestIds[m]).sort());
      await expect(page.locator('table tbody tr')).toHaveCount(3);
      const headers = (await page.locator('table thead th').allInnerTexts()).map(t => t.trim());
      costColumn = headers.indexOf('Cost');
      expect(costColumn, `Cost header in ${JSON.stringify(headers)}`).toBeGreaterThanOrEqual(0);
      expect(pricedText()).not.toBe('$0.00');
      await expect(costCell(unpricedModel)).toHaveText(UNPRICED_LABEL);
      await expect(costCell(freeModel)).toHaveText('$0.00');
      await expect(costCell(PRICED_MODEL)).toHaveText(pricedText());
      await expect(page.locator('table tbody').getByText(UNPRICED_LABEL, { exact: true })).toHaveCount(1);
    }, { timeout: UI_READY });

    await test.step('the Unpriced tooltip explains the missing price', async () => {
      await costCell(unpricedModel).getByText(UNPRICED_LABEL, { exact: true }).hover();
      await expect(page.getByRole('tooltip')).toHaveText(UNPRICED_TOOLTIP);
      await testInfo.attach('unpriced-tooltip', { contentType: 'image/png', body: await page.screenshot() });
      await page.mouse.move(0, 0);
      await expect(page.getByRole('tooltip')).toHaveCount(0);
    }, { timeout: UI_READY });

    const drawerCost = (p: Page): Locator => p.locator('.MuiDrawer-paper')
      .getByText('Cost', { exact: true }).locator('xpath=..');
    const openDrawer = async (model: string) => {
      const detail = page.waitForResponse(r => new URL(r.url()).pathname === `${REQUEST_LOGS}${apiRows[model].id}/` &&
        r.ok(), { timeout: UI_READY });
      await row(model).locator('td').first().click();
      await detail;
      await expect(page.locator('.MuiDrawer-paper')).toBeVisible();
    };
    const closeDrawer = async () => {
      await page.keyboard.press('Escape');
      await expect(page.locator('.MuiDrawer-paper')).toHaveCount(0);
    };

    await test.step('backend check 5: drawer Cost for the unpriced request', async () => {
      await openDrawer(unpricedModel);
      const cost = drawerCost(page);
      await expect(cost).toContainText(UNPRICED_LABEL);
      await cost.getByText(UNPRICED_LABEL, { exact: true }).hover();
      await expect(page.getByRole('tooltip')).toHaveText(UNPRICED_TOOLTIP);
      await testInfo.attach('unpriced-drawer', { contentType: 'image/png', body: await page.screenshot() });
      await closeDrawer();
    }, { timeout: UI_READY });

    await test.step('backend check 5: drawer Cost for the free request', async () => {
      await openDrawer(freeModel);
      await expect(drawerCost(page)).toHaveText('Cost$0.00');
      await closeDrawer();
    }, { timeout: UI_READY });

    await test.step('backend check 5: drawer Cost for the priced request', async () => {
      await openDrawer(PRICED_MODEL);
      await expect(drawerCost(page)).toHaveText(`Cost${pricedText()}`);
      await closeDrawer();
    }, { timeout: UI_READY });
  } finally {
    await context.close();
    await gateway.dispose();
    await accounts.dispose();
  }
});
