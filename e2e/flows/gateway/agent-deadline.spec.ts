import { request } from '@playwright/test';
import type { Locator, Page, Request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { POLL } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';

// Pinned off the source:
// - the header x-agentcc-deadline-ms (internal/models/agentctx_validate.go
//   AgentFieldSuffixes "deadline-ms", an integer 1..3600000, else refused).
// - internal/server/attempt_budget.go: the deadline is the request's start plus
//   deadline-ms; each attempt's context carries it (attemptContext), the waits between
//   attempts end at it (chat_nonstream.go / chat_stream.go), no attempt starts
//   at or after it (attemptRefusal). attemptLimitError answers an attempt the deadline
//   cut with 504, error.type and error.code "gateway_timeout", and the header
//   x-agentcc-deadline-exceeded: true. For an OpenAI-format provider the cut is the
//   adapter's own 504 gateway_timeout "provider request timed out"
//   (internal/providers/openai/openai.go), which attemptLimitError passes on as it
//   is, so the answer carries no error.metadata.deadline_ms (only errDeadlineCut,
//   for a bare context error, adds it).
//   A run whose retry the deadline refused is
//   answered with the last attempt's own error (an upstream 503 is the gateway's 502,
//   server/agent_deadline_attempts_test.go TestAgentDeadlineBoundsRetryBackoff). 504 deadline_exceeded is
//   only for a request no attempt could start for.
// - an org-confined key with retry configured runs the org's failover over its own
//   providers (server/org_failover.go customerFailover), with the org's retryer
//   (routing/retry.go: full jitter, a wait drawn in [0, delay)).
// - the console: Settings > Org Config (GatewaySettingsSection.jsx TAB_SLUGS
//   "org-config"), "Create Custom Config" | "Edit Config" (OrgConfigSection.jsx),
//   dialog "Edit Organization Config", tab "Routing", "Save & Activate"
//   (OrgConfigEditor.jsx); the tab's "Retry Configuration" section, collapsed until
//   its title is clicked (settings/RoutingConfigTab.jsx ConfigSection, defaultOpen
//   false): switch "Enable Retries" (its fields render only
//   while it is on), "Max Retries" and "Multiplier" (Number), "Initial Delay" and "Max
//   Delay" (duration strings), a status code input placeholder "Status code (e.g. 429,
//   500)" that adds the code on Enter (routing.retry.on_status_codes).
// - the wire: POST /agentcc/gateways/default/update-config/ with an RFC 7386 merge
//   patch (hooks/useConfigPatch.js), captured with page.on('request'); Django pushes
//   routing as stored (services/config_push.py _build_payload); the gateway's copy
//   (GET /-/orgs/{org}/config) marshals routing.retry as internal/tenant/config.go
//   RetryConfig (durations kept as strings).
// - the gateway's own record: agentcc_request_log.latency_ms by request_id
//   (models/request_log.py, log_ingestion.py); the Request Logs page
//   (/dashboard/gateway/logs, rows "table tbody tr", newest first, column "Status").
// - the mock's scoped routes (e2e/stack/mock-llm/server.mjs, lib/upstream.ts):
//   lat=<ms> answers after that delay, status=<code> answers that status; each request
//   is recorded with its arrival time on the mock's clock ("at").
// The answer for a deadline that cuts a running attempt is the source's 504
// gateway_timeout with x-agentcc-deadline-exceeded: true.
const ORG_CONFIG_PATH = '/dashboard/gateway/settings/org-config';
const REQUEST_LOGS_PATH = '/dashboard/gateway/logs';
const UPDATE_CONFIG = /\/agentcc\/gateways\/[^/]+\/update-config\/$/;
const DEADLINE_HEADER = 'x-agentcc-deadline-ms';
// The deadline the first call sends (#2, #3).
const DEADLINE_MS = 50;
const SLOW_UPSTREAM_MS = 400;
const RETRY_DEADLINE_MS = 150;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// Spec-local names for what each wait waits on.
const PUSH_VISIBLE = POLL.ASYNC_JOB; // Django's push on save reaching the gateway's copy (60 s sync behind it)
const LOG_VISIBLE = POLL.ASYNC_JOB; // the gateway's log flush reaching Django's ingest
// provisioning 120 + seeding 15 + provider push precondition 60 + editor step 75 +
// #1 PUSH_VISIBLE 60 + two calls 2 + #3 LOG_VISIBLE 60 + both rows ingested
// LOG_VISIBLE 60 + Request Logs step 75 + headroom 30 = 557 s, rounded up.
const FLOW_TIMEOUT = 600_000;

type Created<T> = { result: T };
type GatewayError = { error?: { code?: string; type?: string; message?: string } };

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-015: an agent\'s deadline ends the call with the gateway\'s timeout answer by the deadline, when the provider is slower and when the organization\'s retry would wait past it',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-015',
      area: 'gateway',
      userGoal:
        'An agent developer gives a call a deadline and gets the gateway\'s timeout answer by that deadline, both when the organization\'s provider is slower than the deadline and when the organization\'s retry would wait past it. The answer for a deadline that cuts a running attempt is 504 gateway_timeout with x-agentcc-deadline-exceeded: true.',
      steps: [
        'seed two org providers on the mock, one answering after 400 ms for model 1 and one answering 503 for model 2, and an org key',
        'open Org Config, open the config editor, Routing: Enable Retries, Max Retries 1, Initial Delay 10s, Max Delay 10s, Multiplier 1, retry on 503, Save & Activate',
        'call model 1 with x-agentcc-deadline-ms 50',
        'call model 2 with x-agentcc-deadline-ms 150',
        'open Request Logs',
      ],
      backendChecks: [
        'the retry settings reach the gateway as saved',
        'with a 50 ms deadline against a 400 ms provider the gateway answers 504 gateway_timeout with x-agentcc-deadline-exceeded: true, before the provider would have answered',
        'the gateway\'s own record of that request ends within about 100 ms of its start (latency_ms under 150)',
        'a retry whose backoff would outlive a 150 ms deadline never holds the answer past it (answered as the gateway\'s 502 within 500 ms), and no provider call is sent past it',
      ],
      browserChecks: [
        'Request Logs shows exactly the two calls, Status newest first 502 504',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    // Minted per run: each scenario's recorder holds only this flow's calls (#4); the
    // models route each call to its own provider.
    const slow = { scenario: `gw15s-${suffix}`, name: `e2e-gw15s-${suffix}`, model: `e2e-gw15-m1-${suffix}` };
    const down = { scenario: `gw15d-${suffix}`, name: `e2e-gw15d-${suffix}`, model: `e2e-gw15-m2-${suffix}` };

    const upstream = new MockUpstream();
    const slowProvider = await owner.api.post<Created<{ id: string }>>('/agentcc/provider-credentials/', {
      provider_name: slow.name, credentials: { api_key: `sk-e2e-gw15s-${suffix}` },
      base_url: upstream.baseUrl(slow.scenario, { latencyMs: SLOW_UPSTREAM_MS }),
      api_format: 'openai', models_list: [slow.model], extra_config: { api_path_prefix: '/v1' },
    });
    const downProvider = await owner.api.post<Created<{ id: string }>>('/agentcc/provider-credentials/', {
      provider_name: down.name, credentials: { api_key: `sk-e2e-gw15d-${suffix}` },
      base_url: upstream.baseUrl(down.scenario, { status: 503 }),
      api_format: 'openai', models_list: [down.model], extra_config: { api_path_prefix: '/v1' },
    });
    const key = await owner.api.post<Created<{ key: string; gateway_key_id: string }>>('/agentcc/api-keys/', { name: `e2e-gw15-${suffix}` });
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, slow, down, organizationId: owner.organizationId,
        slowProviderId: slowProvider.result.id, downProviderId: downProvider.result.id,
        keyGatewayId: key.result.gateway_key_id }, null, 2),
    });

    const admin = await GatewayAdmin.open();
    const caller = await request.newContext();
    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    const patches: Request[] = [];
    page.on('request', (r) => {
      if (r.method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname)) patches.push(r);
    });
    const calls: { model: string; deadlineMs: string; status: number; elapsedMs: number; requestId: string }[] = [];
    const agentCall = async (model: string, deadlineMs: number, n: number) => {
      const started = Date.now();
      const res = await caller.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
        headers: { Authorization: `Bearer ${key.result.key}`, [DEADLINE_HEADER]: String(deadlineMs) },
        data: { model, messages: [{ role: 'user', content: `e2e-gw15-${suffix}-${n}` }] },
      });
      const elapsedMs = Date.now() - started;
      calls.push({ model, deadlineMs: String(deadlineMs), status: res.status(), elapsedMs,
        requestId: res.headers()['x-agentcc-request-id'] ?? '' });
      const body = (await res.json().catch(() => ({}))) as GatewayError;
      return { status: res.status(), elapsedMs, header: res.headers()['x-agentcc-deadline-exceeded'] ?? null,
        requestId: res.headers()['x-agentcc-request-id'] ?? '', body };
    };
    const openRouting = async (p: Page): Promise<Locator> => {
      await p.goto(ORG_CONFIG_PATH, { waitUntil: 'domcontentloaded' });
      await p.getByRole('button', { name: /^(Edit Config|Create Custom Config)$/ }).click({ timeout: UI_READY });
      const dialog = p.getByRole('dialog', { name: 'Edit Organization Config' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('tab', { name: 'Routing', exact: true }).click();
      return dialog;
    };
    const saveAndWait = async (p: Page, dialog: Locator) => {
      const saved = p.waitForResponse(
        (r) => r.request().method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname), { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Save & Activate', exact: true }).click();
      expect((await saved).status()).toBe(200);
      await expect(dialog).toBeHidden({ timeout: UI_READY });
    };

    try {
      await test.step('gateway: both org providers are pushed (precondition)', async () => {
        await expect.poll(async () => Object.keys((await admin.orgConfig(owner.organizationId))?.providers ?? {}).sort(),
          PUSH_VISIBLE).toEqual(expect.arrayContaining([slow.name, down.name]));
      });

      await test.step('UI: Routing, Retry Configuration on, 1 retry, 10s backoff, on 503, Save & Activate', async () => {
        const dialog = await openRouting(page);
        await dialog.getByText('Retry Configuration', { exact: true }).click();
        await dialog.getByLabel('Enable Retries', { exact: true }).check();
        await dialog.getByLabel('Max Retries', { exact: true }).fill('1');
        await dialog.getByLabel('Initial Delay', { exact: true }).fill('10s');
        await dialog.getByLabel('Max Delay', { exact: true }).fill('10s');
        await dialog.getByLabel('Multiplier', { exact: true }).fill('1');
        const code = dialog.getByPlaceholder('Status code (e.g. 429, 500)', { exact: true });
        await code.fill('503');
        await code.press('Enter');
        await saveAndWait(page, dialog);
      }, { timeout: STEP_BUDGET });

      await test.step('gateway: the retry settings reach the gateway\'s copy as saved', async () => {
        const sent = patches.at(-1)?.postDataJSON()?.routing?.retry ?? null;
        await testInfo.attach('sent-retry', { contentType: 'application/json', body: JSON.stringify(sent) });
        await expect.poll(async () => (await admin.orgConfig(owner.organizationId))?.routing?.retry ?? null,
          PUSH_VISIBLE).toEqual(sent);
      });

      let cutId = '';
      await test.step('gateway: a 50 ms deadline against a 400 ms provider is answered 504 gateway_timeout before the provider answers', async () => {
        const r = await agentCall(slow.model, DEADLINE_MS, 1);
        cutId = r.requestId;
        await testInfo.attach('cut-answer', { contentType: 'application/json', body: JSON.stringify({ elapsedMs: r.elapsedMs, error: r.body.error ?? null }) });
        expect({ status: r.status, code: r.body.error?.code, header: r.header, beforeUpstream: r.elapsedMs < SLOW_UPSTREAM_MS })
          .toEqual({ status: 504, code: 'gateway_timeout', header: 'true', beforeUpstream: true });
      });

      await test.step('storage: the gateway\'s record of that request ends within about 100 ms of its start', async () => {
        // Precondition: the row has been ingested.
        const latency = async () => (await probe.pg<{ latency_ms: number }>(
          'SELECT latency_ms FROM agentcc_request_log WHERE organization_id = $1 AND request_id = $2',
          [owner.organizationId, cutId]))[0]?.latency_ms ?? null;
        await expect.poll(latency, LOG_VISIBLE).not.toBeNull();
        const latencyMs = await latency();
        await testInfo.attach('cut-latency', { contentType: 'application/json', body: JSON.stringify({ requestId: cutId, latencyMs }) });
        expect({ latencyMs, within150: (latencyMs ?? Infinity) < 150 }).toEqual({ latencyMs, within150: true });
      });

      await test.step('gateway + upstream: a retry whose backoff outlives a 150 ms deadline never holds the answer past it', async () => {
        const r = await agentCall(down.model, RETRY_DEADLINE_MS, 2);
        const seen = await upstream.requests(down.scenario);
        const first = seen.length ? Date.parse(seen[0].at) : NaN;
        await testInfo.attach('retry-calls', { contentType: 'application/json', body: JSON.stringify({ elapsedMs: r.elapsedMs, status: r.status, header: r.header, at: seen.map((s) => s.at) }) });
        expect({ status: r.status, answeredInTime: r.elapsedMs < 500,
          callsWithinDeadline: seen.length >= 1 && seen.every((s) => Date.parse(s.at) - first <= RETRY_DEADLINE_MS) })
          .toEqual({ status: 502, answeredInTime: true, callsWithinDeadline: true });
      });
      await testInfo.attach('calls', { contentType: 'application/json', body: JSON.stringify(calls, null, 2) });

      await test.step('storage: both calls\' rows have been ingested (precondition: the page loads its rows once, no refetch)', async () => {
        await expect.poll(async () => (await probe.pg<{ request_id: string }>(
          'SELECT request_id FROM agentcc_request_log WHERE organization_id = $1 AND request_id = ANY($2::text[])',
          [owner.organizationId, calls.map((c) => c.requestId)])).length, LOG_VISIBLE).toBe(calls.length);
      });

      await test.step('UI: Request Logs shows the two calls, newest first 502 then 504', async () => {
        await page.goto(REQUEST_LOGS_PATH, { waitUntil: 'domcontentloaded' });
        const rows = page.locator('table tbody tr');
        await expect(rows).toHaveCount(2, { timeout: UI_READY });
        const headerTexts = await page.locator('table thead th').allInnerTexts();
        const statusCells = rows.locator(`td:nth-child(${headerTexts.indexOf('Status') + 1})`);
        await expect(statusCells).toHaveText(['502', '504'], { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });
    } finally {
      await admin.dispose();
      await caller.dispose();
      await context.close();
    }
  },
);
