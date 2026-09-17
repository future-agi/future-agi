import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { parse, stringify } from 'yaml';
import { inspectManagedMock, managedMockInspectionError, MOCK_BASE, MOCK_MODEL, registerMockModel, validateBackgroundMockMounts, validateMockEnvironment, validateMockRouting } from '../lib/managed-mock';
import { test as isolatedTest } from '../lib/mock-model-fixtures';
import type { TestActor } from '../lib/provisioning';
import type { StateProbe } from '../lib/state-probe';

const source = readFileSync(new URL('../stack/gateway.e2e.yaml', import.meta.url), 'utf8');

test('managed mock inspection errors expose bounded process diagnostics, never arbitrary output', () => {
  const secret = 'private-provider-credential';
  expect(managedMockInspectionError({ code: 'ETIMEDOUT', status: null, signal: 'SIGTERM', stderr: secret }).message)
    .toBe('STOP: managed mock read-only Docker inspection failed (code=ETIMEDOUT, status=unknown, signal=SIGTERM)');
  expect(managedMockInspectionError({ code: 'ETIMEDOUT', status: 0, stderr: secret, stdout: '{"passed":true}' }).message)
    .toBe('STOP: managed mock read-only Docker inspection failed (code=ETIMEDOUT, status=0, signal=unknown)');
  expect(managedMockInspectionError({ code: secret, status: 137, signal: secret, stderr: secret, stdout: secret }).message)
    .toBe('STOP: managed mock read-only Docker inspection failed (code=unknown, status=137, signal=unknown)');
  expect(managedMockInspectionError({ stderr: 'STOP: managed mock background attestation failed\n' }).message)
    .toBe('STOP: managed mock background attestation failed');
});

// These doubles do not contact the API/DB. In particular, an unsafe preflight
// must fail before custom_model.py's registration-time live completion.
function registrationDouble() {
  const calls: { path: string; body: unknown }[] = [];
  let reads = 0;
  const row = { id: 'model-id', provider: 'openai', workspace_id: 'workspace-id', key_config: 'encrypted-dummy-route' };
  const actor = { organizationId: 'org-id', workspaceId: 'workspace-id', api: {
    post: async (path: string, body: unknown) => { calls.push({ path, body }); return { result: { data: { id: row.id } } }; },
  } } as unknown as TestActor;
  const probe = { pg: async () => ++reads === 1 ? [] : [row] } as unknown as StateProbe;
  return { calls, actor, probe, row, readCount: () => reads };
}

test('managed mock safety accepts the exact configured route and registration wire', async () => {
  const fake = registrationDouble();
  const model = await registerMockModel(fake.actor, fake.probe, () => validateMockRouting(source));
  expect(model.id).toBe('model-id');
  expect(fake.calls).toEqual([{ path: '/model-hub/custom_models/create/', body: {
    model_provider: 'openai', model_name: 'gpt-4o', input_token_cost: 0, output_token_cost: 0,
    config_json: { key: 'local-dev-only-shared-secret-replace-me', api_base: 'http://agentcc-gateway:8080/v1' },
  } }]);
  fake.row.key_config = 'changed-route';
  await expect(model.assertReady()).rejects.toThrow('custom-model identity, credentials or route changed');
});

const unsafeRoutes: [string, (cfg: any) => void][] = [
  ['external upstream', c => { c.providers.openai.base_url = 'https://provider.invalid'; }],
  ['extra provider', c => { c.providers.other = structuredClone(c.providers.openai); }],
  ['real key', c => { c.providers.openai.api_key = 'not-the-dummy-key'; }],
  ['provider preset', c => { c.providers.openai.type = 'openai'; }],
  ['model map', c => { c.model_map = { 'gpt-4o': 'other' }; }],
  ['fallback routing', c => { c.routing = { model_fallbacks: { 'gpt-4o': ['other'] } }; }],
  ['control plane', c => { c.control_plane = { url: 'http://other.invalid' }; }],
  ['env expansion', c => { c.providers.openai.base_url = '${UNTRUSTED_UPSTREAM}'; }],
  ['unknown model', c => { c.providers.openai.models.push('another-model'); }],
];
for (const [name, mutate] of unsafeRoutes) {
  test(`managed mock rejects ${name} before registration`, async () => {
    const cfg = parse(source); mutate(cfg);
    const fake = registrationDouble();
    await expect(registerMockModel(fake.actor, fake.probe, () => validateMockRouting(stringify(cfg))))
      .rejects.toThrow('STOP: managed mock');
    expect(fake.calls).toEqual([]);
    expect(fake.readCount()).toBe(0);
  });
}

