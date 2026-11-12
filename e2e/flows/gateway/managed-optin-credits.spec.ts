import { request } from '@playwright/test';
import type { APIRequestContext } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { GatewayGridReader, GRID_VIEWPORT } from '../../lib/gateway-grid';
import { MANAGED_SETTINGS } from '../../lib/managed-mock';
import { NEW_ORG_DEFAULT_OFF_TAG } from '../../lib/profile';

// Pinned off the running system: the Future AGI credits card on the Models
// page (frontend/src/sections/gateway/models/ManagedModelsCard.jsx: the switch
// named by CARD_TITLE only when enabled, the neutral OFF notice, Enabled chip, the credit
// balance and the out-of-credit warning), the v2 Request Logs grid read in
// display order through lib/gateway-grid.ts, the update-config action the
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
// push an opted-in org gets 503 account_state_missing, never a 402 (BPC#13),
// so the 402 is also the proof that the account reached the gateway. The
// gateway ledger is its in-memory one (MANAGED_SETTINGS.require_redis false).
const MODELS_PATH = '/dashboard/gateway/models';
const STATUS_PATH = '/agentcc/managed-models/';
// managed_credits.py credit_summary and billing/useManagedCredits.js SUMMARY:
// Standalone has no cloud checkout, so this capability must be false.
const CREDIT_SUMMARY_PATH = '/agentcc/managed-credits/';
const CATALOG_PATH = '/agentcc/managed-models/catalog/';
// useManagedModels.js and agentcc/serializers/contracts.py: real org-config write.
const UPDATE_CONFIG = '/agentcc/gateways/default/update-config/';
// ManagedModelsCard.jsx OFF branch at 3c20e189ea; no console opt-in is claimed.
const OFF_NOTICE = 'Future AGI credits are off for this organization.';
// refusals.go refusal(ReserveExhausted): the message the 402 carries and the
// request log stores (agentcc/services/log_ingestion.py error_message).
const EXHAUSTED_MESSAGE =
  'Your organization has no Future AGI credit. Credit purchase is not available on this self-hosted install, so credit is granted by its operator: ask them to grant Future AGI credit, or use your own provider keys on the Gateway Providers page.';
const REQUEST_LOGS_PATH = '/agentcc/request-logs/';
// The gateway flushes request logs to Django in batches.
const LOG_VISIBLE = POLL.ASYNC_JOB;
const OUT_OF_CREDIT =
  'Your Future AGI credits are used up. The gateway refuses requests to Future AGI models (HTTP 402) until credit is added';
