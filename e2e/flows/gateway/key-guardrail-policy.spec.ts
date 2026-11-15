import { request, type APIResponse, type Page } from '@playwright/test';
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
// handlers.go parses the namespace and emits CacheStatus; plugins/cache/plugin.go
// uses miss/hit_exact and skip_key_policy (selected policies never read or store).
const CACHE_NAMESPACE_HEADER = 'x-agentcc-cache-namespace';
const CACHE_HEADER = 'x-agentcc-cache';
// The gateway posts each request log to the backend webhook; ingestion is synchronous,
// the poll only absorbs the gateway's flush interval.
const LOG_VISIBLE = POLL.ASYNC_JOB;
const UI_READY = 60_000;

type Envelope<T> = { result: T };
type Policy = { id: string; name: string; scope: string; mode: string; applied_keys: string[]; gateway_synced: boolean };
type KeyPolicyWire = { id: string; name: string; mode: string; key_ids: string[]; checks: Record<string, { action: string }> };
type GatewayConfigs = { data: Record<string, { cache?: { enabled: boolean }; guardrails?: { key_policies?: KeyPolicyWire[] } }> };
// agentcc/models/request_log.py and services/log_ingestion.py: api_key_id is the
// gateway key ID, not the API key model's primary key; cache_hit is a PG boolean.
type RequestLog = { request_id: string; api_key_id: string; status_code: number; cache_hit: boolean; guardrail_results: GuardrailEntry[] | null };
type GuardrailEntry = { name: string; action: string; policy_name?: string; policy_scope?: string; mode?: string; outcome?: string; would_action?: string };

