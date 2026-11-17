import { request, type Locator } from '@playwright/test';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { authInitScript } from '../../lib/auth';
import { provisionActor } from '../../lib/provisioning';

// GuardrailManagementSection.jsx TAB_SLUGS: "configuration" opens the Rules view,
// whose PipelineSettings.jsx:76-114 shows Mode and Fail Open read-only with a
// "Change in Settings" link to paths.dashboard.gateway.settings?tab=org-config
// (PipelineSettings.jsx:74). The editable Fail Open switch is in the Org Config
// editor's Guardrails tab (GuardrailConfigTab.jsx:1281-1292; OrgConfigEditor.jsx
// "Save & Activate" :691), saved through update-config.
const RULES_ROUTE = '/dashboard/gateway/guardrails/configuration';
const ORG_CONFIG_PATH = '/dashboard/gateway/settings?tab=org-config';
const UPDATE_CONFIG = /\/agentcc\/gateways\/[^/]+\/update-config\/$/;
// agentcc/urls.py and AgentccOrgConfigViewSet.active/create.
const CONFIGS = '/agentcc/org-configs/';
// e2e/stack/mock-llm/server.mjs: an absent route returns 404, completions echo.
const LOCAL_MOCK = 'http://mock-llm:8080';
const UI_READY = 60_000;

type Config = { id: string; guardrails: { fail_open: boolean } };
type Envelope = { result: Config };
type GatewayOrgConfig = { guardrails?: { fail_open?: boolean } };

test('GW-E2E-004: an organization explicitly opts into continuing after a guardrail outage', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'GW-E2E-004',
    area: 'gateway',
    userGoal: 'An organization owner explicitly opts into continuing through a failed guardrail, and can turn that permission off again',
    steps: [
      'seed a local mock provider and a failing local webhook without opting into fail-open',
      'open Guardrails Rules and see Fail Open off, read-only with a link to Settings; open Settings Org Config and see Fail Open off; a gateway request is refused',
      'turn Fail Open on in Settings, save and reload; the same request succeeds with its unique mock echo',
      'turn Fail Open off in Settings, save and reload; the same request is refused again',
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
      // Stored explicitly: an unset Fail Open shows the gateway's own default
      // (on) in the editor (GuardrailConfigTab.jsx:936-942), not "off".
      fail_open: false,
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
  const admin = await GatewayAdmin.open();
  try {
    const assertPolicy = async (expected: boolean) => {
      const active = await actor.api.get<Envelope>(`${CONFIGS}active/`);
      expect(active.result.guardrails.fail_open).toBe(expected);
      await expect.poll(async () => {
        const wire = await admin.orgConfig(actor.organizationId) as GatewayOrgConfig | null;
        expect(wire?.guardrails).toBeDefined();
        return wire!.guardrails!.fail_open;
      // tenant.GuardrailConfig.FailOpen is *bool with json:",omitempty" (gateway
      // 3c9acf8ab6: an org's own choice applies, nil = unset): omitempty drops only
      // nil, so an explicit false the org stored is serialized as false, not omitted.
      }, POLL.ASYNC_JOB).toBe(expected);
    };
    const send = () => gateway.post('/v1/chat/completions', {
      headers: { Authorization: `Bearer ${key.result.key}` },
      data: { model: `${provider}/gpt-4o-mini`, messages: [{ role: 'user', content: name }] },
    });
    // OrgConfigSection.jsx "Create Custom Config" | "Edit Config", dialog "Edit
    // Organization Config", tab "Guardrails", as in stream-answer-check.spec.ts.
    const openEditor = async () => {
      await page.goto(ORG_CONFIG_PATH, { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: /^(Edit Config|Create Custom Config)$/ }).click({ timeout: UI_READY });
      const dialog = page.getByRole('dialog', { name: 'Edit Organization Config' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('tab', { name: 'Guardrails', exact: true }).click();
      return { dialog, toggle: dialog.getByRole('checkbox', { name: 'Fail Open', exact: true }) };
    };
    // GuardrailConfigTab.test.jsx / .R7i.test.jsx: role checkbox, name "Fail Open".
    const save = async (dialog: Locator) => {
      const saved = page.waitForResponse(res => UPDATE_CONFIG.test(new URL(res.url()).pathname) && res.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Save & Activate', exact: true }).click();
      expect((await saved).status()).toBe(200);
      await expect(dialog).toBeHidden({ timeout: UI_READY });
    };
    // Guardrails Rules: Mode and Fail Open are read-only (data-read-only), with a
    // link to Settings. A plain navigation here is the reload.
    const assertReadOnlyPage = async (failOpen: boolean) => {
      await page.goto(RULES_ROUTE, { waitUntil: 'domcontentloaded' });
      const pipeline = page.getByRole('group', { name: 'Fail-open and run mode', exact: true });
      await expect(pipeline).toBeVisible({ timeout: UI_READY });
      await expect(pipeline).toContainText(`Fail open ${failOpen ? 'on' : 'off'}`, { timeout: UI_READY });
      await expect(pipeline.getByRole('link', { name: 'Change in Settings', exact: true }))
        .toHaveAttribute('href', ORG_CONFIG_PATH, { timeout: UI_READY });
      await expect(pipeline.getByRole('checkbox')).toHaveCount(0);
    };

    await assertReadOnlyPage(false);
    let { dialog, toggle } = await openEditor();
    await expect(toggle).not.toBeChecked({ timeout: UI_READY });
    await assertPolicy(false);
    const closed = await send();
    expect(closed.status(), await closed.text()).toBe(503);
    expect(await closed.json()).toMatchObject({ error: { type: 'guardrail', code: 'guardrail_unavailable' } });
    expect(await closed.json()).not.toHaveProperty('choices');

    await toggle.check();
    await save(dialog);
    ({ dialog, toggle } = await openEditor());
    await expect(toggle).toBeChecked({ timeout: UI_READY });
    await assertPolicy(true);
    await assertReadOnlyPage(true);
    const open = await send();
    expect(open.status(), await open.text()).toBe(200);
    expect((await open.json()).choices[0].message.content).toBe(`echo: ${name}`);

    ({ dialog, toggle } = await openEditor());
    await toggle.uncheck();
    await save(dialog);
    ({ dialog, toggle } = await openEditor());
    await expect(toggle).not.toBeChecked({ timeout: UI_READY });
    await assertPolicy(false);
    await assertReadOnlyPage(false);
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
    await admin.dispose();
    await context.close();
    await accounts.dispose();
  }
});
