import { request, test, expect } from '@playwright/test';
import { authInitScript } from '../../lib/auth';
import { provisionActor } from '../../lib/provisioning';
import { POLL, StateProbe } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned facts:
// - GatewayProviderUpdateRequestSerializer (futureagi/agentcc/views/gateway.py
//   update_provider): {name, config: {api_key, base_url, api_format, models}}.
//   The provider is saved as the dashboard's OpenRouter preset saves it, with
//   api_format "openai". base_url "" layers it onto e2e/stack/gateway.e2e.yaml's
//   `openrouter-chat` entry (OpenAI format, http://mock-llm:8080/api/v1), so the
//   gateway serves the decision through the OpenAI-format OpenRouter route
//   (GetOrCreateOpenRouterDecision, the base_url's origin) with the org's key
//   (OrgProviderCache.baseConfig). Mock-llm cannot be reached through an org's
//   own base_url: private provider URLs stay refused on this stack
//   (AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS is false), so the flow cannot seed an
//   explicit base_url. It pins the format instead: the saved
//   agentcc_provider_credential row must say api_format "openai" with no
//   base_url of its own, and gateway.e2e.yaml's entry is OpenAI format too.
// - e2e/stack/mock-llm/server.mjs accepts `Bearer e2e-decision-openrouter-key`
//   only on /api/alpha/decisions (on /v1/systemone it answers 401), requires
//   the gateway's OpenRouter routing policy, answers `e2e-decision:pass` with
//   noul 0.9, resolves ~typesafe/jev-latest to typesafe/jev-1.14 and always
//   reports 37 input tokens. A 200 answer therefore means the gateway posted
//   to /api/alpha/decisions with the org's key.
// - The flow provisions its own organization instead of the worker-scoped
//   fixture actor: it saves a provider, a 1 RPM org config and request-log
//   rows, none of which may reach other flows sharing the worker's org (they
//   once broke GW-E2E-001's usage totals and left the org at 1 RPM).
// - AgentccAPIKeyViewSet.create returns the raw key once, plus gateway_key_id,
//   which is what agentcc_request_log.api_key_id stores.
const PROVIDER = 'openrouter-chat';
const PROVIDER_KEY = 'e2e-decision-openrouter-key';
const MODEL = 'openrouter-chat/~typesafe/jev-latest';
const RESOLVED = 'typesafe/jev-1.14';
const MOCK_INPUT_TOKENS = 37;
// Request Logs table's list endpoint and the Org Config editor's save
// (useCreateOrgConfig).
const REQUEST_LOGS_PATH = '/agentcc/request-logs/';
const ORG_CONFIGS_PATH = '/agentcc/org-configs/';
const LOG_VISIBLE = POLL.ASYNC_JOB;
const UI_READY = 60_000;

interface CreatedKey {
  result: { key: string; gateway_key_id: string };
}
interface LogRow {
  model: string;
  provider: string;
  resolved_model: string;
  status_code: number;
  input_tokens: number;
  output_tokens: number;
  cost: string;
  metadata: Record<string, unknown>;
}