// ManagedModelsCard.jsx CARD_TITLE: the card's title and its switch's name.
const CARD_TITLE = 'Future AGI credits';
// ManagedModelsCard.jsx current/legacy names only; the CatalogFacets.jsx Access
// filter "Future AGI credits (<count>)" is not an enablement control.
const CREDITS_CONTROL_NAME = /^Future AGI credits(?:: one key for every model)?$/;
// The org config push on save, with the gateway's 60 s sync behind it.
const PUSH_VISIBLE = POLL.GATEWAY_SYNC;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors 120 + seeding 15 + four UI/API steps x 75 (300)
// + gateway sync 75 + log visibility 60 + headroom 30 = 600 seconds.
const FLOW_TIMEOUT = 600_000;

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
  'GW-E2E-007: an org admin distinguishes credits OFF from an enabled organization with no credit',
  {
    // Starts OFF. Explicit API fixture setup enables it; there is no UI opt-in.
    tag: ['@flow', NEW_ORG_DEFAULT_OFF_TAG],
    annotation: flowAnnotation({
      id: 'GW-E2E-007',
      area: 'gateway',
      userGoal:
        'An org admin distinguishes managed credits being off from an enabled organization having no credit, and reads the corresponding refusal in the console; API enablement is fixture setup, not a console opt-in journey',
      steps: [
        'mint a gateway API key in an organization of its own',
        'call a managed catalog model with the key before opting in',
        'open Models and read the neutral OFF notice with no enable control',
        'enable managed models through the real organization-config API as fixture setup, then reload Models',
        'read the card: Enabled, the credit balance and the out-of-credit warning',
        'call the same managed model again',
        'open Request Logs and the refused request',
      ],
      backendChecks: [
        'before opt-in the gateway refuses the managed model for this org\'s key as Future AGI credits off, not as a 402',
        'the API fixture save persists managed_models.enabled true in the org\'s active PG agentcc_org_config version and creates the org\'s managed-credit account at zero in PG usage_managedcreditaccount',
        'before API enablement the loaded Models page has the neutral OFF notice and no credits enable control',
        'the card shows Enabled, a $0.00 balance and the out-of-credit warning naming HTTP 402',
        'the managed-models status API reports enabled with a zero balance at the gateway markup, and the credit summary reports billing_available false on Standalone',
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
    await page.setViewportSize(GRID_VIEWPORT);
    try {
      await testInfo.attach('seeded', {
        contentType: 'application/json',
        body: JSON.stringify({ suffix, organizationId: owner.organizationId,
          gatewayKeyId: created.result.gateway_key_id, model }, null, 2),
      });

      await test.step('gateway: before opt-in the managed model is refused as Future AGI credits being off', async () => {
        const before = await callManaged();
        expect({ status: before.status, code: before.body.error?.code })
          .toEqual({ status: 403, code: 'future_agi_credits_off' });
      });

      await test.step('UI: OFF is a neutral notice, not a console opt-in control', async () => {
        const status = (await owner.api.get<{ result: { enabled: boolean } }>(STATUS_PATH)).result;
        expect(status.enabled).toBe(false);
        await page.goto(MODELS_PATH, { waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('alert').filter({ hasText: OFF_NOTICE })).toHaveText(OFF_NOTICE, { timeout: UI_READY });
        await expect(page.getByRole('checkbox', { name: CREDITS_CONTROL_NAME, includeHidden: true })).toHaveCount(0, { timeout: UI_READY });
        await expect(page.getByRole('switch', { name: CREDITS_CONTROL_NAME, includeHidden: true })).toHaveCount(0, { timeout: UI_READY });
        await expect(page.getByRole('dialog', { name: 'Turn on Future AGI credits?' })).toHaveCount(0, { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('fixture API: enable managed models and read back the saved zero-credit account', async () => {
        const saved = await owner.api.post<{ result: { version: number } }>(UPDATE_CONFIG, { managed_models: { enabled: true } });
        expect(saved.result.version).toBeGreaterThan(0);
        await testInfo.attach('enabled-config', { contentType: 'application/json',
          body: JSON.stringify({ organizationId: owner.organizationId, version: saved.result.version, enabled: true }) });

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

      await test.step('UI: reload after API setup; the card says Enabled, $0.00 and why calls are refused', async () => {
        await page.reload({ waitUntil: 'domcontentloaded' });
        await expect(page.getByLabel(CARD_TITLE, { exact: true })).toBeChecked({ timeout: UI_READY });
        await expect(page.getByText('Enabled', { exact: true })).toBeVisible({ timeout: UI_READY });
        await expect(page.getByText('Credit balance', { exact: true }).locator('..'))
          .toHaveText('Credit balance$0.00', { timeout: UI_READY });
        await expect(page.getByRole('alert').filter({ hasText: OUT_OF_CREDIT })).toHaveCount(1, { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('API: the status endpoint reports enabled with a zero balance', async () => {
        const status = (await owner.api.get<{ result: Record<string, unknown> }>(STATUS_PATH)).result;
        expect(status).toEqual({ available: true, enabled: true, balance_usd: 0,
          markup_percent: MANAGED_SETTINGS.markup_percent });
        const credits = (await owner.api.get<{ result: { billing_available: boolean } }>(CREDIT_SUMMARY_PATH)).result;
        expect(credits.billing_available).toBe(false);
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
        const grid = GatewayGridReader.in(page);
        await expect(grid.rows).toHaveCount(logged.length, { timeout: UI_READY });
        await expect.poll(() => grid.columnHeaded('Status'), { timeout: UI_READY }).toEqual(logged.map(() => '402'));
        await grid.row(0).click();
        await expect(page.getByRole('alert').filter({ hasText: EXHAUSTED_MESSAGE })).toHaveCount(1, { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });
    } finally {
      await gw.dispose();
      await context.close();
    }
  },
);
