import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { authInitScript } from '../../lib/auth';
import { provisionActor } from '../../lib/provisioning';

// GuardrailManagementSection.jsx TAB_SLUGS and ConfigTab's Save & Activate.
const RULES_ROUTE = '/dashboard/gateway/guardrails/configuration';
// agentcc/urls.py and AgentccOrgConfigViewSet.active/create.
const CONFIGS = '/agentcc/org-configs/';
// docker-compose.distributed.yml default admin token, as in GW-E2E-002.
const ADMIN_TOKEN = 'local-dev-only-admin-token-replace-me';
// e2e/stack/mock-llm/server.mjs: an absent route returns 404, completions echo.
const LOCAL_MOCK = 'http://mock-llm:8080';
const UI_READY = 60_000;

type Config = { id: string; guardrails: { fail_open: boolean } };
type Envelope = { result: Config };
type GatewayConfigs = { data: Record<string, { guardrails: { fail_open: boolean } }> };

test('GW-E2E-004: an organization explicitly opts into continuing after a guardrail outage', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'GW-E2E-004',
    area: 'gateway',
    userGoal: 'An organization owner explicitly opts into continuing through a failed guardrail, and can turn that permission off again',
    steps: [
      'seed a local mock provider and a failing local webhook without opting into fail-open',
      'open Guardrails Rules and see Fail Open off; a gateway request is refused',
      'turn Fail Open on, save and reload; the same request succeeds with its unique mock echo',
      'turn Fail Open off, save and reload; the same request is refused again',
      'submit an invalid boolean and confirm no configuration version is created',
    ],
    backendChecks: [
      'the active organization config and pushed gateway config store false before opt-in, true after opt-in, and false after revocation',
      'the gateway refuses the local webhook outage before opt-in and after revocation, and returns the unique mock echo only while opted in',
      'an invalid boolean is refused without creating a configuration version',
    ],
  }),
}, async ({ browser, probe }, testInfo) => {
  // Seven UI waits, three pushes, three HTTP requests and seeding headroom.
  test.setTimeout(720_000);
  // The blocking check, provider, key and request logs below belong to an
  // organization of this test's own, so later specs on the worker's shared
  // actor never see them.
  const accounts = await request.newContext({ baseURL: E2E.apiUrl });
  const actor = await provisionActor(accounts, `gw-optin-${testInfo.workerIndex}`);
  const context = await browser.newContext({ baseURL: E2E.appUrl });
  await context.addInitScript(authInitScript, {
    access: actor.tokens.access,
    refresh: actor.tokens.refresh,
    organizationId: actor.organizationId,
    workspaceId: actor.workspaceId,
  });
  const page = await context.newPage();
  const blockedURLs: string[] = [];
  await context.route('**/*', route => {
    const host = new URL(route.request().url()).hostname;
    if (['localhost', '127.0.0.1', 'backend', 'frontend', 'mock-llm', 'agentcc-gateway'].includes(host)) return route.continue();
    blockedURLs.push(route.request().url());
    return route.abort();
  });
  const name = `e2e-optin-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  // gateway.e2e.yaml's `openai` entry is the managed mock. An org provider with that
  // ID and no base_url inherits its upstream (OrgProviderCache.baseConfig), so the
  // flow reaches the mock without setting a provider URL of its own.
  const provider = 'openai';
  const check = `${name}-check`;
  // Bodies pinned from the provider credential, key and org-config serializers.
  const credential = await actor.api.post<{ result: { id: string } }>('/agentcc/provider-credentials/', {
    provider_name: provider,
    credentials: { api_key: 'synthetic-local-only' },
    api_format: 'openai',
    models_list: ['gpt-4o-mini'],
  });
  const key = await actor.api.post<{ result: { id: string; key: string } }>('/agentcc/api-keys/', { name });
  const seed = await actor.api.post<Envelope>(CONFIGS, {
    guardrails: {
      pipeline_mode: 'parallel',
      timeout_ms: 1000,
      checks: { [check]: { enabled: true, action: 'block', confidence_threshold: 1, config: { url: `${LOCAL_MOCK}/unavailable-guardrail`, retry: 0 } } },
    },
    change_description: name,
  });
  await testInfo.attach('seeded-ids', {
    body: JSON.stringify({ organization: actor.organizationId, provider: credential.result.id, key: key.result.id, config: seed.result.id, name, check }),
    contentType: 'application/json',
  });
  const gateway = await request.newContext({ baseURL: E2E.gatewayUrl, timeout: 30_000 });
  try {
    const assertPolicy = async (expected: boolean) => {
      const active = await actor.api.get<Envelope>(`${CONFIGS}active/`);
      expect(active.result.guardrails.fail_open).toBe(expected);
      await expect.poll(async () => {
        const res = await gateway.get('/-/orgs/configs', { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } });
        expect(res.status()).toBe(200);
        const wire = await res.json() as GatewayConfigs;
        const pushed = wire.data[actor.organizationId]?.guardrails;
        expect(pushed).toBeDefined();
        return pushed.fail_open;
      // tenant.GuardrailConfig.FailOpen is a bool with json:",omitempty":
      // the admin response omits false, rather than serializing false.
      }, POLL.ASYNC_JOB).toBe(expected ? true : undefined);
    };
    const send = () => gateway.post('/v1/chat/completions', {
      headers: { Authorization: `Bearer ${key.result.key}` },
      data: { model: `${provider}/gpt-4o-mini`, messages: [{ role: 'user', content: name }] },
    });
    const save = async () => {
      const saved = page.waitForResponse(res => res.url().endsWith(CONFIGS) && res.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Save & Activate', exact: true }).click();
      expect((await saved).status()).toBe(200);
      await page.reload();
    };

    await page.goto(RULES_ROUTE);
    const toggle = page.getByRole('checkbox', { name: 'Fail Open' });
    await expect(toggle).not.toBeChecked({ timeout: UI_READY });
    await assertPolicy(false);
    const closed = await send();
    expect(closed.status(), await closed.text()).toBe(503);
    expect(await closed.json()).toMatchObject({ error: { type: 'guardrail', code: 'guardrail_unavailable' } });
    expect(await closed.json()).not.toHaveProperty('choices');

    await toggle.check();
    await save();
    await expect(toggle).toBeChecked({ timeout: UI_READY });
    await assertPolicy(true);
    const open = await send();
    expect(open.status(), await open.text()).toBe(200);
    expect((await open.json()).choices[0].message.content).toBe(`echo: ${name}`);

    await toggle.uncheck();
    await save();
    await expect(toggle).not.toBeChecked({ timeout: UI_READY });
    await assertPolicy(false);
    const revoked = await send();
    expect(revoked.status(), await revoked.text()).toBe(503);
    expect(await revoked.json()).toMatchObject({ error: { type: 'guardrail', code: 'guardrail_unavailable' } });
    expect(await revoked.json()).not.toHaveProperty('choices');

    // Pin the immutable version table from AgentccOrgConfig.Meta.db_table.
    const versions = async () => (await probe.pg<{ n: string }>(
      'SELECT count(*) AS n FROM agentcc_org_config WHERE organization_id = $1',
      [actor.organizationId],
    ))[0].n;
    const before = await versions();
    await expect(actor.api.post(CONFIGS, { guardrails: { fail_open: 'not-a-boolean' } })).rejects.toMatchObject({ status: 400 });
    expect(await versions()).toBe(before);
  } finally {
    await testInfo.attach('blocked-external-urls', { body: JSON.stringify(blockedURLs), contentType: 'application/json' });
    await gateway.dispose();
    await context.close();
    await accounts.dispose();
  }
});
