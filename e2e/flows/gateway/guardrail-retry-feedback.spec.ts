import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { inspectManagedMock } from '../../lib/managed-mock';

// frontend/src/sections/gateway/guardrails/GuardrailManagementSection.jsx:
// Rules opens ConfigTab, whose Save & Activate calls useCreateOrgConfig.
const GUARDRAILS_PAGE = '/dashboard/gateway/guardrails';
// futureagi/agentcc/urls.py and providers/hooks/useOrgConfig.js.
const ORG_CONFIG_PATH = '/agentcc/org-configs/';
const ACTIVE_CONFIG_PATH = '/agentcc/org-configs/active/';
// GuardrailConfigTab.jsx's rule catalog and GuardrailCheckDialog.jsx's labels.
const CHECK_NAME = 'data-leakage-prevention';
const CHECK_LABEL = 'Data Leakage Prevention';
const DEBUG_LABEL = 'Include safe retry details in request logs';
// Existing GW-E2E-002 admin-read pattern; this is the managed stack's public
// dummy token, not a production credential or a new authentication fixture.
const GATEWAY_ADMIN_TOKEN = 'local-dev-only-admin-token-replace-me';
const GATEWAY_CONFIG_PATH = '/-/orgs/configs';
// e2e/stack/mock-llm/server.mjs: reply() and the nonstreaming usage fingerprint.
const MODEL = 'gpt-4o-mini';
const ORIGINAL_USAGE = { prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 };
const REPAIRED_USAGE = { prompt_tokens: 14, completion_tokens: 14, total_tokens: 28 };
// Independent expected output, not derived from any returned response:
// pipeline/reflexion.go:reflectionRequest supplies the feedback template;
// guardrails/reflexion.go:postBlock prefixes the rule name; leakage/leakage.go
// orders path_traversal before system_info_leak. The stock mock adds "echo: ".
const EXPECTED_REPAIR = 'echo: The preceding answer failed the response checks below. '
  + 'Produce a corrected answer to the original request. Do not repeat the rejected answer.\n'
  + 'data-leakage-prevention: Data leakage detected: path_traversal, system_info_leak';
// e2e/README.md: one page-load budget per short UI step, plus action headroom.
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;

interface Rule {
  name: string;
  stage: string;
  mode: string;
  action: string;
  threshold: number;
  enabled: boolean;
  config: {
    stage: string;
    on_block: string;
    reflect_max_attempts: number;
    reflect_debug: boolean;
  };
  _policy?: string;
}
interface OrgConfig {
  id: string;
  organization: string;
  version: number;
  is_active: boolean;
  guardrails: { rules: Rule[] };
}
interface GatewayConfigList {
  data: Record<string, {
    providers: Record<string, { base_url?: string; api_format: string }>;
    guardrails?: { checks: Record<string, {
      enabled: boolean;
      action: string;
      confidence_threshold: number;
      config: Rule['config'];
    }> };
  }>;
}
interface Completion {
  choices: { index: number; message: { role: string; content: string }; finish_reason: string }[];
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
}

// Attest before provisioning and again before each model-capable request. A
// remote or unattested attach target fails closed; no real provider is called.
test.beforeAll(() => { inspectManagedMock(); });