test('GW-E2E-007: an admin applies a guardrail policy to one gateway key, first monitoring and then enforcing it', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'GW-E2E-007',
    area: 'gateway',
    userGoal: 'An admin monitors and then enforces a guardrail on one API key without cached responses bypassing its policy, disabling caching for unselected keys, or weakening org-wide guardrails',
    steps: [
      'seed a local mock provider, two gateway keys and enabled organization caching with a minted cache namespace',
      'open Guardrails Policies, create a Secret Detection block policy for key A in Monitor mode, picking the key by name; the dialog offers no Org-wide scope',
      'send the same fake AWS-key prompt twice with key A and twice with key B: A is monitored on each uncached echo, while B repeats from the exact cache',
      'switch the policy to Enforce in the dialog; the identical key A request is refused with content_blocked and key B still gets an exact-cache echo',
      'add an org-wide Secret Detection block with caching disabled and set the key policy back to Monitor; key A is still refused',
      'create the documented key policy body through the API; an unknown key ID is refused without creating a policy',
    ],
    backendChecks: [
      'the gateway admin config carries enabled caching and the UI-saved key policy mode, block action and key A gateway key ID during the cache phase',
      'both identical key A Monitor requests return skip_key_policy and have distinct uncached logs with exactly one would_block policy result and no triggered response header',
      'unselected key B returns miss then hit_exact, and remains hit_exact after Enforce; all three exact request logs have no guardrail entry and cache_hit false, true, true',
      'after the UI Enforce save, the identical key A request gets 403 content_blocked, no choices and an uncached blocked key-policy log',
      'an org-wide block wins over a key policy in monitor mode for the same check',
      'the documented request body is accepted verbatim, and an unknown key ID returns 400 without a new agentcc_guardrail_policy row',
    ],
  }),
}, async ({ browser, probe }, testInfo) => {
  test.setTimeout(900_000);
  // Three UI/config groups (360s), two batched log polls (120s), seven requests
  // (210s), seeding/documented API (90s), and 120s headroom.
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
  const cacheNamespace = `e2e-gw7-cache-${suffix}`;
  // AgentccOrgConfigWriteSerializer creates a new immutable config version;
  // org_config_defaults.py pins TTL in seconds and max_entries.
  const cacheConfig = await actor.api.post<Envelope<{ id: string }>>(CONFIGS, {
    cache: { enabled: true, default_ttl: 1800, max_entries: 10000 },
    change_description: `${policyName}-cache`,
  });
  await testInfo.attach('seeded-ids', {
    body: JSON.stringify({ organization: actor.organizationId, provider: credential.result.id, keyA: keyA.id, keyAGateway: keyA.gateway_key_id, keyB: keyB.id, keyBGateway: keyB.gateway_key_id, policyName, cacheNamespace, cacheConfig: cacheConfig.result.id }),
    contentType: 'application/json',
  });
  const requestIds: Record<string, string> = {};
  const gateway = await request.newContext({ baseURL: E2E.gatewayUrl, timeout: 30_000 });
  try {
    const prompt = `${policyName} deploy with ${AWS_KEY}`;
    const send = async (label: string, key: CreatedKey) => {
      const res = await gateway.post('/v1/chat/completions', {
        headers: { Authorization: `Bearer ${key.key}`, [CACHE_NAMESPACE_HEADER]: cacheNamespace },
        data: { model: `${provider}/gpt-4o-mini`, messages: [{ role: 'user', content: prompt }] },
      });
      const id = res.headers()[REQUEST_ID_HEADER];
      expect(id).toBeTruthy();
      expect(Object.values(requestIds)).not.toContain(id);
      requestIds[label] = id;
      return res;
    };
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
    // Batch by the exact response IDs: one flush budget, exact row set and state,
    // not a count that could pass on another request or a missing monitoring result.
    const expectLogs = async (expected: Array<Omit<RequestLog, 'guardrail_results'> & { guardrail_results: unknown[] }>) => {
      const ids = expected.map(row => row.request_id).sort();
      await expect.poll(async () => {
        const rows = await probe.pg<RequestLog>(
          'SELECT request_id, api_key_id, status_code, cache_hit, guardrail_results FROM agentcc_request_log WHERE organization_id = $1 AND request_id = ANY($2::text[]) ORDER BY request_id',
          [actor.organizationId, ids],
        );
        return rows.map(row => ({ ...row, guardrail_results: row.guardrail_results ?? [] }));
      }, LOG_VISIBLE).toEqual([...expected].sort((a, b) => a.request_id.localeCompare(b.request_id)));
    };
    const pushedPolicy = async (expected: { mode: string; cacheEnabled: boolean }) => {
      await expect.poll(async () => {
        const res = await gateway.get('/-/orgs/configs', { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } });
        expect(res.status()).toBe(200);
        const wire = await res.json() as GatewayConfigs;
        const org = wire.data[actor.organizationId];
        const found = org?.guardrails?.key_policies?.find(p => p.name === policyName);
        return found ? { mode: found.mode, key_ids: found.key_ids, action: found.checks['secret-detection']?.action, cacheEnabled: org.cache?.enabled } : null;
      }, POLL.ASYNC_JOB).toEqual({ mode: expected.mode, key_ids: [keyA.gateway_key_id], action: 'block', cacheEnabled: expected.cacheEnabled });
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

    await testInfo.attach('policy-id', { body: JSON.stringify({ policy: created.id }), contentType: 'application/json' });
    // 2. Both cache opt-in and the policy reach the gateway.
    await pushedPolicy({ mode: 'monitor', cacheEnabled: true });

    // 3. Selected policies always bypass caches, even Monitor: both identical
    // requests execute upstream and produce fresh monitoring evidence.
    const monitoredA = await send('A1', keyA);
    await expectEcho(monitoredA);
    expect(monitoredA.headers()[CACHE_HEADER]).toBe('skip_key_policy');
    const repeatedA = await send('A2', keyA);
    await expectEcho(repeatedA);
    expect(repeatedA.headers()[CACHE_HEADER]).toBe('skip_key_policy');

    // 4. Key B is outside the policy and still benefits from exact caching.
    const monitoredB = await send('B1', keyB);
    await expectEcho(monitoredB);
    expect(monitoredB.headers()[CACHE_HEADER]).toBe('miss');
    const repeatedB = await send('B2', keyB);
    await expectEcho(repeatedB);
    expect(repeatedB.headers()[CACHE_HEADER]).toBe('hit_exact');

    // 5. Enforce from the same real UI controls: the identical selected request
    // cannot reuse an earlier echo; the unselected cached entry remains usable.
    expect(await editMode('Enforce')).toMatchObject({ mode: 'enforce' });
    await pushedPolicy({ mode: 'enforce', cacheEnabled: true });
    const enforcedA = await send('A3', keyA);
    await expectBlocked(enforcedA);
    expect(enforcedA.headers()[CACHE_HEADER] ?? '').not.toMatch(/^hit_/);
    const enforcedB = await send('B3', keyB);
    await expectEcho(enforcedB);
    expect(enforcedB.headers()[CACHE_HEADER]).toBe('hit_exact');
    const monitorResult = expect.objectContaining({ name: 'secret-detection', action: 'log', policy_name: policyName, policy_scope: 'key', mode: 'monitor', outcome: 'would_block', would_action: 'block' });
    await expectLogs([
      { request_id: requestIds.A1, api_key_id: keyA.gateway_key_id, status_code: 200, cache_hit: false, guardrail_results: [monitorResult] },
      { request_id: requestIds.A2, api_key_id: keyA.gateway_key_id, status_code: 200, cache_hit: false, guardrail_results: [monitorResult] },
      { request_id: requestIds.B1, api_key_id: keyB.gateway_key_id, status_code: 200, cache_hit: false, guardrail_results: [] },
      { request_id: requestIds.B2, api_key_id: keyB.gateway_key_id, status_code: 200, cache_hit: true, guardrail_results: [] },
      { request_id: requestIds.A3, api_key_id: keyA.gateway_key_id, status_code: 403, cache_hit: false, guardrail_results: [
        expect.objectContaining({ name: 'secret-detection', action: 'block', policy_name: policyName, policy_scope: 'key', mode: 'enforce', outcome: 'blocked' }),
      ] },
      { request_id: requestIds.B3, api_key_id: keyB.gateway_key_id, status_code: 200, cache_hit: true, guardrail_results: [] },
    ]);

    // 6. An org-wide block wins over the key policy in monitor mode for the same check.
    // Body pinned from GW-E2E-004's org-config seed (AgentccOrgConfigViewSet.create).
    await actor.api.post(CONFIGS, {
      guardrails: { pipeline_mode: 'parallel', checks: { 'secret-detection': { enabled: true, action: 'block', confidence_threshold: 0.5 } } },
      change_description: `${policyName}-org-block`,
    });
    expect(await editMode('Monitor (log only)')).toMatchObject({ mode: 'monitor' });
    // Omitted cache uses the serializer's disabled default: this tail preserves
    // org-wide precedence coverage, not an org-wide cache-invalidation claim.
    await pushedPolicy({ mode: 'monitor', cacheEnabled: false });
    const orgBlockedA = await send('A4', keyA);
    await expectBlocked(orgBlockedA);
    await expectLogs([
      { request_id: requestIds.A4, api_key_id: keyA.gateway_key_id, status_code: 403, cache_hit: false, guardrail_results: [
        expect.objectContaining({ name: 'secret-detection', action: 'block', policy_scope: 'org', outcome: 'blocked' }),
      ] },
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
    await testInfo.attach('request-ids', { body: JSON.stringify({ cacheNamespace, requests: requestIds }), contentType: 'application/json' });
    await testInfo.attach('blocked-external-urls', { body: JSON.stringify(blockedURLs), contentType: 'application/json' });
    await gateway.dispose();
    await context.close();
    await accounts.dispose();
  }
});
