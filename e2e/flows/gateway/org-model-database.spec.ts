import { request } from '@playwright/test';
import type { Locator, Page, Request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { POLL } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';

// Pinned off the source:
// - the validation plugin gets the org configs (agentcc-gateway/cmd/agentcc/main.go
//   validationplugin.New + SetTenantStore), and ProcessRequest checks the org's override
//   first, whether or not the model database knows the model
//   (internal/plugins/validation/validation.go).
// - the refusals (internal/plugins/validation/org_overlay.go checkOrgOverride): a
//   capability switched off refuses a request using it with 400 model_capability_error,
//   message "<usage>: the organization's model database turns off <switch> for model
//   \"<m>\"" (tools: "the request sends tools", function_calling); max_output_tokens
//   refuses max_tokens above it with 400 max_tokens_exceeds_limit, error.param
//   "max_tokens", message "max_tokens <asked> exceeds max_output_tokens <limit> that
//   the organization's model database sets for model \"<m>\""; a request that sets
//   neither max_tokens nor max_completion_tokens is sent with max_tokens = the limit.
//   The override is looked up by the model name asked (internal/modeldb/orgdb lookup).
// - the price (internal/plugins/cost/card.go orgCard): a BYOK call's org price is its
//   cost_tracking custom price, else its model_database override's prices merged field
//   by field over the model database's; a minted model name is unknown to the model
//   database (internal/modeldb/litellm.json), so only the override can price it. The
//   mock answers usage 7/7 (e2e/stack/mock-llm/server.mjs), so 7 x 0.001 + 7 x 0.002 =
//   0.021, written in micros (cost/micros.go) and stored by Django as the gateway's
//   cost whenever it sent a cost_source (futureagi/agentcc/services/log_ingestion.py
//   _billed_cost) in agentcc_request_log.cost, numeric(12, 6) (models/request_log.py).
// - the console: Settings > Org Config (GatewaySettingsSection.jsx TAB_SLUGS
//   "org-config"), "Create Custom Config" | "Edit Config" (OrgConfigSection.jsx),
//   dialog "Edit Organization Config", tab "Model Database", "Save & Activate"
//   (OrgConfigEditor.jsx); the tab (settings/ModelDatabaseConfigTab.jsx): a freeSolo
//   input placeholder "Model ID (e.g. gpt-4o, claude-sonnet-4-20250514)" and button
//   "Add Override"; per model an expanded accordion with number fields "Max Output
//   Tokens" (max_output_tokens, Number), "Input per Token" / "Output per Token" /
//   "Cached Input per Token" (pricing.*, Number) and a select per capability
//   ("Function Calling": Default | On | Off; Off saves capabilities.function_calling
//   false, Default deletes it).
// - the wire: POST /agentcc/gateways/default/update-config/ with an RFC 7386 merge
//   patch of the sections that changed (hooks/useConfigPatch.js buildConfigPatch,
//   mergePatch), captured with page.on('request'); Django pushes model_database as
//   stored (services/config_push.py _build_payload); the gateway's copy
//   (GET /-/orgs/{org}/config, server/admin_orgs.go) marshals it as
//   internal/tenant/config.go ModelOverrideOrgConfig.
// - a pre-plugin refusal still runs the post plugins (pipeline/engine.go), so each 400
//   is a request log row; the Request Logs page (/dashboard/gateway/logs, rows "table
//   tbody tr", newest first; columns "Status" and "Cost", the cost by
//   sections/gateway/utils/formatters.js formatCost: >= 0.01 four decimals, 0 "$0.00").
// - wire bodies: AgentccProviderCredentialCreateSerializer (GW-E2E-002, harness/mock-upstream.spec.ts), POST /agentcc/api-keys/ { name } -> result.key, result.gateway_key_id
//   (GW-E2E-007).
// An over-limit max_tokens is refused and an unset one is filled with the limit,
// as the source does it.
const ORG_CONFIG_PATH = '/dashboard/gateway/settings/org-config';
const REQUEST_LOGS_PATH = '/dashboard/gateway/logs';
const UPDATE_CONFIG = /\/agentcc\/gateways\/[^/]+\/update-config\/$/;
const MODEL_ID_PLACEHOLDER = 'Model ID (e.g. gpt-4o, claude-sonnet-4-20250514)';
const MAX_OUTPUT = 50;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// Spec-local names for what each wait waits on.
const PUSH_VISIBLE = POLL.ASYNC_JOB; // Django's push on save reaching the gateway's copy (60 s sync behind it)
const LOG_VISIBLE = POLL.ASYNC_JOB; // the gateway's log flush reaching Django's ingest
// provisioning 120 + seeding 15 + provider push precondition 60 + editor step 75 +
// #1 PUSH_VISIBLE 60 + three calls 5 + #5 LOG_VISIBLE 60 + the three rows ingested
// LOG_VISIBLE 60 + Request Logs step 75 + headroom 30 = 560 s, rounded up.
const FLOW_TIMEOUT = 600_000;

type Created<T> = { result: T };
type Refusal = { status: number; code?: string; param?: string | null; message?: string };

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-014: an org Model Database override prices the organization\'s own-key calls, refuses more output than its limit, fills the limit when none is asked, and refuses tools it turned off',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-014',
      area: 'gateway',
      userGoal:
        'An org admin overrides a model\'s price, output limit and tool use in the organization\'s Model Database, and calls made through the organization\'s own provider are priced by that override, refused when they ask for more output than the limit (and sent with the limit when they name none), and refused tools. As the source does it, an over-limit max_tokens is refused (400), an unset one is filled with the limit',
      steps: [
        'seed the org\'s own provider on the mock serving a minted model, and an org key',
        'open Org Config, open the config editor, Model Database: add an override for the model with Input per Token 0.001, Output per Token 0.002, Max Output Tokens 50 and Function Calling Off, Save & Activate',
        'call the model with max_tokens 100, then with a tool, then with no max_tokens',
        'open Request Logs',
      ],
      backendChecks: [
        'the save reaches the gateway: its copy of the org\'s Model Database holds the override as sent',
        'a request asking for more output than the override allows is refused 400 max_tokens_exceeds_limit on max_tokens, naming 50, and never reaches the provider',
        'a request with tools is refused 400 model_capability_error naming function_calling, and never reaches the provider',
        'a request that sets no max_tokens is sent to the provider with max_tokens 50',
        'that call is priced by the override: 7 input and 7 output tokens at 0.001 and 0.002 cost 0.021 in the request log',
        'the save sent exactly the override as one merge patch of model_database',
      ],
      browserChecks: [
        'Request Logs shows exactly the three calls, Status newest first 200 400 400, and Cost $0.0210 $0.00 $0.00',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    // Minted per run: the model is unknown to the model database, so a cost of 0.021 can
    // only come from the override (#5); the scenario's recorder holds only this flow's
    // calls (#2-#4); the prompts anchor #4.
    const scenario = `gw14-${suffix}`;
    const providerName = `e2e-gw14-${suffix}`;
    const model = `e2e-gw14-model-${suffix}`;
    const prompt = (n: number) => `e2e-gw14-${suffix}-${n}`;

    const upstream = new MockUpstream();
    const provider = await owner.api.post<Created<{ id: string }>>('/agentcc/provider-credentials/', {
      provider_name: providerName, credentials: { api_key: `sk-e2e-gw14-${suffix}` }, base_url: upstream.baseUrl(scenario),
      api_format: 'openai', models_list: [model], extra_config: { api_path_prefix: '/v1' },
    });
    const key = await owner.api.post<Created<{ key: string; gateway_key_id: string }>>('/agentcc/api-keys/', { name: `e2e-gw14-${suffix}` });
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, scenario, model, organizationId: owner.organizationId,
        providerId: provider.result.id, keyGatewayId: key.result.gateway_key_id }, null, 2),
    });

    const admin = await GatewayAdmin.open();
    const caller = await request.newContext();
    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    const patches: Request[] = [];
    page.on('request', (r) => {
      if (r.method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname)) patches.push(r);
    });
    const calls: { n: number; status: number; requestId: string }[] = [];
    const call = async (n: number, extra: Record<string, unknown>) => {
      const res = await caller.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
        headers: { Authorization: `Bearer ${key.result.key}` },
        data: { model, messages: [{ role: 'user', content: prompt(n) }], ...extra },
      });
      calls.push({ n, status: res.status(), requestId: res.headers()['x-agentcc-request-id'] ?? '' });
      return res;
    };
    const refusal = async (res: Awaited<ReturnType<typeof call>>): Promise<Refusal> => {
      const body = (await res.json().catch(() => ({}))) as { error?: { code?: string; param?: string | null; message?: string } };
      return { status: res.status(), code: body.error?.code, param: body.error?.param, message: body.error?.message };
    };
    const upstreamCalls = async () => (await upstream.requests(scenario)).length;
    const openModelDatabase = async (p: Page): Promise<Locator> => {
      await p.goto(ORG_CONFIG_PATH, { waitUntil: 'domcontentloaded' });
      await p.getByRole('button', { name: /^(Edit Config|Create Custom Config)$/ }).click({ timeout: UI_READY });
      const dialog = p.getByRole('dialog', { name: 'Edit Organization Config' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('tab', { name: 'Model Database', exact: true }).click();
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
      await test.step('gateway: the org provider is pushed (precondition)', async () => {
        await expect.poll(async () => Object.keys((await admin.orgConfig(owner.organizationId))?.providers ?? {}),
          PUSH_VISIBLE).toContain(providerName);
      });

      await test.step('UI: Model Database, an override for the model, Save & Activate', async () => {
        const dialog = await openModelDatabase(page);
        await dialog.getByPlaceholder(MODEL_ID_PLACEHOLDER, { exact: true }).fill(model);
        await dialog.getByRole('button', { name: 'Add Override', exact: true }).click();
        await expect(dialog.getByText(model, { exact: true })).toBeVisible({ timeout: UI_READY });
        await dialog.getByLabel('Max Output Tokens', { exact: true }).fill(String(MAX_OUTPUT));
        await dialog.getByLabel('Input per Token', { exact: true }).fill('0.001');
        await dialog.getByLabel('Output per Token', { exact: true }).fill('0.002');
        await dialog.getByRole('combobox', { name: 'Function Calling' }).click();
        await page.getByRole('option', { name: 'Off', exact: true }).click();
        await saveAndWait(page, dialog);
      }, { timeout: STEP_BUDGET });

      await test.step('gateway: the save reaches the gateway\'s copy of the org\'s Model Database', async () => {
        const sent = patches.at(-1)?.postDataJSON()?.model_database?.overrides?.[model] ?? null;
        await testInfo.attach('sent-override', { contentType: 'application/json', body: JSON.stringify(sent) });
        await expect.poll(async () => (await admin.orgConfig(owner.organizationId))?.model_database?.overrides?.[model] ?? null,
          PUSH_VISIBLE).toEqual(sent);
      });

      await test.step('gateway: max_tokens above the override\'s limit is refused, and never sent', async () => {
        const r = await refusal(await call(1, { max_tokens: 100 }));
        expect({ status: r.status, code: r.code, param: r.param, names50: (r.message ?? '').includes(`exceeds max_output_tokens ${MAX_OUTPUT} that the organization's model database sets`), upstreamCalls: await upstreamCalls() })
          .toEqual({ status: 400, code: 'max_tokens_exceeds_limit', param: 'max_tokens', names50: true, upstreamCalls: 0 });
      });

      await test.step('gateway: a request with tools is refused, and never sent', async () => {
        const r = await refusal(await call(2, { tools: [{ type: 'function', function: { name: 'e2e_gw14_tool', parameters: { type: 'object', properties: {} } } }] }));
        expect({ status: r.status, code: r.code, namesSwitch: (r.message ?? '').includes('the organization\'s model database turns off function_calling'), upstreamCalls: await upstreamCalls() })
          .toEqual({ status: 400, code: 'model_capability_error', namesSwitch: true, upstreamCalls: 0 });
      });

      let pricedId = '';
      await test.step('upstream: a request that sets no max_tokens is sent with the limit', async () => {
        const res = await call(3, {});
        expect(res.status()).toBe(200);
        pricedId = res.headers()['x-agentcc-request-id'] ?? '';
        const sent = (await upstream.requests(scenario)).map((r) => {
          const body = r.body as { max_tokens?: number; messages?: { content: string }[] };
          return { max_tokens: body.max_tokens, content: body.messages?.at(-1)?.content };
        });
        expect(sent).toEqual([{ max_tokens: MAX_OUTPUT, content: prompt(3) }]);
      });
      await testInfo.attach('calls', { contentType: 'application/json', body: JSON.stringify(calls, null, 2) });

      await test.step('storage: that call is priced by the override, 0.021', async () => {
        await expect.poll(async () => (await probe.pg<{ cost: string }>(
          'SELECT cost::text AS cost FROM agentcc_request_log WHERE organization_id = $1 AND request_id = $2',
          [owner.organizationId, pricedId])).map((r) => r.cost), LOG_VISIBLE).toEqual(['0.021000']);
      });

      await test.step('UI wire: the save sent exactly the override, as one merge patch', async () => {
        expect(patches.map((r) => r.postDataJSON())).toEqual([{ model_database: { overrides: { [model]: {
          pricing: { input_per_token: 0.001, output_per_token: 0.002 },
          max_output_tokens: MAX_OUTPUT,
          capabilities: { function_calling: false },
        } } } }]);
      });

      await test.step('storage: the three calls\' rows have been ingested (precondition: the page loads its rows once, no refetch)', async () => {
        await expect.poll(async () => (await probe.pg<{ request_id: string }>(
          'SELECT request_id FROM agentcc_request_log WHERE organization_id = $1 AND request_id = ANY($2::text[])',
          [owner.organizationId, calls.map((c) => c.requestId)])).length, LOG_VISIBLE).toBe(calls.length);
      });

      await test.step('UI: Request Logs shows the three calls, newest first, the priced one at $0.0210', async () => {
        await page.goto(REQUEST_LOGS_PATH, { waitUntil: 'domcontentloaded' });
        const rows = page.locator('table tbody tr');
        await expect(rows).toHaveCount(3, { timeout: UI_READY });
        const headerTexts = await page.locator('table thead th').allInnerTexts();
        const column = (name: string) => rows.locator(`td:nth-child(${headerTexts.indexOf(name) + 1})`);
        await expect(column('Status')).toHaveText(['200', '400', '400'], { timeout: UI_READY });
        await expect(column('Cost')).toHaveText(['$0.0210', '$0.00', '$0.00'], { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });
    } finally {
      await admin.dispose();
      await caller.dispose();
      await context.close();
    }
  },
);