test(
  'GW-E2E-005: a decision through the org OpenRouter provider is answered, logged with its cost, and held to the key rate limit',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-005',
      area: 'gateway',
      userGoal:
        'A self-hosted team calls an OpenRouter Jev decision model through the gateway with its own OpenRouter key and a gateway key, sees the call in Request Logs with its cost, and is held to its rate limit',
      steps: [
        'sign in as a freshly provisioned organization of its own',
        'save the OpenRouter provider key and Jev models for the organization, as the dashboard preset does (OpenAI format)',
        'mint a gateway API key',
        'send a decision (a System One body to /v1/systemone) to openrouter-chat/~typesafe/jev-latest with that key',
        'open Gateway Request Logs and find the decision',
        'limit each key to one request per minute',
        'send the same decision again',
      ],
      backendChecks: [
        'the decision answer is the vendor answer, byte for byte, from OpenRouter\'s /api/alpha/decisions on the org key (the mock takes that key nowhere else)',
        'the saved PG agentcc_provider_credential row for openrouter-chat has api_format openai and no base_url of its own',
        'exactly one PG agentcc_request_log row for the key records the decision: endpoint_type decision, provider openrouter-chat, the vendor token counts and a cost above zero',
        'the Request Logs list endpoint the UI calls returns that row',
        'the second decision is refused with 429 before the vendor is called: no tokens and no cost are recorded for it',
      ],
    }),
  },
  async ({ browser }, testInfo) => {
    // Seed + call (~10s), two log polls (2×60s), three UI waits (3×60s): 310s.
    test.setTimeout(330_000);
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    // Its own organization: nothing it saves reaches the worker's shared org.
    const accounts = await request.newContext({ baseURL: E2E.apiUrl });
    const actor = await provisionActor(accounts, `gw005-${suffix}`);
    const probe = new StateProbe({ api: actor.api, chUrl: E2E.chUrl, chDatabase: E2E.chDatabase,
      chPassword: E2E.chPassword, pgUrl: E2E.pgUrl, catalogChUrl: E2E.catalogChUrl,
      catalogChDatabase: E2E.catalogChDatabase });
    const context = await browser.newContext({
      baseURL: E2E.appUrl,
      ...(process.env.E2E_RECORD_VIDEO === '1' ? { recordVideo: { dir: testInfo.outputPath('video') } } : {}),
    });
    await context.addInitScript(authInitScript, {
      access: actor.tokens.access,
      refresh: actor.tokens.refresh,
      organizationId: actor.organizationId,
      workspaceId: actor.workspaceId,
    });
    const page = await context.newPage();
    const marker = `e2e-decision:pass gw005-${suffix}`;
    const body = {
      model: MODEL,
      state: { ticket: marker },
      questions: { resolved: { type: 'noul', instructions: `Was ticket gw005-${suffix} resolved?` } },
    };

    await actor.api.post('/agentcc/gateways/default/update-provider/', {
      name: PROVIDER,
      config: {
        api_key: PROVIDER_KEY,
        base_url: '',
        api_format: 'openai',
        models: ['typesafe/jev-1.13', '~typesafe/jev-latest'],
      },
    });
    // The format the flow claims is the one saved, not one inherited by accident.
    const saved = await probe.pg<{ api_format: string; base_url: string }>(
      'SELECT api_format, base_url FROM agentcc_provider_credential WHERE organization_id = $1 AND provider_name = $2 AND NOT deleted',
      [actor.organizationId, PROVIDER],
    );
    expect(saved).toEqual([{ api_format: 'openai', base_url: '' }]);
    const created = await actor.api.post<CreatedKey>('/agentcc/api-keys/', { name: `e2e-gw005-${suffix}` });
    const gatewayKeyId = created.result.gateway_key_id;
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ organizationId: actor.organizationId, gatewayKeyId, marker }),
    });

    const gateway = await request.newContext({
      baseURL: E2E.gatewayUrl,
      extraHTTPHeaders: { Authorization: `Bearer ${created.result.key}` },
    });
    const logsFor = () =>
      probe.pg<LogRow>(
        'SELECT model, provider, resolved_model, status_code, input_tokens, output_tokens, cost::text AS cost, metadata FROM agentcc_request_log WHERE organization_id = $1 AND api_key_id = $2 ORDER BY started_at',
        [actor.organizationId, gatewayKeyId],
      );

    await test.step('API: the decision is answered through the org OpenRouter provider', async () => {
      // The org config push is asynchronous; the first call can precede it.
      let answer = '';
      await expect
        .poll(async () => {
          const res = await gateway.post('/v1/systemone', { data: body });
          answer = await res.text();
          return res.status();
        }, POLL.ASYNC_JOB)
        .toBe(200);
      expect(JSON.parse(answer)).toEqual({
        model: RESOLVED,
        answers: { resolved: { type: 'noul', noul: 0.9 } },
        usage: { input_tokens: MOCK_INPUT_TOKENS, output_tokens: 0 },
      });
    });

    await test.step('storage: one request log row records the decision with its cost', async () => {
      await expect.poll(async () => (await logsFor()).length, LOG_VISIBLE).toBeGreaterThan(0);
      const rows = (await logsFor()).filter((row) => row.status_code === 200);
      expect(rows).toHaveLength(1);
      expect(rows[0]).toMatchObject({
        provider: PROVIDER,
        status_code: 200,
        input_tokens: MOCK_INPUT_TOKENS,
        output_tokens: 0,
      });
      expect(rows[0].metadata.endpoint_type).toBe('decision');
      expect(Number(rows[0].cost)).toBeGreaterThan(0);
    });

    await test.step('UI: the decision appears in Gateway Request Logs', async () => {
      await page.goto('/dashboard/gateway/logs', { waitUntil: 'domcontentloaded' });
      // RequestTable columns (columnModel.js): Timestamp, Model, Provider, ...
      const row = page.locator('tbody tr').filter({ hasText: MODEL });
      await expect(row).toHaveCount(1, { timeout: UI_READY });
      await expect(row.locator('td:nth-child(2)')).toHaveText(MODEL);
      await expect(row.locator('td:nth-child(3)')).toHaveText(PROVIDER);
      await testInfo.attach('request-logs', { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });

      const listed = await actor.api.get<{ count: number; results: { model: string; provider: string }[] }>(
        REQUEST_LOGS_PATH, { api_key_id: gatewayKeyId },
      );
      expect(listed.count).toBe(1);
      expect(listed.results[0]).toMatchObject({ model: MODEL, provider: PROVIDER });
    });

    await test.step('UI: limit each gateway key to one request per minute', async () => {
      await page.goto('/dashboard/gateway/settings', { waitUntil: 'domcontentloaded' });
      await page.getByRole('tab', { name: 'Org Config', exact: true }).click();
      await page.getByRole('button', { name: /^(Create Custom Config|Edit Config)$/ }).click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('tab', { name: 'Rate Limiting', exact: true }).click();
      await dialog.getByLabel('Enable per-org rate limiting').check();
      await dialog.getByLabel('RPM per Key').fill('1');
      const saved = page.waitForResponse(
        (r) => new URL(r.url()).pathname === ORG_CONFIGS_PATH && r.request().method() === 'POST',
        { timeout: UI_READY },
      );
      await dialog.getByRole('button', { name: 'Save & Activate' }).click();
      expect((await saved).status()).toBeLessThan(300);
      await expect(dialog).toBeHidden({ timeout: UI_READY });
      await testInfo.attach('rate-limit-saved', { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
    });

    await test.step('API: the next decision is refused with 429 before the vendor is called', async () => {
      // Two calls back to back: the second is past the one-a-minute limit
      // whatever minute window the first fell in.
      const statuses: number[] = [];
      let refusal = '';
      for (let i = 0; i < 2; i++) {
        const res = await gateway.post('/v1/systemone', { data: body });
        statuses.push(res.status());
        refusal = await res.text();
      }
      expect(statuses[1]).toBe(429);
      expect(JSON.parse(refusal).error.code).toBe('rate_limit_exceeded');
      await testInfo.attach('statuses-after-limit', { contentType: 'application/json', body: JSON.stringify(statuses) });

      // Every answered call has its own row with the vendor's tokens and a
      // cost; every refused one records neither.
      const answered = 1 + statuses.filter((s) => s === 200).length;
      await expect
        .poll(async () => (await logsFor()).length, LOG_VISIBLE)
        .toBeGreaterThanOrEqual(answered + 1);
      const rows = await logsFor();
      expect(rows.filter((row) => row.status_code === 200)).toHaveLength(answered);
      const refused = rows.filter((row) => row.status_code !== 200);
      expect(refused.length).toBeGreaterThan(0);
      for (const row of refused) {
        expect(row).toMatchObject({ status_code: 429, input_tokens: 0, output_tokens: 0 });
        expect(Number(row.cost)).toBe(0);
      }
    });

    await gateway.dispose();
    await probe.dispose();
    await context.close();
    await accounts.dispose();
  },
);
