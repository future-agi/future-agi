import { request } from '@playwright/test';
import type { Locator, Page, Request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { freshWindow } from '../../lib/clock';
import { E2E } from '../../lib/env';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { POLL } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';

// Pinned off the source:
// - the console: Settings > Org Config (GatewaySettingsSection.jsx TAB_SLUGS
//   "org-config"), "Create Custom Config" | "Edit Config" (OrgConfigSection.jsx),
//   dialog "Edit Organization Config", tab "Rate Limiting", "Save & Activate"
//   (OrgConfigEditor.jsx); the tab (settings/RateLimitingConfigTab.jsx): switch
//   "Enable per-org rate limiting" (its fields render only while it is on), number
//   fields "Requests per Minute (RPM)" (global_rpm), "TPM per Key" (per_key_tpm),
//   "RPM per Model" (per_model_rpm), each saved as Number(v) || 0, so a cleared field
//   saves 0; the enforcement line "This gateway enforces these limits." while the node lists
//   rate_limiting in org_sections_enforced (stock: it does).
// - the wire: POST /agentcc/gateways/default/update-config/ with an RFC 7386 merge
//   patch of the sections that changed (hooks/useConfigPatch.js buildConfigPatch,
//   mergePatch: a section absent before is sent whole); captured with
//   page.on('request').
// - the gateway's copy (GET /-/orgs/{org}/config, server/admin_orgs.go): rate_limiting
//   as internal/tenant/config.go RateLimitConfig marshals it, every limit omitempty,
//   so a limit saved as 0 is absent there.
// - the refusal (internal/plugins/ratelimit/orglimits.go processOrg, ratelimit.go
//   rateLimitExceeded, models/errors.go WriteError): 429, error.code
//   "rate_limit_exceeded", error.param the field (global_rpm, per_model_rpm,
//   per_key_tpm), message "Rate limit exceeded: <what> (<field> = <n>). Please retry
//   after <s> seconds.", header Retry-After; limits count per minute, so each group of
//   calls starts with at least 25 s left in the gateway's own minute (lib/clock.ts
//   freshWindow, read from its Date header) and runs in well under a second.
// - per_key_tpm arithmetic (orglimits.go processOrg + estimateTokens,
//   internal/tokenizer EstimateTokens, plugins/ratelimit/limiter.go AllowN and
//   Settle): the limiter admits a request while its window's count is under the limit
//   (count >= limit refuses; the request's own estimate is not added first), then
//   holds the estimate, 3 + 1 (role "user") + 6 (the one-word prompt
//   e2e-gw11-<suffix>-<n>, 21 to 24 characters) + 3 + max_tokens 5 = 18 tokens, and
//   settles it to the mock's 7 + 7 = 14 (e2e/stack/mock-llm usage pin). With TPM per
//   Key 10 a key's first call is admitted (0 < 10), its second refused (14, or 18 if
//   not yet settled, >= 10), the other key's first admitted (0 < 10). A call is
//   refused only once the count has reached the limit, whatever its own estimate
//   adds. Each counter's window starts at its first use and lasts a
//   minute (limiter.go window); each group of calls starts with at least 25 s left in
//   the gateway's minute (freshWindow), well inside one window.
// - a pre-plugin refusal still runs the post plugins (pipeline/engine.go), so every
//   429 is a request log row; GET /agentcc/request-logs/ ({count, results}; ordering
//   started_at allowed, views/request_log.py) and the Request Logs page
//   (/dashboard/gateway/logs, rows "table tbody tr", newest first: the page asks for
//   -started_at, logs/hooks/useRequestLogs.js), column "Status" (GW-E2E-007's pins).
// - wire bodies: AgentccProviderCredentialCreateSerializer (GW-E2E-002, harness/mock-upstream.spec.ts), POST /agentcc/api-keys/ { name } -> result.key, result.gateway_key_id
//   (GW-E2E-007).
const ORG_CONFIG_PATH = '/dashboard/gateway/settings/org-config';
const REQUEST_LOGS_PATH = '/dashboard/gateway/logs';
const REQUEST_LOGS = '/agentcc/request-logs/';
const UPDATE_CONFIG = /\/agentcc\/gateways\/[^/]+\/update-config\/$/;
const RATE_LIMIT_ENFORCED = 'This gateway enforces these limits.';
const LIMIT_FIELD = /\(([a-z_.]+) = \d+\)/;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// Spec-local names for what each wait waits on.
const PUSH_VISIBLE = POLL.ASYNC_JOB; // Django's push on save reaching the gateway's copy (60 s sync behind it)
const LOG_VISIBLE = POLL.ASYNC_JOB; // the gateway's log flush reaching Django's ingest
// provisioning 120 + seeding 15 + three save steps 3 x STEP_BUDGET 75 (225) + three
// PUSH_VISIBLE polls 3 x 60 (180) + three fresh-window waits at most 3 x 30 (90) +
// eight calls 10 + #6 LOG_VISIBLE 60 + Request Logs step 75 + headroom 30 = 805 s,
// rounded up.
const FLOW_TIMEOUT = 840_000;

type Created<T> = { result: T };
type Outcome = { status: number; code?: string; param?: string; retryAfter?: boolean };
type LogRow = { status_code: number; model: string; error_message: string };

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-011: org rate limits set in the console refuse the first request past each limit, across the organization\'s keys, naming the limit',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-011',
      area: 'gateway',
      userGoal:
        'An org admin sets organization-wide, per-model and per-key token rate limits in the console, and the gateway refuses the first request past each limit, counting every key of the organization together where the limit says so, and names the limit that refused it',
      steps: [
        'seed the org\'s own provider on the mock serving three models, and two org keys A and B',
        'open Org Config, open the config editor, Rate Limiting: switch on per-org rate limiting, set Requests per Minute (RPM) to 1, Save & Activate',
        'in a fresh minute of the gateway\'s clock, call model 1 with key A, then with key B',
        'Rate Limiting: clear RPM, set RPM per Model to 1, save; in a fresh minute call model 2 with A, model 2 with B, model 3 with B',
        'Rate Limiting: clear RPM per Model, set TPM per Key to 10, save; in a fresh minute call model 1 with A twice and with B once, each with max_tokens 5',
        'open Request Logs',
      ],
      backendChecks: [
        'each save reaches the gateway: before each pair the gateway\'s copy of the org config carries the rate limits that save sent',
        'with global_rpm 1 the org\'s second request in the minute is refused even from another key: A 200, B 429 rate_limit_exceeded naming global_rpm, with Retry-After',
        'with per_model_rpm 1 a model\'s second request is refused from any key while another model passes: model 2 by A 200, model 2 by B 429 naming per_model_rpm, model 3 by B 200',
        'with per_key_tpm 10 a key\'s request once its tokens this minute reached the limit is refused while another key passes: A 200, A 429 naming per_key_tpm, B 200',
        'the three saves sent exactly these merge patches: rate limiting on with global_rpm 1; global_rpm 0 and per_model_rpm 1; per_model_rpm 0 and per_key_tpm 10',
        'the org\'s request log holds every call in order, the three refusals as 429 rows whose message names global_rpm, per_model_rpm and per_key_tpm',
      ],
      browserChecks: [
        'the Rate Limiting tab shows "This gateway enforces these limits." on the stock node',
        'Request Logs shows exactly the eight calls, Status newest first 200 429 200 200 429 200 429 200',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    // Minted per run: the models are this flow's alone (a per-model counter is keyed
    // by the model name; #3, #6), the scenario its upstream; the keys anchor #2-#4 by
    // call order, the fresh org makes #6 an exact list.
    const scenario = `gw11-${suffix}`;
    const providerName = `e2e-gw11-${suffix}`;
    const [m1, m2, m3] = [1, 2, 3].map((i) => `e2e-gw11-m${i}-${suffix}`);

    const upstream = new MockUpstream();
    const provider = await owner.api.post<Created<{ id: string }>>('/agentcc/provider-credentials/', {
      provider_name: providerName, credentials: { api_key: `sk-e2e-gw11-${suffix}` }, base_url: upstream.baseUrl(scenario),
      api_format: 'openai', models_list: [m1, m2, m3], extra_config: { api_path_prefix: '/v1' },
    });
    const keyA = await owner.api.post<Created<{ key: string; gateway_key_id: string }>>('/agentcc/api-keys/', { name: `e2e-gw11-${suffix}-a` });
    const keyB = await owner.api.post<Created<{ key: string; gateway_key_id: string }>>('/agentcc/api-keys/', { name: `e2e-gw11-${suffix}-b` });
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, scenario, models: [m1, m2, m3], organizationId: owner.organizationId,
        providerId: provider.result.id, keyAGatewayId: keyA.result.gateway_key_id,
        keyBGatewayId: keyB.result.gateway_key_id }, null, 2),
    });

    const admin = await GatewayAdmin.open();
    const caller = await request.newContext();
    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    const patches: Request[] = [];
    page.on('request', (r) => {
      if (r.method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname)) patches.push(r);
    });
    const calls: { key: string; model: string; n: number; status: number; requestId: string }[] = [];
    let n = 0;
    const call = async (key: 'A' | 'B', model: string, maxTokens?: number): Promise<Outcome> => {
      n += 1;
      const res = await caller.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
        headers: { Authorization: `Bearer ${(key === 'A' ? keyA : keyB).result.key}` },
        data: { model, messages: [{ role: 'user', content: `e2e-gw11-${suffix}-${n}` }],
          ...(maxTokens === undefined ? {} : { max_tokens: maxTokens }) },
      });
      calls.push({ key, model, n, status: res.status(), requestId: res.headers()['x-agentcc-request-id'] ?? '' });
      if (res.status() === 200) return { status: 200 };
      const body = (await res.json().catch(() => ({}))) as { error?: { code?: string; param?: string } };
      return { status: res.status(), code: body.error?.code, param: body.error?.param,
        retryAfter: Number(res.headers()['retry-after']) > 0 };
    };
    const openRateLimiting = async (p: Page): Promise<Locator> => {
      await p.goto(ORG_CONFIG_PATH, { waitUntil: 'domcontentloaded' });
      await p.getByRole('button', { name: /^(Edit Config|Create Custom Config)$/ }).click({ timeout: UI_READY });
      const dialog = p.getByRole('dialog', { name: 'Edit Organization Config' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('tab', { name: 'Rate Limiting', exact: true }).click();
      return dialog;
    };
    const saveAndWait = async (p: Page, dialog: Locator) => {
      const saved = p.waitForResponse(
        (r) => r.request().method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname), { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Save & Activate', exact: true }).click();
      expect((await saved).status()).toBe(200);
      await expect(dialog).toBeHidden({ timeout: UI_READY });
    };
    // #1, one helper for the three saves: the gateway's copy carries every limit saved
    // so far (the captured patches merged in order; a limit saved as 0 is omitted there).
    let saved: Record<string, unknown> = {};
    const saveReachedGateway = async () => {
      saved = { ...saved, ...(patches.at(-1)?.postDataJSON()?.rate_limiting ?? {}) };
      const want = Object.fromEntries(Object.entries(saved).filter(([k, v]) => k === 'enabled' || v !== 0));
      await expect.poll(async () => (await admin.orgConfig(owner.organizationId))?.rate_limiting ?? null, PUSH_VISIBLE).toEqual(want);
    };

    try {
      await test.step('gateway: the org provider is pushed (precondition)', async () => {
        await expect.poll(async () => Object.keys((await admin.orgConfig(owner.organizationId))?.providers ?? {}),
          PUSH_VISIBLE).toContain(providerName);
      });

      await test.step('UI: Rate Limiting, on, Requests per Minute (RPM) 1, Save & Activate', async () => {
        const dialog = await openRateLimiting(page);
        await expect(dialog.getByText(RATE_LIMIT_ENFORCED, { exact: true })).toBeVisible({ timeout: UI_READY });
        await dialog.getByLabel('Enable per-org rate limiting', { exact: true }).check();
        await dialog.getByLabel('Requests per Minute (RPM)', { exact: true }).fill('1');
        await saveAndWait(page, dialog);
      }, { timeout: STEP_BUDGET });

      await test.step('gateway: global_rpm 1 refuses the org\'s second request in the minute, from the other key', async () => {
        await saveReachedGateway();
        await freshWindow(caller);
        const pair = [await call('A', m1), await call('B', m1)];
        expect(pair).toEqual([{ status: 200 }, { status: 429, code: 'rate_limit_exceeded', param: 'global_rpm', retryAfter: true }]);
      });

      await test.step('UI: Rate Limiting, RPM cleared, RPM per Model 1, Save & Activate', async () => {
        const dialog = await openRateLimiting(page);
        await dialog.getByLabel('Requests per Minute (RPM)', { exact: true }).fill('');
        await dialog.getByLabel('RPM per Model', { exact: true }).fill('1');
        await saveAndWait(page, dialog);
      }, { timeout: STEP_BUDGET });

      await test.step('gateway: per_model_rpm 1 refuses a model\'s second request from any key, another model passes', async () => {
        await saveReachedGateway();
        await freshWindow(caller);
        const trio = [await call('A', m2), await call('B', m2), await call('B', m3)];
        expect(trio).toEqual([{ status: 200 }, { status: 429, code: 'rate_limit_exceeded', param: 'per_model_rpm', retryAfter: true }, { status: 200 }]);
      });

      await test.step('UI: Rate Limiting, RPM per Model cleared, TPM per Key 10, Save & Activate', async () => {
        const dialog = await openRateLimiting(page);
        await dialog.getByLabel('RPM per Model', { exact: true }).fill('');
        await dialog.getByLabel('TPM per Key', { exact: true }).fill('10');
        await saveAndWait(page, dialog);
      }, { timeout: STEP_BUDGET });

      await test.step('gateway: per_key_tpm 10 refuses a key whose tokens reached it, the other key passes', async () => {
        await saveReachedGateway();
        await freshWindow(caller);
        const tokens = [await call('A', m1, 5), await call('A', m1, 5), await call('B', m1, 5)];
        expect(tokens).toEqual([{ status: 200 }, { status: 429, code: 'rate_limit_exceeded', param: 'per_key_tpm', retryAfter: true }, { status: 200 }]);
      });
      await testInfo.attach('calls', { contentType: 'application/json', body: JSON.stringify(calls, null, 2) });

      await test.step('UI wire: the three saves sent exactly these merge patches', async () => {
        expect(patches.map((r) => r.postDataJSON())).toEqual([
          { rate_limiting: { enabled: true, global_rpm: 1 } },
          { rate_limiting: { global_rpm: 0, per_model_rpm: 1 } },
          { rate_limiting: { per_model_rpm: 0, per_key_tpm: 10 } },
        ]);
      });

      await test.step('API: the request log holds every call in order, each refusal naming its limit', async () => {
        await expect.poll(async () => (await owner.api.get<{ results: LogRow[] }>(REQUEST_LOGS, { ordering: 'started_at' })).results
          .map((r) => [r.status_code, r.model, r.error_message.match(LIMIT_FIELD)?.[1] ?? null]), LOG_VISIBLE).toEqual([
          [200, m1, null], [429, m1, 'global_rpm'],
          [200, m2, null], [429, m2, 'per_model_rpm'], [200, m3, null],
          [200, m1, null], [429, m1, 'per_key_tpm'], [200, m1, null],
        ]);
      });

      await test.step('UI: Request Logs shows the eight calls, newest first', async () => {
        await page.goto(REQUEST_LOGS_PATH, { waitUntil: 'domcontentloaded' });
        const rows = page.locator('table tbody tr');
        await expect(rows).toHaveCount(8, { timeout: UI_READY });
        const headerTexts = await page.locator('table thead th').allInnerTexts();
        const statusCells = rows.locator(`td:nth-child(${headerTexts.indexOf('Status') + 1})`);
        await expect(statusCells).toHaveText(['200', '429', '200', '200', '429', '200', '429', '200'], { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });
    } finally {
      await admin.dispose();
      await caller.dispose();
      await context.close();
    }
  },
);
