import { request, type APIRequestContext, type APIResponse, type Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { authInitScript } from '../../lib/auth';
import { provisionActor } from '../../lib/provisioning';

// GuardrailManagementSection.jsx TAB_SLUGS ("policies") and paths.dashboard.gateway.guardrails.policies.
const POLICIES_ROUTE = '/dashboard/gateway/guardrails/policies';
// agentcc/urls.py: AgentccGuardrailPolicyViewSet and AgentccOrgConfigViewSet.
const POLICIES = '/agentcc/guardrail-policies/';
const CONFIGS = '/agentcc/org-configs/';
// docker-compose.distributed.yml default admin token, as in GW-E2E-004.
const ADMIN_TOKEN = 'local-dev-only-admin-token-replace-me';
// The AWS documentation's example key; the built-in Secret Detection finds it
// (agentcc-gateway/internal/server/guardrail_routes_test.go awsKey).
const AWS_KEY = 'AKIAIOSFODNN7EXAMPLE';
// agentcc-gateway/internal/middleware/requestid.go; stored as agentcc_request_log.request_id.
const REQUEST_ID_HEADER = 'x-agentcc-request-id';
// agentcc-gateway/internal/server/handlers.go: set only when a guardrail acted on the request.
const TRIGGERED_HEADER = 'x-agentcc-guardrail-triggered';
// The gateway posts each request log to the backend webhook; ingestion is synchronous,
// the poll only absorbs the gateway's flush interval.
const LOG_VISIBLE = POLL.ASYNC_JOB;
const UI_READY = 60_000;

type Envelope<T> = { result: T };
type Policy = { id: string; name: string; scope: string; mode: string; applied_keys: string[]; gateway_synced: boolean };
type KeyPolicyWire = { id: string; name: string; mode: string; key_ids: string[]; checks: Record<string, { action: string }> };
type GatewayConfigs = { data: Record<string, { guardrails?: { key_policies?: KeyPolicyWire[] } }> };
type GuardrailEntry = { name: string; action: string; policy_name?: string; policy_scope?: string; mode?: string; outcome?: string; would_action?: string };

test('GW-E2E-007: an admin applies a guardrail policy to one gateway key, first monitoring and then enforcing it', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'GW-E2E-007',
    area: 'gateway',
    userGoal: 'An admin rolls a guardrail out to a single API key: monitor it first, then enforce it, without affecting other keys or weakening org-wide guardrails',
    steps: [
      'seed a local mock provider and two gateway keys in an organization of this test',
      'open Guardrails Policies, create a Secret Detection block policy for key A in Monitor mode, picking the key by name; the dialog offers no Org-wide scope',
      'a prompt with a fake AWS key sent with key A gets the unchanged echo; key B also gets it',
      'switch the policy to Enforce in the dialog; key A is refused with content_blocked and key B still gets the echo',
      'add an org-wide Secret Detection block and set the key policy back to Monitor; key A is still refused',
      'create the documented key policy body through the API; an unknown key ID is refused without creating a policy',
    ],
    backendChecks: [
      'the gateway admin config for the organization carries the key policy with key A\'s gateway key ID and the mode the UI saved',
      'key A\'s monitored request log records the policy name, mode monitor and outcome would_block, with no triggered response header; key B\'s log has no guardrail entry',
      'with the policy enforced, key A gets 403 content_blocked and its log records outcome blocked; key B gets 200',
      'an org-wide block wins over a key policy in monitor mode for the same check',
      'the documented request body is accepted verbatim, and an unknown key ID returns 400 without a new agentcc_guardrail_policy row',
    ],
  }),
}, async ({ browser, probe }, testInfo) => {
  // Four UI saves and reloads, four gateway config polls, six log polls and seeding headroom.
  test.setTimeout(900_000);
  // An organization of this test's own, so its policies and org-wide block never reach
  // other specs on the worker's shared actor (as GW-E2E-004 does).
  const accounts = await request.newContext({ baseURL: E2E.apiUrl });
  const actor = await provisionActor(accounts, `gw-keypol-${testInfo.workerIndex}`);
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
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const policyName = `e2e-rollout-${suffix}`;
  // gateway.e2e.yaml's `openai` entry is the managed mock (see GW-E2E-004).
  const provider = 'openai';
  // Bodies pinned from the provider credential and API key serializers.
  const credential = await actor.api.post<Envelope<{ id: string }>>('/agentcc/provider-credentials/', {
    provider_name: provider,
    credentials: { api_key: 'synthetic-local-only' },
    api_format: 'openai',
    models_list: ['gpt-4o-mini'],
  });
  type CreatedKey = { id: string; key: string; gateway_key_id: string; name: string };
  const keyA = (await actor.api.post<Envelope<CreatedKey>>('/agentcc/api-keys/', { name: `e2e-key-a-${suffix}` })).result;
  const keyB = (await actor.api.post<Envelope<CreatedKey>>('/agentcc/api-keys/', { name: `e2e-key-b-${suffix}` })).result;
  await testInfo.attach('seeded-ids', {
    body: JSON.stringify({ organization: actor.organizationId, provider: credential.result.id, keyA: keyA.id, keyAGateway: keyA.gateway_key_id, keyB: keyB.id, keyBGateway: keyB.gateway_key_id, policyName }),
    contentType: 'application/json',
  });
  const gateway = await request.newContext({ baseURL: E2E.gatewayUrl, timeout: 30_000 });
  try {
    const prompt = `${policyName} deploy with ${AWS_KEY}`;
    const send = (key: CreatedKey) => gateway.post('/v1/chat/completions', {
      headers: { Authorization: `Bearer ${key.key}` },
      data: { model: `${provider}/gpt-4o-mini`, messages: [{ role: 'user', content: prompt }] },
    });
    const expectEcho = async (res: APIResponse) => {
      expect(res.status(), await res.text()).toBe(200);
      expect((await res.json()).choices[0].message.content).toBe(`echo: ${prompt}`);
      expect(res.headers()[TRIGGERED_HEADER]).toBeUndefined();
    };
    const expectBlocked = async (res: APIResponse) => {
      expect(res.status(), await res.text()).toBe(403);
      expect(await res.json()).toMatchObject({ error: { code: 'content_blocked' } });
      expect(await res.json()).not.toHaveProperty('choices');
    };
    // The request log row the gateway shipped for one response, by its request ID.
    const guardrailLog = async (res: APIResponse) => {
      const requestId = res.headers()[REQUEST_ID_HEADER];
      expect(requestId).toBeTruthy();
      let entries: GuardrailEntry[] | null = null;
      await expect.poll(async () => {
        const rows = await probe.pg<{ guardrail_results: GuardrailEntry[] | null }>(
          'SELECT guardrail_results FROM agentcc_request_log WHERE organization_id = $1 AND request_id = $2',
          [actor.organizationId, requestId],
        );
        if (rows.length === 0) return false;
        entries = rows[0].guardrail_results ?? [];
        return true;
      }, LOG_VISIBLE).toBe(true);
      return entries as unknown as GuardrailEntry[];
    };
    const pushedPolicy = async (expected: { mode: string } | null) => {
      await expect.poll(async () => {
        const res = await gateway.get('/-/orgs/configs', { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } });
        expect(res.status()).toBe(200);
        const wire = await res.json() as GatewayConfigs;
        const found = wire.data[actor.organizationId]?.guardrails?.key_policies?.find(p => p.name === policyName);
        return found ? { mode: found.mode, key_ids: found.key_ids, action: found.checks['secret-detection']?.action } : null;
      }, POLL.ASYNC_JOB).toEqual(expected && { mode: expected.mode, key_ids: [keyA.gateway_key_id], action: 'block' });
    };
    const savePolicy = async (current: Page) => {
      const saved = current.waitForResponse(res => res.url().includes(POLICIES) && ['POST', 'PATCH'].includes(res.request().method()), { timeout: UI_READY });
      await current.getByRole('button', { name: 'Save policy', exact: true }).click();
      const res = await saved;
      expect(res.status()).toBe(200);
      return (await res.json() as Envelope<Policy>).result;
    };
    const editMode = async (mode: 'Enforce' | 'Monitor (log only)') => {
      await page.goto(POLICIES_ROUTE);
      await page.getByRole('button', { name: `Actions for ${policyName}`, exact: true }).click({ timeout: UI_READY });
      await page.getByRole('menuitem', { name: 'Edit' }).click();
      const dialog = page.getByTestId('guardrail-policy-dialog');
      await dialog.getByRole('radio', { name: mode, exact: true }).check();
      return savePolicy(page);
    };

    // 1. Create the policy from the UI: Secret Detection block, key A, Monitor.
    await page.goto(POLICIES_ROUTE);
    await page.getByRole('button', { name: 'New policy', exact: true }).first().click({ timeout: UI_READY });
    const dialog = page.getByTestId('guardrail-policy-dialog');
    await expect(dialog).toBeVisible({ timeout: UI_READY });
    await dialog.getByRole('textbox', { name: 'Name', exact: true }).fill(policyName);
    // The tab manages key policies only: there is no Org-wide scope to pick.
    await expect(dialog.getByRole('radio', { name: 'Org-wide' })).toHaveCount(0);
    const picker = dialog.getByRole('combobox', { name: 'API keys' });
    await picker.click();
    await picker.fill(keyA.name);
    // The option shows the key's name and prefix, never its secret.
    const option = page.getByRole('option', { name: new RegExp(`^${keyA.name} \\(`) });
    await expect(option).toBeVisible({ timeout: UI_READY });
    await expect(option).not.toContainText(keyA.key);
    await option.click();
    await dialog.getByRole('radio', { name: 'Monitor (log only)', exact: true }).check();
    await dialog.getByRole('checkbox', { name: 'Secret Detection' }).check();
    await expect(dialog.getByRole('combobox', { name: 'Secret Detection action' })).toHaveText('Block');
    const created = await savePolicy(page);
    expect(created).toMatchObject({ name: policyName, scope: 'key', mode: 'monitor', applied_keys: [keyA.id] });
    await expect(page.getByTestId(`guardrail-policy-row-${created.id}`)).toContainText('Monitor', { timeout: UI_READY });

    // 2. The policy reaches the gateway with key A's gateway key ID.
    await pushedPolicy({ mode: 'monitor' });

    // 3. Monitor: key A gets the unchanged echo, and its log records what would have happened.
    const monitoredA = await send(keyA);
    await expectEcho(monitoredA);
    expect(await guardrailLog(monitoredA)).toEqual([
      expect.objectContaining({ name: 'secret-detection', action: 'log', policy_name: policyName, policy_scope: 'key', mode: 'monitor', outcome: 'would_block', would_action: 'block' }),
    ]);
    // 4. Key B is outside the policy.
    const monitoredB = await send(keyB);
    await expectEcho(monitoredB);
    expect(await guardrailLog(monitoredB)).toEqual([]);

    // 5. Enforce from the UI: key A is refused, key B still passes.
    expect(await editMode('Enforce')).toMatchObject({ mode: 'enforce' });
    await pushedPolicy({ mode: 'enforce' });
    const enforcedA = await send(keyA);
    await expectBlocked(enforcedA);
    expect(await guardrailLog(enforcedA)).toEqual([
      expect.objectContaining({ name: 'secret-detection', action: 'block', policy_name: policyName, mode: 'enforce', outcome: 'blocked' }),
    ]);
    await expectEcho(await send(keyB));

    // 6. An org-wide block wins over the key policy in monitor mode for the same check.
    // Body pinned from GW-E2E-004's org-config seed (AgentccOrgConfigViewSet.create).
    await actor.api.post(CONFIGS, {
      guardrails: { pipeline_mode: 'parallel', checks: { 'secret-detection': { enabled: true, action: 'block', confidence_threshold: 0.5 } } },
      change_description: `${policyName}-org-block`,
    });
    expect(await editMode('Monitor (log only)')).toMatchObject({ mode: 'monitor' });
    await pushedPolicy({ mode: 'monitor' });
    const orgBlockedA = await send(keyA);
    await expectBlocked(orgBlockedA);
    expect(await guardrailLog(orgBlockedA)).toEqual([
      expect.objectContaining({ name: 'secret-detection', action: 'block', policy_scope: 'org', outcome: 'blocked' }),
    ]);

    // 7. The documented API body works as written; an unknown key ID is refused and saves nothing.
    const docsApi = await request.newContext({
      baseURL: E2E.apiUrl,
      extraHTTPHeaders: { 'X-Api-Key': actor.apiKey, 'X-Secret-Key': actor.secretKey },
    });
    try {
      const docsBody = (keyId: string) => ({
        name: `payments-rollout-${suffix}`,
        scope: 'key',
        mode: 'monitor',
        applied_keys: [keyId],
        checks: [
          { name: 'secret-detection', enabled: true, action: 'block' },
          { name: 'pii-detection', enabled: true, action: 'log', config: { remediation: 'redact' } },
        ],
      });
      const count = async () => Number((await probe.pg<{ n: string }>(
        'SELECT count(*) AS n FROM agentcc_guardrail_policy WHERE organization_id = $1',
        [actor.organizationId],
      ))[0].n);
      const before = await count();
      const unknown = await docsApi.post(POLICIES, { data: docsBody('00000000-0000-4000-8000-000000000000') });
      expect(unknown.status(), await unknown.text()).toBe(400);
      expect(await count()).toBe(before);
      const docs = await docsApi.post(POLICIES, { data: docsBody(keyB.id) });
      expect(docs.status(), await docs.text()).toBe(200);
      const docsPolicy = (await docs.json() as Envelope<Policy>).result;
      expect(docsPolicy).toMatchObject({ scope: 'key', mode: 'monitor', applied_keys: [keyB.id] });
      const enforced = await docsApi.patch(`${POLICIES}${docsPolicy.id}/`, { data: { mode: 'enforce' } });
      expect(enforced.status(), await enforced.text()).toBe(200);
      expect((await enforced.json() as Envelope<Policy>).result.mode).toBe('enforce');
      expect(await count()).toBe(before + 1);
    } finally {
      await docsApi.dispose();
    }
  } finally {
    await testInfo.attach('blocked-external-urls', { body: JSON.stringify(blockedURLs), contentType: 'application/json' });
    await gateway.dispose();
    await context.close();
    await accounts.dispose();
  }
});
