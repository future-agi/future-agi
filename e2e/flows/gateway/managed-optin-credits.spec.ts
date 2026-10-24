import { request } from '@playwright/test';
import type { APIRequestContext } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { MANAGED_SETTINGS } from '../../lib/managed-mock';

// Pinned off the running system: the managed models card on the Models page
// (frontend/src/sections/gateway/models/ManagedModelsCard.jsx: the "Use
// managed models" switch, the Enable confirmation, the Enabled chip, the
// credit balance and the out-of-credit warning), the update-config action the
// switch saves through (useManagedModels.js useSetManagedModelsEnabled), the
// status endpoint the card reads (agentcc/views/managed_models.py list), the
// org config table (agentcc_org_config.managed_models), and the gateway's
// managed-credit refusal (agentcc-gateway/internal/plugins/credits/
// refusals.go: 402 managed_credits_exhausted, limit_source agentcc_credits).
//
// Nothing is seeded on the gateway: opting in makes Django create the org's
// managed-credit account at zero and push it (agentcc/services/config_push.py
// _ensure_managed_account -> ee/usage/services/managed_credits.py
// ensure_account_pushed -> PUT /-/orgs/{org}/credits/granted). Without that
// push an opted-in org gets 503 account_state_missing, never a 402,
// so the 402 is also the proof that the account reached the gateway. The
// gateway ledger is its in-memory one (MANAGED_SETTINGS.require_redis false).
const MODELS_PATH = '/dashboard/gateway/models';
const STATUS_PATH = '/agentcc/managed-models/';
const CATALOG_PATH = '/agentcc/managed-models/catalog/';
const UPDATE_CONFIG = /\/agentcc\/gateways\/[^/]+\/update-config\/$/;
// refusals.go refusal(ReserveExhausted): the message the 402 carries and the
// request log stores (agentcc/services/log_ingestion.py error_message).
const EXHAUSTED_MESSAGE =
  'Your organization has no managed credits left. Add credits or use your own provider keys.';
const REQUEST_LOGS_PATH = '/agentcc/request-logs/';
// The gateway flushes request logs to Django in batches.
const LOG_VISIBLE = POLL.ASYNC_JOB;
const OUT_OF_CREDIT =
  'Your managed-model credit is used up. The gateway refuses requests to managed models (HTTP 402) until credit is added';
// The org config push on save, with the gateway's 60 s sync behind it.
const PUSH_VISIBLE = POLL.ASYNC_JOB;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors provisioning 120 + seeding 15 + three UI steps of
// STEP_BUDGET 75 (225) + PUSH_VISIBLE 60 + LOG_VISIBLE 60 + headroom 30.
const FLOW_TIMEOUT = 510_000;

interface CatalogModel {
  id: string;
  dynamic_price?: boolean;
  billed_input_per_mtok?: number;
  billed_output_per_mtok?: number;
}
// A refusal's body; a served call has no `error`, so a 200 reads as code undefined.
interface GatewayError {
  error?: { code: string; type: string; metadata?: Record<string, unknown> };
}