test('GW-E2E-005: response retry settings persist and return only the first passing answer', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'GW-E2E-005',
    area: 'gateway',
    userGoal: 'A developer enables response retry with feedback, keeps that setting after reload, and receives only the first passing answer from the gateway',
    steps: [
      'provision an isolated organization with a local mock provider and a uniquely named gateway key',
      'send an inert two-pattern prompt through the gateway and read its exact unguarded mock echo',
      'open Gateway Guardrails Rules and configure Data Leakage Prevention as Response, Block and Retry with feedback, with two additional attempts and safe retry details',
      'Save the check, then Save & Activate the organization configuration',
      'reload Rules and reopen the check to verify the persisted controls',
      'repeat the original prompt through the gateway and receive only the first passing feedback echo',
    ],
    backendChecks: [
      'Before activation, the tenant-scoped gateway returns exactly the minted mock echo with one-generation usage.',
      'Save & Activate persists the exact post-response Block/reflect/2/debug rule under the actor organization and pushes the corresponding check to the gateway; API, PG and reopened UI agree.',
      'The activated rule rejects the original candidate and returns exactly the first passing feedback echo after one retry, with aggregate two-generation usage and no rejected sentinel.',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Scope provisioning 120 + attestation/seeding 60 + five UI steps x 75
  // + two gateway calls x 60 + teardown/headroom 25 = 720 seconds.
  test.setTimeout(720_000);
  // Active org guardrails must not affect the worker actor's later gateway or
  // eval flows. scopeActors is the existing test-owned isolation fixture.
  const owner = scopes.ownerA;
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const marker = `e2e-reflect-${suffix}`;
  // Inert text only: the mock echoes it, never reads a file or executes code.
  // Two native leakage categories score 1, exceeding the default threshold .8.
  const prompt = `${marker} /etc/passwd stack trace`;
  const expectedRule: Rule = {
    name: CHECK_NAME,
    stage: 'post',
    mode: 'sync',
    action: 'block',
    threshold: 0.8,
    enabled: true,
    config: { stage: 'post', on_block: 'reflect', reflect_max_attempts: 2, reflect_debug: true },
  };
  await testInfo.attach('managed-mock', {
    contentType: 'application/json',
    body: JSON.stringify(inspectManagedMock()),
  });

  // Wire bodies: AgentccProviderCredentialCreateSerializer and
  // AgentccAPIKeyCreateSerializer. Both writes are scoped to this fresh org.
  // Omit base_url: the serializer stores ''. providers/override.go inherits
  // the attested operator route for this provider ID, but uses the tenant's
  // own credential. Keep AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS=false: explicitly
  // naming the mock's private URL would correctly fail that safety boundary.
  const provider = await owner.api.post<{
    result: { id: string; base_url: string; gateway_synced: boolean };
  }>(
    '/agentcc/provider-credentials/',
    {
      provider_name: 'openai',
      display_name: marker,
      credentials: { api_key: 'e2e-mock' },
      api_format: 'openai',
      models_list: [MODEL],
      extra_config: { api_path_prefix: '/v1' },
    },
  );
  expect(provider.result.gateway_synced).toBe(true);
  expect(provider.result.base_url).toBe('');
  const key = await owner.api.post<{
    result: { id: string; gateway_key_id: string; key: string; organization: string };
  }>('/agentcc/api-keys/', {
    name: marker,
    allowed_models: [MODEL],
    allowed_providers: ['openai'],
  });
  expect(key.result.organization).toBe(owner.organizationId);
  await testInfo.attach('seeded', {
    contentType: 'application/json',
    body: JSON.stringify({
      organizationId: owner.organizationId,
      workspaceId: owner.workspaceId,
      providerCredentialId: provider.result.id,
      apiKeyId: key.result.id,
      gatewayKeyId: key.result.gateway_key_id,
      marker,
      prompt,
    }),
  });

  const gateway = {
    get: (route: string, options: { headers?: Record<string, string> } = {}) => fetch(
      new URL(route, E2E.gatewayUrl), { headers: options.headers },
    ),
    post: (route: string, options: { headers?: Record<string, string>; data: unknown }) => fetch(
      new URL(route, E2E.gatewayUrl), {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...options.headers },
        body: JSON.stringify(options.data), redirect: 'manual',
      },
    ),
  };
  // Inspect only this org's nonsecret routing fields before any completion.
  // The endpoint and authentication are already exercised by GW-E2E-002.
  const routingResponse = await gateway.get(GATEWAY_CONFIG_PATH, {
    headers: { Authorization: `Bearer ${GATEWAY_ADMIN_TOKEN}` },
  });
  expect(routingResponse.status).toBe(200);
  const routing = (await routingResponse.json() as GatewayConfigList).data[owner.organizationId];
  expect(Object.keys(routing.providers)).toEqual(['openai']);
  // tenant.ProviderConfig omits the empty base_url on admin reads. Combined
  // with inspectManagedMock's exact YAML route, this proves no tenant URL
  // overrides the mock-only operator destination.
  expect(Object.keys(routing.providers.openai)).not.toContain('base_url');
  expect(routing.providers.openai.api_format).toBe('openai');

  await test.step('Read the unguarded tenant-scoped mock echo', async () => {
    inspectManagedMock();
    const response = await gateway.post('/v1/chat/completions', {
      headers: { Authorization: `Bearer ${key.result.key}` },
      data: { model: MODEL, stream: false, messages: [{ role: 'user', content: prompt }] },
    });
    expect(response.status).toBe(200);
    const body = await response.json() as Completion;
    expect(body.choices).toEqual([{
      index: 0,
      message: { role: 'assistant', content: `echo: ${prompt}` },
      finish_reason: 'stop',
    }]);
    expect(body.usage).toEqual(ORIGINAL_USAGE);
    await testInfo.attach('before-activation', {
      contentType: 'application/json',
      body: JSON.stringify({ choices: body.choices, usage: body.usage }),
    });
  }, { timeout: UI_READY });

  const context = await scopes.openContext(browser, owner);
  const page = await context.newPage();
  const card = page.locator('.MuiCard-root').filter({
    has: page.getByText(CHECK_LABEL, { exact: true }),
  });
  // GuardrailConfigTab.jsx:renderCheckCard has exactly one unnamed edit
  // button; the other button on a configured card is explicitly named Reset.
  const edit = card.getByRole('button', { name: '', exact: true });
  const dialog = page.getByRole('dialog', { name: `Configure: ${CHECK_LABEL}`, exact: true });

  await test.step('Open Gateway Guardrails Rules', async () => {
    await page.goto(GUARDRAILS_PAGE);
    await expect(page.getByRole('tab', { name: 'Rules', exact: true })).toBeVisible({ timeout: UI_READY });
    await page.getByRole('tab', { name: 'Rules', exact: true }).click();
    await expect(card).toBeVisible({ timeout: UI_READY });
  }, { timeout: STEP_BUDGET });

  await test.step('Choose an enabled response Block check', async () => {
    await edit.click();
    await expect(dialog).toBeVisible({ timeout: UI_READY });
    await dialog.getByLabel('Enabled', { exact: true }).setChecked(true);
    await dialog.getByRole('combobox', { name: 'Action', exact: false }).click();
    await page.getByRole('option', { name: 'Block', exact: true }).click();
    await dialog.getByRole('combobox', { name: 'Check stage', exact: false }).click();
    await page.getByRole('option', { name: 'Response', exact: true }).click();
    await expect(dialog.getByRole('combobox', { name: 'When a response is blocked', exact: false }))
      .toHaveText('Reject response', { timeout: UI_READY });
  }, { timeout: STEP_BUDGET });

  await test.step('Save two additional attempts with safe retry details', async () => {
    await dialog.getByRole('combobox', { name: 'When a response is blocked', exact: false }).click();
    await page.getByRole('option', { name: 'Retry with feedback', exact: true }).click();
    await dialog.getByRole('spinbutton', { name: 'Additional attempts', exact: true }).fill('2');
    await dialog.getByLabel(DEBUG_LABEL, { exact: true }).setChecked(true);
    await dialog.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(dialog).toBeHidden({ timeout: UI_READY });
    await expect(page.getByRole('button', { name: 'Save & Activate', exact: true }))
      .toBeEnabled({ timeout: UI_READY });
  }, { timeout: STEP_BUDGET });

  await test.step('Activate and read the exact rule from API, Postgres and gateway', async () => {
    const savedResponse = page.waitForResponse(response =>
      new URL(response.url()).pathname === ORG_CONFIG_PATH && response.request().method() === 'POST',
    { timeout: UI_READY });
    await page.getByRole('button', { name: 'Save & Activate', exact: true }).click();
    const response = await savedResponse;
    expect(response.status()).toBe(200);
    const submitted = response.request().postDataJSON() as { guardrails: { rules: Rule[] } };
    expect(submitted.guardrails.rules).toEqual([expectedRule]);
    const saved = await response.json() as { result: OrgConfig & { gateway_synced: boolean } };
    expect(saved.result.gateway_synced).toBe(true);
    expect(saved.result.organization).toBe(owner.organizationId);
    expect(saved.result.is_active).toBe(true);
    expect(saved.result.guardrails.rules).toEqual([{ ...expectedRule, _policy: expect.any(String) }]);
    await testInfo.attach('activated-config', {
      contentType: 'application/json',
      body: JSON.stringify({
        organizationId: owner.organizationId,
        configId: saved.result.id,
        version: saved.result.version,
        rules: saved.result.guardrails.rules,
      }),
    });

    // useOrgConfig reads this endpoint; it is not StateProbe.apiList's table
    // envelope. All writes/pushes finish before the save responds: no CDC wait.
    const active = await owner.api.get<{ result: OrgConfig }>(ACTIVE_CONFIG_PATH);
    expect(active.result.id).toBe(saved.result.id);
    expect(active.result.organization).toBe(owner.organizationId);
    expect(active.result.is_active).toBe(true);
    expect(active.result.guardrails.rules).toEqual([{ ...expectedRule, _policy: expect.any(String) }]);
    // AgentccOrgConfig.Meta.db_table; bind both captured config ID and owner.
    const rows = await probe.pg<{
      id: string; organization_id: string; version: number; is_active: boolean; rules: Rule[];
    }>(`SELECT id, organization_id, version, is_active, guardrails->'rules' AS rules
        FROM agentcc_org_config WHERE id = $1 AND organization_id = $2 AND deleted = false`,
    [saved.result.id, owner.organizationId]);
    expect(rows).toEqual([{
      id: saved.result.id,
      organization_id: owner.organizationId,
      version: saved.result.version,
      is_active: true,
      rules: [{ ...expectedRule, _policy: expect.any(String) }],
    }]);

    const pushedResponse = await gateway.get(GATEWAY_CONFIG_PATH, {
      headers: { Authorization: `Bearer ${GATEWAY_ADMIN_TOKEN}` },
    });
    expect(pushedResponse.status).toBe(200);
    const pushed = (await pushedResponse.json() as GatewayConfigList).data[owner.organizationId];
    // e2e/stack/gateway.e2e.yaml (hash-attested by inspectManagedMock) has no
    // guardrails block, so global static rules stay disabled; this managed
    // rule must still run once the control plane pushes it.
    // config_push._transform_guardrails converts rules to this exact map.
    expect(pushed.guardrails?.checks).toEqual({
      [CHECK_NAME]: {
        enabled: true,
        action: 'block',
        confidence_threshold: 0.8,
        config: expectedRule.config,
      },
    });
    expect(Object.keys(pushed.providers)).toEqual(['openai']);
    expect(Object.keys(pushed.providers.openai)).not.toContain('base_url');
    const persistedProvider = await owner.api.get<{ result: { base_url: string } }>(
      `/agentcc/provider-credentials/${provider.result.id}/`,
    );
    expect(persistedProvider.result.base_url).toBe('');
    await expect(page.getByRole('button', { name: 'Save & Activate', exact: true }))
      .toBeHidden({ timeout: UI_READY });
  }, { timeout: STEP_BUDGET });

  await test.step('Reload and reopen the saved response retry controls', async () => {
    await page.reload();
    await expect(card).toBeVisible({ timeout: UI_READY });
    await edit.click();
    await expect(dialog).toBeVisible({ timeout: UI_READY });
    await expect(dialog.getByLabel('Enabled', { exact: true })).toBeChecked({ timeout: UI_READY });
    // MUI names each select by its label plus the selected value.
    await expect(dialog.getByRole('combobox', { name: 'Action', exact: false }))
      .toHaveText('Block', { timeout: UI_READY });
    await expect(dialog.getByRole('combobox', { name: 'Check stage', exact: false }))
      .toHaveText('Response', { timeout: UI_READY });
    await expect(dialog.getByRole('combobox', { name: 'When a response is blocked', exact: false }))
      .toHaveText('Retry with feedback', { timeout: UI_READY });
    await expect(dialog.getByRole('spinbutton', { name: 'Additional attempts', exact: true }))
      .toHaveValue('2', { timeout: UI_READY });
    await expect(dialog.getByLabel(DEBUG_LABEL, { exact: true })).toBeChecked({ timeout: UI_READY });
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  }, { timeout: STEP_BUDGET });

  await test.step('Return only the first passing candidate after one retry', async () => {
    inspectManagedMock();
    const response = await gateway.post('/v1/chat/completions', {
      headers: { Authorization: `Bearer ${key.result.key}` },
      data: { model: MODEL, stream: false, messages: [{ role: 'user', content: prompt }] },
    });
    expect(response.status).toBe(200);
    // server/reflexion.go:setReflexionHeaders; retry count is additional
    // generations, so one retry is strictly less than the configured cap two.
    expect(response.headers.get('x-agentcc-reflexion-attempts')).toBe('1');
    expect(response.headers.get('x-agentcc-provider-attempts')).toBe('2');
    const returnedBody = await response.text();
    const body = JSON.parse(returnedBody) as Completion;
    expect(body.choices).toEqual([{
      index: 0,
      message: { role: 'assistant', content: EXPECTED_REPAIR },
      finish_reason: 'stop',
    }]);
    expect(body.usage).toEqual(REPAIRED_USAGE);
    expect(returnedBody).not.toContain(marker);
    expect(returnedBody).not.toContain('/etc/passwd');
    expect(returnedBody).not.toContain('stack trace');
    await testInfo.attach('after-activation', {
      contentType: 'application/json',
      body: JSON.stringify({
        choices: body.choices,
        usage: body.usage,
        retryAttempts: response.headers.get('x-agentcc-reflexion-attempts'),
        providerAttempts: response.headers.get('x-agentcc-provider-attempts'),
      }),
    });
  }, { timeout: UI_READY });
});
