import { request } from '@playwright/test';
import type { Locator, Page, Request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { StackLock, STACK_LOCKS } from '../../lib/stack-lock';
import { POLL } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';

// Pinned off the source:
// - an org-confined key routed to an org provider runs a failover over the org's own
//   provider instances only, sorted by provider id when the org chose no strategy, with
//   the org's retry and its own circuit breakers keyed "org:<org>/<provider>|<model>"
//   (agentcc-gateway/internal/server/org_failover.go customerFailover, orgTargetID);
//   a push builds a new failover whose circuits start closed (orgFailovers, keyed by
//   the config pointer the tenant store holds). Any admin's Health Check re-pushes
//   every org's config (futureagi/agentcc/views/gateway.py _repush_org_configs), so
//   requests 2 and 3 run under the stack lock (lib/stack-lock.ts) that such a
//   click is taken under (GW-E2E-010). Not covered by the lock: the gateway's 60 s
//   bulk sync also stores a new pointer for every org (internal/tenant/store.go
//   MergeBulk), which closes A's circuit if it lands in the ~100 ms between
//   requests 2 and 3. Go proofs:
//   server/org_failover_test.go TestOrgFailoverNeverReachesAnOperatorProvider,
//   TestOrgBreakerOpensOnTheOrgProvider.
// - the failover loop (internal/routing/failover.go ExecuteObserved, callTarget): the
//   breaker is asked once per target before its call (an open circuit records the
//   attempt and moves on, no call), the retries run inside the target's call, and the
//   breaker records ONE result per target call. Attempt kinds: the first try on the
//   first target "primary", a later try on the same target "retry", the first try on a
//   later target "failover"; an attempt is recorded under the org's provider id
//   (orgFailover.recorded). Failover and retry on_status_codes match the upstream
//   status or the gateway's; the gateway's own status for an upstream 503 is 502
//   (server/agent_deadline_attempts_test.go TestAgentDeadlineBoundsRetryBackoff), so a [503] list can only
//   match the upstream status. The OpenAI adapter parses the mock's 503 error body
//   into Status 502 with UpstreamStatus 503 (the status lists' value) and the body's
//   own code (internal/providers/openai/openai.go parseProviderError), and an
//   attempt's status_code is the upstream's only when the code carries it
//   ("provider_<n>"), else the error's Status (routing/errclass.go AttemptStatus), so
//   A's failed attempts are recorded with 502.
// - the model map (settings/ModelMapConfigTab.jsx; server/handlers.go org routes): a
//   mapped name goes only to the mapped org provider, and is sent as that provider's
//   model when it lists exactly one.
// - agentcc_request_attempt (futureagi/agentcc/models/request_attempt.py: request_id,
//   index, kind, provider, status_code, outcome), written by log ingest
//   (services/log_ingestion.py _attempt_rows); the Request Logs page
//   (/dashboard/gateway/logs, rows "table tbody tr", newest first, column "Status") and
//   its drawer's getByRole('list', { name: 'Attempts' }) items with data-outcome
//   (logs/attempts/AttemptsTimeline.jsx; GW-E2E-004's pins), loaded from
//   GET /agentcc/request-logs/<id>/.
// - the console: Providers > Routing (/dashboard/gateway/providers/routing,
//   providers/RoutingConfigView.jsx): card "Failover Configuration" with an unlabelled
//   Switch, "Max Attempts", the status list "Failover on Status Codes"
//   ([data-testid="routing-failover-codes"], placeholder "Type code + Enter"), button
//   "Save Routing Config", wire { routing: { failover: { enabled, max_attempts,
//   on_status_codes } } }. Settings > Org Config, "Edit Config", dialog "Edit
//   Organization Config" (OrgConfigEditor.jsx), tab "Routing"
//   (settings/RoutingConfigTab.jsx): sections "Circuit Breaker" and "Retry
//   Configuration", each collapsed until its title is clicked (ConfigSection,
//   defaultOpen false), with switches "Enable Circuit Breaker" / "Enable Retries",
//   fields "Failure Threshold", "Success Threshold", "Cooldown" / "Max Retries",
//   "Initial Delay", "Max Delay", "Multiplier", and code inputs placeholder "Status code
//   (e.g. 429)" / "Status code (e.g. 429, 500)" that add on Enter; tab "Model Map":
//   placeholders "Model name (e.g. gpt-4o)" and "Provider ID (e.g. openai)", button
//   "Add". Each save is POST /agentcc/gateways/default/update-config/ with an RFC 7386
//   merge patch (hooks/useConfigPatch.js), captured with page.on('request'); Django
//   pushes routing and model_map as stored (services/config_push.py _build_payload).
// - the mock's scoped routes (e2e/stack/mock-llm/server.mjs, lib/upstream.ts):
//   status=503 answers 503; every request is recorded with its headers.
// That the organization's key never reaches the operator's provider is carried by
// the attempt rows, which name no provider outside the org's (the operator's
// node provider is the mock's default route, which the recorder does not record); an
// upstream 503 matches a [503] list by the upstream status only.
const ROUTING_PATH = '/dashboard/gateway/providers/routing';
const ORG_CONFIG_PATH = '/dashboard/gateway/settings/org-config';
const REQUEST_LOGS_PATH = '/dashboard/gateway/logs';
const REQUEST_LOGS = '/agentcc/request-logs/';
const UPDATE_CONFIG = /\/agentcc\/gateways\/[^/]+\/update-config\/$/;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// Spec-local names for what each wait waits on.
const PUSH_VISIBLE = POLL.ASYNC_JOB; // Django's push on save reaching the gateway's copy (60 s sync behind it)
const LOG_VISIBLE = POLL.ASYNC_JOB; // the gateway's log flush reaching Django's ingest (request log and attempt rows)
// Waits on another worker's hold of the re-push lock: GW-E2E-009 holds it for its
// unstamped precondition (POLL.GATEWAY_SYNC) and its Member Health Check step.
const REPUSH_LOCK_WAIT = POLL.GATEWAY_SYNC.timeout + STEP_BUDGET;
// provisioning 120 + seeding 20 + provider push precondition 60 + Routing page step 75
// + editor step 75 + #1 PUSH_VISIBLE 60 + request 1 2 + #2 LOG_VISIBLE 60 + alias call
// 2 + breaker step 75 + breaker push PUSH_VISIBLE 60 + REPUSH_LOCK_WAIT 150 + requests
// 2 and 3 3 + #3's attempt rows LOG_VISIBLE 60 + Request Logs and drawer steps 2 x 75
// + headroom 30 = 982 s, rounded up.
const FLOW_TIMEOUT = 990_000;

type Created<T> = { result: T };
type LogRow = { id: string; request_id: string };

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-016: a customer key\'s retry, failover, circuit breaker and model map run on the organization\'s own providers only',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-016',
      area: 'gateway',
      userGoal:
        'An org admin who runs two providers for a model sets failover on 503, retries and a circuit breaker, and maps an alias to a third provider, and the organization\'s API key is retried, failed over, cut off from a failing provider and routed by the alias, always on the organization\'s own providers. That the key never reaches the operator\'s provider is carried by the attempt rows, which name no provider outside the organization\'s, because the operator\'s node provider is the mock\'s default route, which the upstream recorder does not record; and an upstream 503 matches a [503] list by the upstream status only, the gateway\'s own status being 502',
      steps: [
        'seed providers A (the mock answering 503) and B (echo) serving one model, provider C (echo) serving exactly one other model, and an org key',
        'open Providers > Routing: Failover on, Max Attempts 2, Failover on Status Codes 503, Save Routing Config',
        'open Org Config, the config editor, Routing: Enable Retries, Max Retries 1, Initial Delay 1ms, Max Delay 2ms, Multiplier 1, retry on 503; Model Map: the alias to C; Save & Activate',
        'call the model (request 1), then the alias',
        'Org Config, Routing: Enable Circuit Breaker, Failure Threshold 1, Success Threshold 1, Cooldown 60s, on 503, Save & Activate',
        'call the model twice more (requests 2 and 3)',
        'open Request Logs, then request 1\'s detail',
      ],
      backendChecks: [
        'the failover, retry and model map saves reach the gateway as sent',
        'an upstream 503 on the first provider matches the [503] lists (the gateway\'s own status for it, 502, is what the attempt rows record): it is retried once, then failed over to the second provider, which answers (200; A called twice, B once; attempts A primary error 502, A retry error 502, B failover success)',
        'only the organization\'s providers are ever called, each with its own credential: no foreign (scenario, authorization) pair at the mock and no attempt naming another provider',
        'the alias is routed to the mapped provider and sent as its one model',
        'once the breaker opens on the failing provider, the next request makes no call to it: A\'s calls for requests 2 and 3 are [2, 0], B\'s [1, 1], both answered 200',
        'the three saves sent exactly the console\'s merge patches',
      ],
      browserChecks: [
        'Request Logs shows exactly the four calls, all 200',
        'request 1\'s drawer lists three attempts, data-outcome error, error, success',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    // Minted per run: A sorts before B, so with no strategy A is the primary
    // (org_failover.go); the provider keys anchor #3, the scenarios #2, #4 and #5, the
    // models and the alias are this flow's alone.
    const model = `e2e-gw16-model-${suffix}`;
    const mc = `e2e-gw16-mc-${suffix}`;
    const alias = `e2e-gw16-alias-${suffix}`;
    const providers = {
      a: { name: `e2e-gw16-a-${suffix}`, scenario: `gw16a-${suffix}`, key: `sk-e2e-gw16-a-${suffix}` },
      b: { name: `e2e-gw16-b-${suffix}`, scenario: `gw16b-${suffix}`, key: `sk-e2e-gw16-b-${suffix}` },
      c: { name: `e2e-gw16-c-${suffix}`, scenario: `gw16c-${suffix}`, key: `sk-e2e-gw16-c-${suffix}` },
    };

    const upstream = new MockUpstream();
    const created: Record<string, string> = {};
    for (const [id, p, models, opts] of [
      ['a', providers.a, [model], { status: 503 }],
      ['b', providers.b, [model], {}],
      ['c', providers.c, [mc], {}],
    ] as const) {
      const res = await owner.api.post<Created<{ id: string }>>('/agentcc/provider-credentials/', {
        provider_name: p.name, credentials: { api_key: p.key }, base_url: upstream.baseUrl(p.scenario, opts),
        api_format: 'openai', models_list: [...models], extra_config: { api_path_prefix: '/v1' },
      });
      created[id] = res.result.id;
    }
    const key = await owner.api.post<Created<{ key: string; gateway_key_id: string }>>('/agentcc/api-keys/', { name: `e2e-gw16-${suffix}` });
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, model, mc, alias, providers: Object.fromEntries(Object.entries(providers)
        .map(([id, p]) => [id, { name: p.name, scenario: p.scenario, providerId: created[id] }])),
        organizationId: owner.organizationId, keyGatewayId: key.result.gateway_key_id }, null, 2),
    });

    const admin = await GatewayAdmin.open();
    const caller = await request.newContext();
    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    const patches: Request[] = [];
    page.on('request', (r) => {
      if (r.method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname)) patches.push(r);
    });
    const calls: { name: string; model: string; status: number; requestId: string }[] = [];
    const callModel = async (name: string, m: string) => {
      const res = await caller.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
        headers: { Authorization: `Bearer ${key.result.key}` },
        data: { model: m, messages: [{ role: 'user', content: `e2e-gw16-${suffix}-${name}` }] },
      });
      calls.push({ name, model: m, status: res.status(), requestId: res.headers()['x-agentcc-request-id'] ?? '' });
      return { status: res.status(), requestId: res.headers()['x-agentcc-request-id'] ?? '' };
    };
    const counts = async () => ({
      a: (await upstream.requests(providers.a.scenario)).length,
      b: (await upstream.requests(providers.b.scenario)).length,
    });
    const attemptRows = async (requestId: string) => (await probe.pg<{ provider: string; kind: string; outcome: string; status_code: number | null }>(
      `SELECT provider, kind, outcome, status_code FROM agentcc_request_attempt
        WHERE organization_id = $1 AND request_id = $2 ORDER BY index`, [owner.organizationId, requestId]))
      .map((r) => [r.provider, r.kind, r.outcome, r.outcome === 'error' ? r.status_code : '-']);
    // #3, one helper asserted after every call: what the mock recorded for the three
    // scenarios, and every attempt row of the org, name only the org's own providers.
    const minted = new Set(Object.values(providers).map((p) => `${p.scenario} Bearer ${p.key}`));
    const ours = new Set(Object.values(providers).map((p) => p.name));
    const onlyOrgProviders = async () => {
      const pairs: string[] = [];
      for (const p of Object.values(providers)) {
        for (const r of await upstream.requests(p.scenario)) pairs.push(`${p.scenario} ${r.headers.authorization}`);
      }
      const attempted = (await probe.pg<{ provider: string }>(
        'SELECT DISTINCT provider FROM agentcc_request_attempt WHERE organization_id = $1', [owner.organizationId])).map((r) => r.provider);
      expect({ foreignCalls: pairs.filter((x) => !minted.has(x)), foreignAttemptProviders: attempted.filter((x) => !ours.has(x)) })
        .toEqual({ foreignCalls: [], foreignAttemptProviders: [] });
    };
    const openEditorTab = async (p: Page, tab: string): Promise<Locator> => {
      await p.goto(ORG_CONFIG_PATH, { waitUntil: 'domcontentloaded' });
      await p.getByRole('button', { name: /^(Edit Config|Create Custom Config)$/ }).click({ timeout: UI_READY });
      const dialog = p.getByRole('dialog', { name: 'Edit Organization Config' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('tab', { name: tab, exact: true }).click();
      return dialog;
    };
    const saveAndWait = async (p: Page, dialog: Locator) => {
      const saved = p.waitForResponse(
        (r) => r.request().method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname), { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Save & Activate', exact: true }).click();
      expect((await saved).status()).toBe(200);
      await expect(dialog).toBeHidden({ timeout: UI_READY });
    };
    const addCode = async (input: Locator, code: string) => {
      await input.fill(code);
      await input.press('Enter');
    };
    let repush: StackLock | undefined;

    try {
      await test.step('gateway: the three org providers are pushed (precondition)', async () => {
        await expect.poll(async () => Object.keys((await admin.orgConfig(owner.organizationId))?.providers ?? {}),
          PUSH_VISIBLE).toEqual(expect.arrayContaining(Object.values(providers).map((p) => p.name)));
      });

      await test.step('UI: Providers > Routing, Failover on, Max Attempts 2, on 503, Save Routing Config', async () => {
        await page.goto(ROUTING_PATH, { waitUntil: 'domcontentloaded' });
        const card = page.locator('.MuiCard-root').filter({ has: page.getByRole('heading', { name: 'Failover Configuration', exact: true }) });
        await card.getByRole('checkbox').check({ timeout: UI_READY });
        await card.getByLabel('Max Attempts', { exact: true }).fill('2');
        await addCode(page.locator('[data-testid="routing-failover-codes"]').getByPlaceholder('Type code + Enter', { exact: true }), '503');
        await expect(page.locator('[data-testid="routing-failover-codes"] .MuiChip-label')).toHaveText(['503'], { timeout: UI_READY });
        const saved = page.waitForResponse(
          (r) => r.request().method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname), { timeout: UI_READY });
        await page.getByRole('button', { name: 'Save Routing Config', exact: true }).click();
        expect((await saved).status()).toBe(200);
      }, { timeout: STEP_BUDGET });

      await test.step('UI: Org Config, Routing retries on 503 and the alias to C in Model Map, Save & Activate', async () => {
        const dialog = await openEditorTab(page, 'Routing');
        await dialog.getByText('Retry Configuration', { exact: true }).click();
        await dialog.getByLabel('Enable Retries', { exact: true }).check();
        await dialog.getByLabel('Max Retries', { exact: true }).fill('1');
        await dialog.getByLabel('Initial Delay', { exact: true }).fill('1ms');
        await dialog.getByLabel('Max Delay', { exact: true }).fill('2ms');
        await dialog.getByLabel('Multiplier', { exact: true }).fill('1');
        await addCode(dialog.getByPlaceholder('Status code (e.g. 429, 500)', { exact: true }), '503');
        await dialog.getByRole('tab', { name: 'Model Map', exact: true }).click();
        await dialog.getByPlaceholder('Model name (e.g. gpt-4o)', { exact: true }).fill(alias);
        await dialog.getByPlaceholder('Provider ID (e.g. openai)', { exact: true }).fill(providers.c.name);
        await dialog.getByRole('button', { name: 'Add', exact: true }).click();
        await expect(dialog.getByText(alias, { exact: true })).toBeVisible({ timeout: UI_READY });
        await saveAndWait(page, dialog);
      }, { timeout: STEP_BUDGET });

      await test.step('gateway: the failover, retry and model map saves reach the gateway as sent', async () => {
        const [routingSave, editorSave] = patches.map((r) => r.postDataJSON());
        const sent = { failover: routingSave?.routing?.failover ?? null, retry: editorSave?.routing?.retry ?? null,
          model_map: editorSave?.model_map ?? null };
        await testInfo.attach('sent', { contentType: 'application/json', body: JSON.stringify(sent) });
        await expect.poll(async () => {
          const cfg = await admin.orgConfig(owner.organizationId);
          return { failover: cfg?.routing?.failover ?? null, retry: cfg?.routing?.retry ?? null, model_map: cfg?.model_map ?? null };
        }, PUSH_VISIBLE).toEqual(sent);
      });

      let before = await counts();
      let first = { status: 0, requestId: '' };
      await test.step('gateway + upstream + storage: request 1 is retried on A once, then failed over to B', async () => {
        first = await callModel('r1', model);
        const after = await counts();
        const delta = { a: after.a - before.a, b: after.b - before.b };
        before = after;
        await expect.poll(async () => ({ status: first.status, calls: delta, attempts: await attemptRows(first.requestId) }), LOG_VISIBLE)
          .toEqual({ status: 200, calls: { a: 2, b: 1 }, attempts: [
            [providers.a.name, 'primary', 'error', 502],
            [providers.a.name, 'retry', 'error', 502],
            [providers.b.name, 'failover', 'success', '-'],
          ] });
      });
      await test.step('upstream + storage: only the org\'s providers were called (after request 1)', onlyOrgProviders);

      await test.step('gateway + upstream: the alias goes to C, sent as C\'s one model', async () => {
        const res = await callModel('alias', alias);
        const sent = (await upstream.requests(providers.c.scenario)).map((r) => ({ model: (r.body as { model?: string }).model }));
        expect({ status: res.status, sent }).toEqual({ status: 200, sent: [{ model: mc }] });
      });
      await test.step('upstream + storage: only the org\'s providers were called (after the alias)', onlyOrgProviders);
      before = await counts();

      await test.step('UI: Org Config, Routing, Circuit Breaker on, threshold 1, cooldown 60s, on 503, Save & Activate', async () => {
        const dialog = await openEditorTab(page, 'Routing');
        await dialog.getByText('Circuit Breaker', { exact: true }).click();
        await dialog.getByLabel('Enable Circuit Breaker', { exact: true }).check();
        await dialog.getByLabel('Failure Threshold', { exact: true }).fill('1');
        await dialog.getByLabel('Success Threshold', { exact: true }).fill('1');
        await dialog.getByLabel('Cooldown', { exact: true }).fill('60s');
        await addCode(dialog.getByPlaceholder('Status code (e.g. 429)', { exact: true }), '503');
        await saveAndWait(page, dialog);
      }, { timeout: STEP_BUDGET });

      await test.step('gateway: the breaker reaches the gateway (precondition)', async () => {
        const sent = patches.at(-1)?.postDataJSON()?.routing?.circuit_breaker ?? null;
        await expect.poll(async () => (await admin.orgConfig(owner.organizationId))?.routing?.circuit_breaker ?? null,
          PUSH_VISIBLE).toEqual(sent);
      });

      await test.step('gateway + upstream: once the breaker opens on A, the next request makes no call to A', async () => {
        // The stack lock: no other flow's admin Health Check may re-push this org's config (and so
        // close A's circuit) between requests 2 and 3.
        repush = await StackLock.acquire(STACK_LOCKS.ORG_CONFIG_REPUSH, { timeoutMs: REPUSH_LOCK_WAIT });
        await testInfo.attach('repush-lock', { contentType: 'application/json', body: JSON.stringify({ waitedMs: repush.waitedMs }) });
        const perRequest: { a: number; b: number; status: number }[] = [];
        for (const name of ['r2', 'r3']) {
          const res = await callModel(name, model);
          const after = await counts();
          perRequest.push({ a: after.a - before.a, b: after.b - before.b, status: res.status });
          before = after;
        }
        await repush.release();
        expect({ aCalls: perRequest.map((r) => r.a), bCalls: perRequest.map((r) => r.b), statuses: perRequest.map((r) => r.status) })
          .toEqual({ aCalls: [2, 0], bCalls: [1, 1], statuses: [200, 200] });
      }, { timeout: REPUSH_LOCK_WAIT + 15_000 });
      await testInfo.attach('calls', { contentType: 'application/json', body: JSON.stringify(calls, null, 2) });

      await test.step('upstream + storage: only the org\'s providers were called, over every attempt row', async () => {
        // Precondition: every call's attempt rows have been ingested.
        await expect.poll(async () => (await probe.pg<{ n: number }>(
          'SELECT count(DISTINCT request_id)::int AS n FROM agentcc_request_attempt WHERE organization_id = $1',
          [owner.organizationId]))[0]?.n ?? 0, LOG_VISIBLE).toBe(calls.length);
        await onlyOrgProviders();
      });

      await test.step('UI wire: the three saves sent exactly these merge patches', async () => {
        expect(patches.map((r) => r.postDataJSON())).toEqual([
          { routing: { failover: { enabled: true, max_attempts: 2, on_status_codes: [503] } } },
          { routing: { retry: { enabled: true, max_retries: 1, initial_delay: '1ms', max_delay: '2ms', multiplier: 1, on_status_codes: [503] } },
            model_map: { [alias]: providers.c.name } },
          { routing: { circuit_breaker: { enabled: true, failure_threshold: 1, success_threshold: 1, cooldown: '60s', on_status_codes: [503] } } },
        ]);
      });

      const rows = page.locator('table tbody tr');
      await test.step('UI: Request Logs shows the four calls, all 200', async () => {
        await page.goto(REQUEST_LOGS_PATH, { waitUntil: 'domcontentloaded' });
        await expect(rows).toHaveCount(4, { timeout: UI_READY });
        const headerTexts = await page.locator('table thead th').allInnerTexts();
        await expect(rows.locator(`td:nth-child(${headerTexts.indexOf('Status') + 1})`)).toHaveText(['200', '200', '200', '200'], { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('UI: request 1\'s drawer lists its three attempts, error, error, success', async () => {
        const logs = (await owner.api.get<{ results: LogRow[] }>(REQUEST_LOGS, { ordering: '-started_at' })).results;
        const row1 = logs.find((r) => r.request_id === first.requestId);
        expect(row1).toBeDefined();
        const detailLoaded = page.waitForResponse((r) => r.url().includes(`${REQUEST_LOGS}${row1!.id}/`) && r.ok(), { timeout: UI_READY });
        // Newest first: request 1 is the oldest, the last row.
        await rows.nth(logs.indexOf(row1!)).click();
        await detailLoaded;
        const attempts = page.getByRole('list', { name: 'Attempts' }).locator(':scope > li');
        await expect(attempts).toHaveCount(3, { timeout: UI_READY });
        expect(await attempts.evaluateAll((items) => items.map((li) => li.getAttribute('data-outcome')))).toEqual(['error', 'error', 'success']);
      }, { timeout: STEP_BUDGET });
    } finally {
      await admin.dispose();
      await caller.dispose();
      await context.close();
      await repush?.release();
    }
  },
);