// scopeActors signs its users up before the body runs, so its setup falls
// under the file's timeout, not the body's test.setTimeout: without this
// line it gets the 120 s project default.
test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-007: an org admin turns on managed models and, with no credit, sees why managed calls are refused',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-007',
      area: 'gateway',
      userGoal:
        'An org admin turns on managed models and, with no credit left, sees in the console why managed requests are refused',
      steps: [
        'mint a gateway API key in an organization of its own',
        'call a managed catalog model with the key before opting in',
        'open Models, switch on Use managed models and confirm Enable',
        'read the card: Enabled, the credit balance and the out-of-credit warning',
        'call the same managed model again',
        'open Request Logs and the refused request',
      ],
      backendChecks: [
        'before opt-in the gateway refuses the managed model for this org\'s key as not permitted, not as a 402',
        'the switch saves managed_models.enabled true in the org\'s active PG agentcc_org_config version and creates the org\'s managed-credit account at zero in PG usage_managedcreditaccount',
        'the card shows Enabled, a $0.00 balance and the out-of-credit warning naming HTTP 402',
        'the managed-models status API reports enabled with a zero balance at the gateway markup',
        'after opt-in the gateway answers the same call with 402 managed_credits_exhausted from the agentcc_credits limit, so the zero account was pushed to it',
        'the refused call is logged under the org as a managed 402 with the gateway\'s reason, and Request Logs show exactly that row and reason',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;

    const created = await owner.api.post<{ result: { key: string; gateway_key_id: string } }>(
      '/agentcc/api-keys/',
      { name: `e2e-gw7-${suffix}` },
    );
    const rawKey = created.result.key;
    // A managed catalog model with a list price: the first id, sorted.
    const catalog = (await owner.api.get<{ result: { models: CatalogModel[] } }>(CATALOG_PATH)).result;
    const model = [...catalog.models]
      .filter((m) => /^[a-z0-9]/.test(m.id) && !m.dynamic_price && Number.isFinite(m.billed_input_per_mtok))
      .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))[0].id;

    const gw: APIRequestContext = await request.newContext();
    const callManaged = async () => {
      const res = await gw.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
        headers: { Authorization: `Bearer ${rawKey}` },
        data: { model, messages: [{ role: 'user', content: `e2e-gw7-${suffix}` }] },
      });
      return { status: res.status(), body: (await res.json()) as GatewayError };
    };

    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    try {
      await testInfo.attach('seeded', {
        contentType: 'application/json',
        body: JSON.stringify({ suffix, organizationId: owner.organizationId,
          gatewayKeyId: created.result.gateway_key_id, model }, null, 2),
      });

      await test.step('gateway: before opt-in the managed model is not permitted for this key', async () => {
        const before = await callManaged();
        expect({ status: before.status, code: before.body.error?.code })
          .toEqual({ status: 403, code: 'permission_denied' });
      });

      await test.step('UI: switch on managed models and confirm Enable', async () => {
        await page.goto(MODELS_PATH, { waitUntil: 'domcontentloaded' });
        const toggle = page.getByLabel('Use managed models');
        await expect(toggle).not.toBeChecked({ timeout: UI_READY });
        await toggle.click();
        const dialog = page.getByRole('dialog', { name: 'Enable managed models?' });
        await expect(dialog).toBeVisible({ timeout: UI_READY });
        const saved = page.waitForResponse(
          (r) => UPDATE_CONFIG.test(new URL(r.url()).pathname) && r.request().method() === 'POST',
          { timeout: UI_READY },
        );
        await dialog.getByRole('button', { name: 'Enable', exact: true }).click();
        const response = await saved;
        expect(response.status()).toBe(200);
        expect(response.request().postDataJSON()).toEqual({ managed_models: { enabled: true } });
        await expect(page.getByText('Managed models enabled', { exact: true })).toBeVisible({ timeout: UI_READY });

        const rows = await probe.pg<{ managed_models: Record<string, unknown> }>(
          `SELECT managed_models FROM agentcc_org_config
            WHERE organization_id = $1 AND is_active AND NOT deleted`,
          [owner.organizationId],
        );
        expect(rows.map((row) => row.managed_models.enabled)).toEqual([true]);
        const accounts = await probe.pg<{ granted_total_micros: string; settled_spend_micros: string }>(
          `SELECT granted_total_micros, settled_spend_micros FROM usage_managedcreditaccount
            WHERE organization_id = $1`,
          [owner.organizationId],
        );
        expect(accounts.map((a) => [Number(a.granted_total_micros), Number(a.settled_spend_micros)]))
          .toEqual([[0, 0]]);
      }, { timeout: STEP_BUDGET });

      await test.step('UI: the card says Enabled, $0.00 and why managed calls are refused', async () => {
        await expect(page.getByLabel('Use managed models')).toBeChecked({ timeout: UI_READY });
        await expect(page.getByText('Enabled', { exact: true })).toBeVisible({ timeout: UI_READY });
        await expect(page.getByText('Credit balance', { exact: true }).locator('..'))
          .toHaveText('Credit balance$0.00', { timeout: UI_READY });
        await expect(page.getByRole('alert').filter({ hasText: OUT_OF_CREDIT })).toHaveCount(1, { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('API: the status endpoint reports enabled with a zero balance', async () => {
        const status = (await owner.api.get<{ result: Record<string, unknown> }>(STATUS_PATH)).result;
        expect(status).toEqual({ available: true, enabled: true, balance_usd: 0,
          markup_percent: MANAGED_SETTINGS.markup_percent });
      });

      await test.step('gateway: after opt-in the same call is a 402 from the credits limit', async () => {
        await expect
          .poll(async () => {
            const after = await callManaged();
            return { status: after.status, code: after.body.error?.code,
              limit_source: after.body.error?.metadata?.limit_source };
          }, PUSH_VISIBLE)
          .toEqual({ status: 402, code: 'managed_credits_exhausted', limit_source: 'agentcc_credits' });
      });

      await test.step('API + UI: Request Logs show the refused call as a managed 402 with its reason', async () => {
        type Row = { id: string; model: string; status_code: number; billing_mode: string | null;
          is_error: boolean; error_message: string };
        await expect
          .poll(async () => (await owner.api.get<{ count: number }>(REQUEST_LOGS_PATH)).count, LOG_VISIBLE)
          .toBeGreaterThanOrEqual(1);
        const logged = (await owner.api.get<{ results: Row[] }>(REQUEST_LOGS_PATH)).results;
        // Every refusal the poll above sent is a 402 of this model; the 403
        // before opt-in is refused before the request is logged.
        expect([...new Set(logged.map(({ model: m, status_code, billing_mode, is_error, error_message }) =>
          JSON.stringify({ model: m, status_code, billing_mode, is_error, error_message })))])
          .toEqual([JSON.stringify({ model, status_code: 402, billing_mode: 'managed', is_error: true,
            error_message: EXHAUSTED_MESSAGE })]);

        await page.goto('/dashboard/gateway/logs', { waitUntil: 'domcontentloaded' });
        const rows = page.locator('table tbody tr');
        await expect(rows).toHaveCount(logged.length, { timeout: UI_READY });
        const headerTexts = await page.locator('table thead th').allInnerTexts();
        const statusCells = rows.locator(`td:nth-child(${headerTexts.indexOf('Status') + 1})`);
        await expect(statusCells).toHaveText(logged.map(() => '402'), { timeout: UI_READY });
        await rows.first().click();
        await expect(page.getByRole('alert').filter({ hasText: EXHAUSTED_MESSAGE })).toHaveCount(1, { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });
    } finally {
      await gw.dispose();
      await context.close();
    }
  },
);
