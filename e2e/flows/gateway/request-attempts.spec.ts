import { request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned off the running system, like request-log-columns.spec.ts: the logs
// webhook the gateway flushes to and its secret (agentcc/views/webhook.py
// GatewayWebhookView, e2e/stack/*.env AGENTCC_WEBHOOK_SECRET), the list and
// detail endpoints the Request Logs table and drawer call
// (agentcc/views/request_log.py list/retrieve), and the Columns picker.
//
// The two entries are the gateway's own log entry v2 contract golden
// (api_contracts/gateway/examples/log-entry-v2.json: a managed request whose
// primary attempt was rate limited and an auto fallback served it), with the
// ids minted per run and round prices. They are delivered on the webhook
// because the deterministic mock cannot fail an attempt: this flow proves
// ingest -> Postgres -> API -> console, not that the gateway produces them.
const LOGS_WEBHOOK_PATH = '/agentcc/webhook/logs/';
const WEBHOOK_SECRET =
  process.env.AGENTCC_WEBHOOK_SECRET || 'e2e-agentcc-webhook-secret';
const REQUEST_LOGS_PATH = '/agentcc/request-logs/';
// The Agent & billing columns this flow ticks (logs/columns/gatewayColumns.js).
const BILLING_COLUMNS = ['Billing mode', 'Upstream cost', 'Markup', 'Attempts'];
// Cell text per row, newest first: labels from logs/explorerLabels.js
// BILLING_MODES_SHORT, money from utils/formatters.js formatCost (>= 0.01 four
// decimals, >= 0.001 five, below that seven with trailing zeros cut to one),
// and a dash where a v2 field is absent (columns/GatewayCell.jsx). The gateway
// omits cost_markup on BYOK (plugins/logging/flusher.go omitempty).
const EXPECTED_CELLS = [
  { 'Billing mode': 'Managed', 'Upstream cost': '$0.0120', Markup: '$0.00060', Attempts: '2' },
  { 'Billing mode': 'BYOK', 'Upstream cost': '$0.00210', Markup: '-', Attempts: '1' },
];
// The landed cost is FutureAGI's own number: staff-only in the API
// (serializers/request_log.py STAFF_ONLY_ATTEMPT_FIELDS, cost_landed) and never
// rendered (attempts/AttemptsTimeline.jsx). Its formatted value must not appear.
const LANDED_USD = 0.0131;
const LANDED_TEXT = '$0.0131';
const LOG_VISIBLE = POLL.ASYNC_JOB;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors provisioning 120 + seeding 15 + LOG_VISIBLE 60 + three UI
// steps of STEP_BUDGET 75 (225) + headroom 30.
const FLOW_TIMEOUT = 450_000;

interface RequestLogRow {
  id: string;
  request_id: string;
}
interface Paginated<T> {
  count: number;
  results: T[];
}
interface DetailAttempt {
  index: number;
  kind: string;
  outcome: string;
  reason: string;
  [field: string]: unknown;
}
interface RequestLogDetail {
  request_id: string;
  attempt_count: number;
  attempts: DetailAttempt[];
  billing: { mode: string; credential_type: string; cost_upstream: string; cost_markup: string | null } & Record<string, unknown>;
}

// scopeActors signs its users up before the body runs, so its setup falls
// under the file's timeout, not the body's test.setTimeout: without this
// line it gets the 120 s project default.
test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-004: Request Logs show how each gateway request was billed and every upstream attempt behind it',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-004',
      area: 'gateway',
      userGoal:
        'A platform engineer sees, for each gateway request, how it was billed and every upstream attempt behind it',
      steps: [
        'mint a gateway API key in an organization of its own',
        'deliver two gateway requests on the logs webhook: a managed request whose first attempt was rate limited and fell back, and a BYOK request served on its first attempt',
        'open Request Logs, open Columns and tick Billing mode, Upstream cost, Markup and Attempts',
        'read the two rows\' billing cells',
        'open the managed request and read its attempts in the detail drawer',
      ],
      backendChecks: [
        'each request is stored in PG agentcc_request_log under the key\'s organization with the billing mode, upstream cost, markup and attempt count the gateway reported',
        'PG agentcc_request_attempt holds exactly the managed request\'s two attempts in index order, error then success, and the BYOK request\'s one',
        'the request-log detail endpoint returns those attempts in order and no landed cost',
        'the table shows exactly Managed and BYOK with each row\'s upstream cost, markup and attempt count',
        'the drawer lists the two attempts in order, rate-limited primary then the auto fallback that was billed, and the landed cost appears nowhere on the page',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    // An organization per test: GW-E2E-001 asserts exact org-wide request-log
    // sets on the worker actor, so these logs must not land there.
    const owner = scopes.ownerA;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const managedId = `e2e-gw4-${suffix}-managed`;
    const byokId = `e2e-gw4-${suffix}-byok`;

    const created = await owner.api.post<{ result: { gateway_key_id: string } }>(
      '/agentcc/api-keys/',
      { name: `e2e-gw4-${suffix}` },
    );
    const gatewayKeyId = created.result.gateway_key_id;
    const now = Date.now();

    // log-entry-v2.json, managed: primary 429 then auto_fallback 200, 5% markup.
    const managedEntry = {
      request_id: managedId,
      generation_id: managedId,
      timestamp: new Date(now + 1000).toISOString(),
      model: 'auto',
      resolved_model: 'anthropic/claude-sonnet-5.5',
      provider: 'openrouter',
      prompt_tokens: 1820,
      completion_tokens: 412,
      total_tokens: 2232,
      status_code: 200,
      latency_ms: 2311,
      is_stream: false,
      is_error: false,
      cache_hit: false,
      fallback_used: true,
      guardrail_triggered: false,
      cost: 0.0126,
      auth_key_id: gatewayKeyId,
      metadata: { cost_markup_percent: '5' },
      attempts: [
        {
          attempt_id: `${managedId}.1`, index: 1, kind: 'primary', reason: '',
          provider: 'openrouter', model: 'openai/gpt-6-sol',
          endpoint_id: 'openrouter:openai:openai/gpt-6-sol', upstream_provider: 'openai',
          credential_type: 'managed', status_code: 429, outcome: 'error',
          error_class: 'rate_limited', error_origin: 'upstream',
          error_message: 'Rate limit reached for requests',
          started_at: new Date(now - 2000).toISOString(), latency_ms: 180, ttfb_ms: 0,
          prompt_tokens: 0, completion_tokens: 0,
          upstream_cost_micros: 0, billed_cost_micros: 0, landed_cost_micros: 0,
          upstream_fee_bps: 550, processing_fee_bps: 320, cost_source: 'unpriced',
          usage_estimated: false, billable: false,
        },
        {
          attempt_id: `${managedId}.2`, index: 2, kind: 'auto_fallback', reason: 'status_429',
          provider: 'openrouter', model: 'anthropic/claude-sonnet-5.5',
          endpoint_id: 'openrouter:anthropic:anthropic/claude-sonnet-5.5',
          upstream_provider: 'anthropic', served_upstream_provider: 'anthropic',
          credential_type: 'managed', status_code: 200, outcome: 'success',
          started_at: new Date(now - 1800).toISOString(), latency_ms: 2117, ttfb_ms: 640,
          prompt_tokens: 1820, completion_tokens: 412,
          upstream_cost_micros: 12000, billed_cost_micros: 12600,
          landed_cost_micros: Math.round(LANDED_USD * 1_000_000),
          upstream_fee_bps: 550, processing_fee_bps: 320, cost_source: 'provider_reported',
          usage_estimated: false, billable: true,
        },
      ],
      attempt_count: 2,
      billing_mode: 'managed',
      credential_type: 'managed',
      cost_upstream: 0.012,
      cost_markup: 0.0006,
      cost_markup_bps: 500,
      cost_source: 'provider_reported',
      usage_estimated: false,
      cost_landed: LANDED_USD,
      content_mode: 'metadata',
    };
    // The same shape served on its first attempt with the org's own key: no
    // markup field, as the flusher omits a zero.
    const byokEntry = {
      request_id: byokId,
      generation_id: byokId,
      timestamp: new Date(now).toISOString(),
      model: 'gpt-4o-mini',
      resolved_model: 'gpt-4o-mini',
      provider: 'openai',
      prompt_tokens: 600,
      completion_tokens: 200,
      total_tokens: 800,
      status_code: 200,
      latency_ms: 640,
      is_stream: false,
      is_error: false,
      cache_hit: false,
      fallback_used: false,
      guardrail_triggered: false,
      cost: 0.0021,
      auth_key_id: gatewayKeyId,
      attempts: [
        {
          attempt_id: `${byokId}.1`, index: 1, kind: 'primary', reason: '',
          provider: 'openai', model: 'gpt-4o-mini', endpoint_id: 'openai:openai:gpt-4o-mini',
          upstream_provider: 'openai', credential_type: 'byok', status_code: 200,
          outcome: 'success', started_at: new Date(now - 700).toISOString(),
          latency_ms: 640, ttfb_ms: 210, prompt_tokens: 600, completion_tokens: 200,
          upstream_cost_micros: 2100, billed_cost_micros: 2100,
          cost_source: 'catalog', usage_estimated: false, billable: true,
        },
      ],
      attempt_count: 1,
      billing_mode: 'byok',
      credential_type: 'byok',
      cost_upstream: 0.0021,
      cost_source: 'catalog',
      usage_estimated: false,
      content_mode: 'metadata',
    };

    const req = await request.newContext();
    try {
      for (const entry of [managedEntry, byokEntry]) {
        const res = await req.post(`${E2E.apiUrl}${LOGS_WEBHOOK_PATH}`, {
          headers: { 'X-Webhook-Secret': WEBHOOK_SECRET },
          data: { logs: [entry] },
        });
        expect(res.status(), await res.text()).toBe(200);
      }
    } finally {
      await req.dispose();
    }

    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify(
        { suffix, organizationId: owner.organizationId, gatewayKeyId, requestIds: [managedId, byokId] },
        null,
        2,
      ),
    });

    let managedLogId = '';

    await test.step('storage: the requests and their attempts are stored under the org', async () => {
      await expect
        .poll(
          async () => (await owner.api.get<Paginated<RequestLogRow>>(REQUEST_LOGS_PATH)).count,
          LOG_VISIBLE,
        )
        .toBe(2);

      const logs = await probe.pg<{
        id: string; request_id: string; billing_mode: string; credential_type: string;
        cost_upstream: string; cost_markup: string | null; attempt_count: number;
      }>(
        `SELECT id, request_id, billing_mode, credential_type, cost_upstream, cost_markup, attempt_count
           FROM agentcc_request_log WHERE organization_id = $1 ORDER BY request_id`,
        [owner.organizationId],
      );
      expect(
        logs.map((row) => ({
          request_id: row.request_id,
          billing_mode: row.billing_mode,
          credential_type: row.credential_type,
          cost_upstream: Number(row.cost_upstream),
          cost_markup: row.cost_markup === null ? null : Number(row.cost_markup),
          attempt_count: row.attempt_count,
        })),
      ).toEqual([
        { request_id: byokId, billing_mode: 'byok', credential_type: 'byok', cost_upstream: 0.0021, cost_markup: null, attempt_count: 1 },
        { request_id: managedId, billing_mode: 'managed', credential_type: 'managed', cost_upstream: 0.012, cost_markup: 0.0006, attempt_count: 2 },
      ]);
      managedLogId = logs.find((row) => row.request_id === managedId)!.id;

      const attempts = await probe.pg<{ request_id: string; index: number; kind: string; outcome: string }>(
        `SELECT request_id, index, kind, outcome FROM agentcc_request_attempt
          WHERE organization_id = $1 ORDER BY request_id, index`,
        [owner.organizationId],
      );
      expect(attempts).toEqual([
        { request_id: byokId, index: 1, kind: 'primary', outcome: 'success' },
        { request_id: managedId, index: 1, kind: 'primary', outcome: 'error' },
        { request_id: managedId, index: 2, kind: 'auto_fallback', outcome: 'success' },
      ]);
    });

    await test.step('API: the detail endpoint returns the attempts in order and no landed cost', async () => {
      const detail = (
        await owner.api.get<{ result: RequestLogDetail }>(`${REQUEST_LOGS_PATH}${managedLogId}/`)
      ).result;
      expect(detail.request_id).toBe(managedId);
      expect(detail.attempt_count).toBe(2);
      expect(detail.attempts.map(({ index, kind, outcome, reason }) => ({ index, kind, outcome, reason }))).toEqual([
        { index: 1, kind: 'primary', outcome: 'error', reason: '' },
        { index: 2, kind: 'auto_fallback', outcome: 'success', reason: 'status_429' },
      ]);
      expect(detail.billing.mode).toBe('managed');
      expect(Object.keys(detail.billing)).not.toContain('cost_landed');
      for (const attempt of detail.attempts) {
        expect(Object.keys(attempt)).not.toContain('landed_cost_micros');
      }
    });

    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    const headers = page.locator('table thead th');
    const rows = page.locator('table tbody tr');

    await test.step('UI: tick the billing columns and read both rows', async () => {
      await page.goto('/dashboard/gateway/logs', { waitUntil: 'domcontentloaded' });
      await expect(rows).toHaveCount(2, { timeout: UI_READY });

      await page.getByRole('button', { name: 'Columns' }).click();
      const dialog = page.getByRole('dialog', { name: 'Choose columns' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      for (const name of BILLING_COLUMNS) {
        await dialog.getByRole('checkbox', { name, exact: true }).check();
      }
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden({ timeout: UI_READY });

      const headerTexts = await headers.allInnerTexts();
      for (const name of BILLING_COLUMNS) expect(headerTexts).toContain(name);
      await expect
        .poll(
          async () =>
            Promise.all(
              (await rows.all()).map(async (row) => {
                const cells = await row.locator('td').allInnerTexts();
                return Object.fromEntries(
                  BILLING_COLUMNS.map((name) => [name, cells[headerTexts.indexOf(name)].trim()]),
                );
              }),
            ),
          { timeout: UI_READY },
        )
        .toEqual(EXPECTED_CELLS);
    }, { timeout: STEP_BUDGET });

    await test.step('UI: the drawer lists both attempts of the managed request', async () => {
      const detailLoaded = page.waitForResponse(
        (r) => r.url().includes(`${REQUEST_LOGS_PATH}${managedLogId}/`) && r.ok(),
        { timeout: UI_READY },
      );
      // Newest first: the managed request is the first row.
      await rows.first().click();
      await detailLoaded;

      const attempts = page.getByRole('list', { name: 'Attempts' }).locator(':scope > li');
      await expect(attempts).toHaveCount(2, { timeout: UI_READY });
      expect(await attempts.evaluateAll((items) => items.map((li) => li.getAttribute('data-outcome')))).toEqual([
        'error',
        'success',
      ]);
      // Each attempt's chips in DOM order: kind, outcome, billed mark, then
      // credential (attempts/AttemptsTimeline.jsx AttemptRow; explorerLabels.js).
      await expect(attempts.nth(0).locator('.MuiChip-label')).toHaveText(
        ['Primary', 'Error', 'Not billed', 'Managed'], { timeout: UI_READY });
      await expect(attempts.nth(1).locator('.MuiChip-label')).toHaveText(
        ['Auto fallback', 'Success', 'Billed', 'Managed'], { timeout: UI_READY });
      await expect(attempts.nth(0)).toContainText('openrouter / openai/gpt-6-sol');
      await expect(attempts.nth(0)).toContainText('Rate limited (upstream)');
      await expect(attempts.nth(0)).toContainText('Upstream $0.00 · Billed $0.00');
      await expect(attempts.nth(1)).toContainText('openrouter / anthropic/claude-sonnet-5.5');
      await expect(attempts.nth(1)).toContainText('Reason: Rate limited (429)');
      await expect(attempts.nth(1)).toContainText('Upstream $0.0120 · Billed $0.0126');
      await expect(page.getByText(LANDED_TEXT)).toHaveCount(0);
    }, { timeout: STEP_BUDGET });

    await context.close();
  },
);