test('managed mock rejects duplicate YAML keys before registration', async () => {
  const fake = registrationDouble();
  await expect(registerMockModel(fake.actor, fake.probe, () => validateMockRouting(`${source}\nproviders: {}\n`)))
    .rejects.toThrow('invalid gateway YAML');
  expect(fake.calls).toEqual([]);
  expect(fake.readCount()).toBe(0);
});

test('managed mock rejects YAML aliases before registration', async () => {
  const cfg = parse(source);
  cfg.providers.other = cfg.providers.openai;
  const fake = registrationDouble();
  await expect(registerMockModel(fake.actor, fake.probe, () => validateMockRouting(stringify(cfg))))
    .rejects.toThrow('aliased gateway YAML');
  expect(fake.calls).toEqual([]);
  expect(fake.readCount()).toBe(0);
});

// Offline-only capability cases; deliberately use base test with no fixtures.
const backgroundEnvironment = {
  AGENTCC_INTERNAL_API_KEY: 'local-dev-only-shared-secret-replace-me',
  AGENTCC_ADMIN_TOKEN: 'local-dev-only-admin-token-replace-me',
  AGENTCC_INTERNAL_URL: 'http://agentcc-gateway:8080',
  AGENTCC_GATEWAY_INTERNAL_URL: 'http://agentcc-gateway:8080', MODEL_SERVING_URL: 'http://mock-llm:8080',
  ENV_TYPE: 'local', EE_LICENSE_KEY: '', NO_STARTUP_DB_MUTATIONS: 'true', OTEL_ENABLED: 'false',
  FUTURE_AGI_TELEMETRY_DISABLED: 'true', TEMPORAL_HOST: 'temporal:7233', TEMPORAL_NAMESPACE: 'default',
  DJANGO_SETTINGS_MODULE: 'tfc.settings.settings', MAILGUN_API_KEY: '',
  TEMPORAL_ALL_QUEUES: 'true', TEMPORAL_EXCLUDED_QUEUES: 'simulation_runner',
};

const gatewayMounts = [
  { Type: 'bind', Source: '/fixture/gateway.e2e.yaml', Destination: '/app/config.yaml', RW: false },
  { Type: 'bind', Source: '/dev/null', Destination: '/app/Vertex_AI_Creds.json', RW: false },
];

test('managed mock background accepts only the exact gateway credential-suppression mount', () => {
  validateBackgroundMockMounts('agentcc-gateway', gatewayMounts);
  validateBackgroundMockMounts('agentcc-gateway', [...gatewayMounts].reverse());
});

for (const [name, mutate] of [
  ['real credential source', (mounts: typeof gatewayMounts) => { mounts[1].Source = '/real/credentials.json'; }],
  ['writable credential suppression', (mounts: typeof gatewayMounts) => { mounts[1].RW = true; }],
  ['non-bind credential suppression', (mounts: typeof gatewayMounts) => { mounts[1].Type = 'volume'; }],
  ['wrong credential destination', (mounts: typeof gatewayMounts) => { mounts[1].Destination = '/app/other.json'; }],
  ['additional mount', (mounts: typeof gatewayMounts) => { mounts.push({ ...mounts[1], Destination: '/extra' }); }],
  ['duplicate suppression mount', (mounts: typeof gatewayMounts) => { mounts.push({ ...mounts[1] }); }],
  ['missing suppression mount', (mounts: typeof gatewayMounts) => { mounts.pop(); }],
  ['missing config mount', (mounts: typeof gatewayMounts) => { mounts.shift(); }],
  ['writable config mount', (mounts: typeof gatewayMounts) => { mounts[0].RW = true; }],
] as const) {
  test(`managed mock background rejects ${name}`, () => {
    const mounts = structuredClone(gatewayMounts); mutate(mounts);
    expect(() => validateBackgroundMockMounts('agentcc-gateway', mounts)).toThrow('STOP: managed mock');
  });
}

test('managed mock background never permits the gateway exception on the mock server', () => {
  validateBackgroundMockMounts('mock-llm', [
    { Type: 'bind', Source: '/fixture/server.mjs', Destination: '/srv/server.mjs', RW: false },
  ]);
  expect(() => validateBackgroundMockMounts('mock-llm', gatewayMounts)).toThrow('STOP: managed mock');
});

test('managed mock background credential env is allowed only with verified gateway suppression', () => {
  const env = { ...backgroundEnvironment, GOOGLE_APPLICATION_CREDENTIALS: '/app/Vertex_AI_Creds.json' };
  validateMockEnvironment('agentcc-gateway', env, true, gatewayMounts);
  expect(() => validateMockEnvironment('agentcc-gateway', env, true)).toThrow('STOP: managed mock');
  for (const service of ['backend', 'worker', 'mock-llm']) {
    expect(() => validateMockEnvironment(service, env, true, gatewayMounts)).toThrow('STOP: managed mock');
  }
  expect(() => validateMockEnvironment('agentcc-gateway', { ...env, GOOGLE_APPLICATION_CREDENTIALS: '/other.json' },
    true, gatewayMounts)).toThrow('STOP: managed mock');
  for (const changed of [{ Source: '/real/credentials.json' }, { RW: true }, { Type: 'volume' },
    { Destination: '/other.json' }]) {
    expect(() => validateMockEnvironment('agentcc-gateway', env, true,
      [gatewayMounts[0], { ...gatewayMounts[1], ...changed }])).toThrow('STOP: managed mock');
  }
});

test('managed mock background opt-in rejects old routes and missing required settings', () => {
  validateMockRouting(source, true);
  const old = parse(source);
  old.providers.openai.models = old.providers.openai.models.filter((name: string) => name !== 'turing_flash');
  validateMockRouting(stringify(old)); // Judge-only callers retain the old supported contract.
  expect(() => validateMockRouting(stringify(old), true)).toThrow('missing background capability');
  validateMockEnvironment('worker', backgroundEnvironment, true);
  for (const key of Object.keys(backgroundEnvironment)) {
    const env: Record<string, string> = { ...backgroundEnvironment }; delete env[key];
    expect(() => validateMockEnvironment('worker', env, true), key).toThrow('STOP: managed mock');
  }
  validateMockEnvironment('agentcc-gateway', backgroundEnvironment, true);
  expect(() => validateMockEnvironment('agentcc-gateway', {}, true)).toThrow('required agentcc-gateway');
});

for (const [name, value] of Object.entries({
  MODEL_SERVING_URL: 'https://serving.invalid', AGENTCC_INTERNAL_URL: 'https://gateway.invalid',
  AGENTCC_INTERNAL_API_KEY: 'real-key', EE_LICENSE_KEY: 'license', ENV_TYPE: 'production',
  FUTURE_AGI_TELEMETRY_DISABLED: 'false', NO_STARTUP_DB_MUTATIONS: 'false', OTEL_ENABLED: 'true',
  TEMPORAL_HOST: 'remote:7233', TEMPORAL_NAMESPACE: 'another', TEMPORAL_EXCLUDED_QUEUES: 'agent_compass',
  HTTPS_PROXY: 'http://proxy.invalid', NODE_OPTIONS: '--require=other', LD_PRELOAD: 'other', PYTHONPATH: '/other',
  OPENAI_API_KEY: 'real-key', GOOGLE_APPLICATION_CREDENTIALS: '/key.json', AWS_SESSION_TOKEN: 'token',
  MAILGUN_API_KEY: 'e2e-mock', SLACK_WEBHOOK_CHANNEL: 'https://notify.invalid',
  DEPLOYMENT_TELEMETRY_SLACK_WEBHOOK: 'https://notify.invalid', SENTRY_ENABLED: 'true', SENTRY_DSN: 'remote',
  ERROR_LOGS_WEBHOOK: 'https://notify.invalid', MIX_PANEL_TOKEN: 'token',
  EMAIL_BACKEND: 'django.core.mail.backends.smtp.EmailBackend',
})) {
  test(`managed mock background rejects unsafe ${name} before registration and reads`, async () => {
    const fake = registrationDouble();
    await expect(registerMockModel(fake.actor, fake.probe, async () => {
      await Promise.resolve();
      validateMockEnvironment('worker', { ...backgroundEnvironment, [name]: value }, true);
    })).rejects.toThrow('STOP: managed mock');
    expect(fake.calls).toEqual([]);
    expect(fake.readCount()).toBe(0);
  });
}

test('managed mock background rechecks an awaited safety veto immediately before dispatch', async () => {
  const fake = registrationDouble();
  let safe = true;
  let checks = 0;
  const model = await registerMockModel(fake.actor, fake.probe, async () => {
    await Promise.resolve(); checks++;
    validateMockEnvironment('worker', { ...backgroundEnvironment,
      MODEL_SERVING_URL: safe ? 'http://mock-llm:8080' : 'http://other:8080' }, true);
  });
  const reads = fake.readCount();
  const registrations = fake.calls.length;
  safe = false;
  await expect(model.assertReady()).rejects.toThrow('MODEL_SERVING_URL');
  expect(checks).toBe(3);
  expect(fake.calls).toHaveLength(registrations);
  expect(fake.readCount()).toBe(reads);
});

test('managed stack routing has runtime labels, network and read-only source proof', async ({}, testInfo) => {
  const receipt = inspectManagedMock();
  expect(receipt.services.map(s => s.service)).toEqual([
    'agentcc-gateway', 'mock-llm', 'backend', 'worker', 'frontend', 'postgres', 'clickhouse']);
  expect(receipt.gatewaySha).toMatch(/^[a-f0-9]{64}$/);
  expect(receipt.mockSha).toMatch(/^[a-f0-9]{64}$/);
  await testInfo.attach('verified-managed-mock', { contentType: 'application/json', body: JSON.stringify(receipt) });
});

// Standalone post-recreation attestation. Deliberately outside the offline
// `--grep 'managed mock'` selection; no actor/model fixture or dispatch.
test('managed background routing has source, serving health and owned poller proof', async ({}, testInfo) => {
  const receipt = inspectManagedMock({ evalBackground: true });
  await testInfo.attach('verified-managed-background-routing', {
    contentType: 'application/json', body: JSON.stringify(receipt),
  });
  expect(receipt.background?.capability).toBe('eval-clustering');
});

// Two independent tests with the same fixture/model and --workers=1 prove a
// fresh org each time: registration refuses any pre-existing org+gpt-4o row.
// No module-level prior-test state, cleanup or browser is needed.
for (const ordinal of [1, 2]) {
  isolatedTest(`test-scoped mock model registration ${ordinal} remains isolated`, async ({ actor, probe, mockModel }, testInfo) => {
    await mockModel.assertReady();
    const rows = await probe.pg<{ id: string; organization_id: string; workspace_id: string; user_model_id: string }>(
      'SELECT id,organization_id,workspace_id,user_model_id FROM model_hub_customaimodel WHERE organization_id=$1 AND deleted=false',
      [actor.organizationId]);
    expect(rows).toEqual([{ id: mockModel.id, organization_id: actor.organizationId,
      workspace_id: actor.workspaceId, user_model_id: MOCK_MODEL }]);
    await testInfo.attach('isolated-model-scope', { contentType: 'application/json',
      body: JSON.stringify({ rows, route: MOCK_BASE }) });
  });
}
