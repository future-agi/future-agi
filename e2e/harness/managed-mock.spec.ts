import { WALK_MOCKS, WALK_COMMANDS, WALK_PORTS, WALK_SOURCES } from '../lib/walk-mock-contract';
import { test, expect, type TestInfo } from '@playwright/test';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse, stringify } from 'yaml';
import { E2E } from '../lib/env';
import { inspectManagedMock, managedMockInspectionError, MOCK_BASE, MOCK_MODEL, NEW_ORG_DEFAULT, registerMockModel, validateBackgroundMockMounts, validateMockEnvironment, validateMockRouting } from '../lib/managed-mock';
import { standaloneServices, validateStandaloneAppEnvironment } from '../lib/managed-mock-standalone';
import { test as isolatedTest } from '../lib/mock-model-fixtures';
import type { TestActor } from '../lib/provisioning';
import type { StateProbe } from '../lib/state-probe';
import { EXPORT_ENDPOINTS } from '../lib/export-sinks';
import { MCP_MOCK, PUBLIC_NETWORK } from '../lib/public-network';
import { walkGatewayProfile, MCP_CATALOG_DELTA, MCP_DELTA, MCP_SIGNIN_DELTA, mcpCatalogConfig, mcpConfig, mcpSigninConfig,
  PROFILE_FILES, PROFILE_RUNS_MCP_MOCK, r7altConfig, validateMcpCatalogProfile, validateMcpProfile, validateMcpSigninProfile,
  validateR7altProfile, type WalkGatewayProfile } from '../lib/profile';
import { createHash } from 'node:crypto';
import { isIPv4 } from 'node:net';
import { endianness } from 'node:os';

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
  // The one managed provider (fagi-managed) stays on the mock with the dummy key.
  ['external managed upstream', c => { c.providers['fagi-managed'].base_url = 'https://openrouter.ai/api'; }],
  ['managed provider without a base_url', c => { delete c.providers['fagi-managed'].base_url; }],
  ['real managed key', c => { c.providers['fagi-managed'].api_key = 'sk-or-not-the-dummy'; }],
  ['second managed provider', c => { c.providers['fagi-managed-2'] = structuredClone(c.providers['fagi-managed']); }],
  ['managed models list', c => { c.providers['fagi-managed'].models = ['anthropic/claude-haiku-4.5']; }],
  ['live catalog refresh', c => { c.managed.catalog_refresh = '1m'; }],
  ['managed Redis ledger', c => { c.managed.require_redis = true; }],
  ['managed provider without its section', c => { delete c.managed; }],
  ['managed section without its provider', c => { delete c.providers['fagi-managed']; }],
  // The guardrail plugin is on with no rule of the file's own; a file rule could name
  // a webhook or an external provider.
  ['a guardrail rule', c => { c.guardrails.rules = [{ name: 'webhook', stage: 'pre', action: 'block' }]; }],
  ['a guardrail setting', c => { c.guardrails.default_timeout = '1s'; }],
  ['guardrails as a list', c => { c.guardrails = [{ enabled: true }]; }],
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
  DJANGO_SETTINGS_MODULE: 'tfc.settings.e2e', MAILGUN_API_KEY: '',
  TEMPORAL_ALL_QUEUES: 'true', TEMPORAL_EXCLUDED_QUEUES: 'simulation_runner',
};

test('managed mock allows only exact local Slack fixtures on application services', () => {
  const slack = {
    SLACK_CLIENT_ID: 'e2e-client', SLACK_CLIENT_SECRET: 'e2e-secret',
    SLACK_OAUTH_AUTHORIZE_URL: 'http://localhost:28081/oauth/v2/authorize',
    SLACK_REDIRECT_URI: 'http://localhost:8100/integrations/slack/callback/',
    SLACK_API_URL: 'http://mock-slack:8080/api',
  };
  validateMockEnvironment('backend', { ...backgroundEnvironment, ...slack }, true);
  validateMockEnvironment('worker', { ...backgroundEnvironment, SLACK_API_URL: slack.SLACK_API_URL }, true);
  expect(() => validateMockEnvironment('backend', { ...backgroundEnvironment, ...slack,
    SLACK_API_URL: 'https://slack.com/api' }, true)).toThrow('SLACK_CLIENT_ID');
  expect(() => validateMockEnvironment('backend', { ...backgroundEnvironment, ...slack,
    SLACK_CLIENT_SECRET: 'private' }, true)).toThrow('SLACK_CLIENT_SECRET');
  expect(() => validateMockEnvironment('app', { ...backgroundEnvironment, ...slack }, true))
    .toThrow('SLACK_CLIENT_ID');
});

for (const evalBackground of [false, true]) {
  test(`managed mock accepts only the optional E2E webhook secret (background=${evalBackground})`, async () => {
    const optionalEnvironments: Record<string, string>[] = [{}, { AGENTCC_WEBHOOK_SECRET: '' },
      { AGENTCC_WEBHOOK_SECRET: 'e2e-agentcc-webhook-secret' }];
    for (const service of ['backend', 'worker', 'agentcc-gateway', 'mock-llm']) {
      for (const optional of optionalEnvironments) {
        validateMockEnvironment(service, { ...backgroundEnvironment, ...optional }, evalBackground);
      }
    }
    const fake = registrationDouble();
    await registerMockModel(fake.actor, fake.probe, () => {
      validateMockEnvironment('backend', { ...backgroundEnvironment,
        AGENTCC_WEBHOOK_SECRET: 'e2e-agentcc-webhook-secret' }, evalBackground);
      validateMockRouting(source, evalBackground);
    });
    expect(fake.calls).toHaveLength(1);
  });

  for (const [name, override] of [
    ['non-mock webhook secret', { AGENTCC_WEBHOOK_SECRET: 'private-webhook-secret' }],
    ['webhook secret prefix', { AGENTCC_WEBHOOK_SECRET: 'e2e-agentcc-webhook-secret-extra' }],
    ['lookalike gateway key', { AGENTCC_WEBHOOK_SECRET_EXTRA: 'e2e-agentcc-webhook-secret' }],
    ['external gateway', { AGENTCC_INTERNAL_URL: 'https://gateway.invalid' }],
    ['provider credential', { OPENAI_API_KEY: 'private-provider-key' }],
  ] as [string, Record<string, string>][]) {
    test(`managed mock webhook allowance rejects ${name} before registration (background=${evalBackground})`, async () => {
      const fake = registrationDouble();
      await expect(registerMockModel(fake.actor, fake.probe, () => {
        validateMockEnvironment('backend', { ...backgroundEnvironment,
          AGENTCC_WEBHOOK_SECRET: 'e2e-agentcc-webhook-secret', ...override }, evalBackground);
      })).rejects.toThrow('STOP: managed mock');
      expect(fake.calls).toEqual([]);
      expect(fake.readCount()).toBe(0);
    });
  }
}

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
    { Type: 'bind', Source: '/fixture/wire.mjs', Destination: '/srv/wire.mjs', RW: false },
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

for (const evalBackground of [false, true]) {
  test(`managed mock refuses a private-provider or foreign control-plane gateway (background=${evalBackground})`, () => {
    for (const service of ['backend', 'worker', 'agentcc-gateway']) {
      validateMockEnvironment(service, { ...backgroundEnvironment, AGENTCC_CONTROL_PLANE_URL: 'http://backend',
        AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS: 'false' }, evalBackground);
      for (const override of [{ AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS: 'true' },
        { AGENTCC_CONTROL_PLANE_URL: 'https://control-plane.invalid' }] as Record<string, string>[]) {
        expect(() => validateMockEnvironment(service, { ...backgroundEnvironment, ...override }, evalBackground))
          .toThrow('unsupported gateway override');
      }
    }
  });
}

// The new-org default (lib/profile.ts, lib/managed-mock.ts NEW_ORG_DEFAULT): the
// API's setting is accepted as exactly "1", and only on a run that declares it
// (E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS=1); anywhere else it is refused.
test('managed mock accepts the new-org default only as 1 on a run that declares it', () => {
  const declared = process.env.E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS;
  const withDefault = (value: string) => ({ ...backgroundEnvironment, [NEW_ORG_DEFAULT]: value });
  try {
    delete process.env.E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS;
    for (const service of ['backend', 'worker']) {
      validateMockEnvironment(service, withDefault(''), true);
      expect(() => validateMockEnvironment(service, withDefault('1'), true)).toThrow('unsupported gateway override');
    }
    expect(() => validateStandaloneAppEnvironment({ ...composedStandaloneEnvironment(), [NEW_ORG_DEFAULT]: '1' }, false))
      .toThrow('unsupported gateway override');

    process.env.E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS = '1';
    for (const service of ['backend', 'worker']) {
      validateMockEnvironment(service, withDefault('1'), true);
      expect(() => validateMockEnvironment(service, withDefault('true'), true)).toThrow('unsupported gateway override');
    }
    validateStandaloneAppEnvironment({ ...composedStandaloneEnvironment(), [NEW_ORG_DEFAULT]: '1' }, false);
  } finally {
    if (declared === undefined) delete process.env.E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS;
    else process.env.E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS = declared;
  }
});

test('managed mock keeps default-on and MCP environment admission independent', () => {
  const declared = process.env.E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS;
  const profile = process.env.E2E_GATEWAY_PROFILE;
  try {
    process.env.E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS = '1';
    process.env.E2E_GATEWAY_PROFILE = 'mcp';
    const env = { ...composedStandaloneEnvironment(), [NEW_ORG_DEFAULT]: '1',
      AGENTCC_MCP_STATE_KEYS: ['e2e-only', 'mcp-state-key', 'not-a-secret', '0000'].join('-') };
    validateStandaloneAppEnvironment(env, false);
    expect(() => validateStandaloneAppEnvironment({ ...env, [NEW_ORG_DEFAULT]: 'true' }, false))
      .toThrow('unsupported gateway override');
    expect(() => validateStandaloneAppEnvironment({ ...env, AGENTCC_MCP_STATE_KEYS: 'unlisted' }, false))
      .toThrow('unsupported gateway override');
    process.env.E2E_GATEWAY_PROFILE = 'stock';
    expect(() => validateStandaloneAppEnvironment(env, false)).toThrow('unsupported gateway override');
    process.env.E2E_GATEWAY_PROFILE = 'mcp';
    delete process.env.E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS;
    expect(() => validateStandaloneAppEnvironment(env, false)).toThrow('unsupported gateway override');
  } finally {
    if (declared === undefined) delete process.env.E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS;
    else process.env.E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS = declared;
    if (profile === undefined) delete process.env.E2E_GATEWAY_PROFILE;
    else process.env.E2E_GATEWAY_PROFILE = profile;
  }
});

test('managed mock background refuses a sandbox provider credential', () => {
  expect(() => validateMockEnvironment('worker', { ...backgroundEnvironment, DAYTONA_API_KEY: 'e2e-mock' }, true))
    .toThrow('credential override');
  expect(() => validateMockEnvironment('worker', { ...backgroundEnvironment, E2B_API_KEY: 'e2e-mock' }, true))
    .toThrow('credential override');
});

// What Compose gives Standalone's `app`: docker-compose.yml's environment
// interpolated from standalone-e2e.env alone (the overlay resets env_file).
function composedStandaloneEnvironment(): Record<string, string> {
  const vars = Object.fromEntries(readFileSync(new URL('../stack/standalone-e2e.env', import.meta.url), 'utf8')
    .split('\n').filter(line => /^[A-Z_][A-Z0-9_]*=/.test(line))
    .map(line => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1)]));
  const compose = parse(readFileSync(new URL('../../docker-compose.yml', import.meta.url), 'utf8'));
  return Object.fromEntries(Object.entries(compose.services.app.environment as Record<string, unknown>)
    .map(([key, value]) => [key, String(value ?? '').replace(/\$\{([A-Z0-9_]+)(?::?-([^}]*))?\}/g,
      (_, name: string, fallback?: string) => vars[name] || fallback || '')]));
}

for (const evalBackground of [false, true]) {
  test(`managed mock accepts the Standalone app environment Compose builds (background=${evalBackground})`, () => {
    validateStandaloneAppEnvironment(composedStandaloneEnvironment(), evalBackground);
  });
}

for (const [name, override, reason] of [
  ['real provider key', { OPENAI_API_KEY: 'private-provider-key' }, 'non-mock provider key'],
  ['Sentry DSN', { SENTRY_DSN: 'https://sentry.invalid/1' }, 'credential override'],
  ['proxy', { HTTPS_PROXY: 'http://proxy.invalid' }, 'proxy/preload override'],
  ['mail sender domain', { MAILGUN_SENDER_DOMAIN: 'mail.invalid' }, 'credential override'],
  ['private provider URLs', { AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS: 'true' }, 'unsupported gateway override'],
  ['foreign control plane', { AGENTCC_CONTROL_PLANE_URL: 'https://control-plane.invalid' }, 'unsupported gateway override'],
  ['unknown gateway setting', { AGENTCC_BASE_URL: 'https://gateway.invalid' }, 'unsupported gateway override'],
  ['nonlocal mail', { EMAIL_BACKEND: 'django.core.mail.backends.smtp.EmailBackend' }, 'nonlocal email backend'],
  ['Sentry', { SENTRY_ENABLED: 'true' }, 'Sentry must be disabled'],
  ['telemetry', { FUTURE_AGI_TELEMETRY_DISABLED: 'false' }, 'required app FUTURE_AGI_TELEMETRY_DISABLED'],
] as [string, Record<string, string>, string][]) {
  test(`managed mock refuses a Standalone app with a ${name} override`, () => {
    expect(() => validateStandaloneAppEnvironment({ ...composedStandaloneEnvironment(), ...override }, false))
      .toThrow(reason);
  });
}

test('managed mock refuses a Standalone app without the telemetry opt-out', () => {
  const env = composedStandaloneEnvironment();
  delete env.FUTURE_AGI_TELEMETRY_DISABLED;
  expect(() => validateStandaloneAppEnvironment(env, false)).toThrow('required app FUTURE_AGI_TELEMETRY_DISABLED');
});

// The same pins as Distributed's backend and worker (validateMockEnvironment),
// with Standalone's loopback gateway and Temporal.
for (const [key, evalBackground] of [['AGENTCC_ADMIN_TOKEN', false], ['EE_LICENSE_KEY', true],
  ['MAILGUN_API_KEY', true]] as [string, boolean][]) {
  test(`managed mock refuses a Standalone app without ${key} (background=${evalBackground})`, () => {
    const env = composedStandaloneEnvironment();
    delete env[key];
    expect(() => validateStandaloneAppEnvironment(env, evalBackground)).toThrow(`required app ${key} mismatch`);
  });
}

// A stand-in `docker` on PATH answers the read-only calls the inspection makes
// (bin/e2e compose passes through to it), so the whole inspection runs offline.
const fakeDockerScript = `#!/bin/sh
if [ -n "$FAKE_DOCKER_FAIL" ]; then echo "$FAKE_DOCKER_FAIL $*" >&2; exit 1; fi
case " $* " in
  *" context inspect "*) cat "$FAKE_DOCKER/context.json" ;;
  *" network inspect "*) for last; do :; done; cat "$FAKE_DOCKER/network-$last.json" ;;
  *" compose "*" config "*) cat "$FAKE_DOCKER/config.json" ;;
  *" compose "*" ps "*) cat "$FAKE_DOCKER/ps.txt" ;;
  *" inspect "*) for last; do :; done
    if [ -f "$FAKE_DOCKER/inspect-$last.json" ]; then cat "$FAKE_DOCKER/inspect-$last.json"; else cat "$FAKE_DOCKER/containers.json"; fi ;;
  *) echo "unexpected docker call" >&2; exit 64 ;;
esac
`;
const repoFile = (path: string) => fileURLToPath(new URL(`../${path}`, import.meta.url));
type FakeContainer = { service: string; ports?: Record<string, string>; cmd?: string[]; entrypoint?: string[];
  env?: Record<string, string>; extraHosts?: string[]; mounts?: [string, string][]; image?: string; publicOnly?: boolean };

function fakeStack(stack: 'standalone' | 'distributed', profile: WalkGatewayProfile = 'stock') {
  const project = stack === 'standalone' ? 'futureagi-e2e-standalone' : 'futureagi-e2e';
  const network = `${project}_default`;
  const publicNetwork = `${project}_${PUBLIC_NETWORK.key}`;
  const members: readonly string[] = PUBLIC_NETWORK.members[stack];
  const pinned = PUBLIC_NETWORK.pinned as Record<string, { ip: string; alias: string }>;
  const port = (url: string) => new URL(url).port;
  const gatewayConfig = repoFile(PROFILE_FILES[profile].replace(/^e2e\//, ''));
  // H-MCP: under the mcp profile, agentcc-mcpmock on e2e-public only.
  const mcpMock: FakeContainer[] = PROFILE_RUNS_MCP_MOCK[profile] ? [{ service: MCP_MOCK.service, image: MCP_MOCK.image,
    cmd: [...MCP_MOCK.command], entrypoint: [...MCP_MOCK.entrypoint], env: { PATH: '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
      E2E_MCP_MOCK_AUTH: 'bearer:e2e-mcp-upstream-bearer-not-a-secret' },
    publicOnly: true }] : [];
  const mock: FakeContainer = { service: 'mock-llm', cmd: ['node', '/srv/server.mjs'], entrypoint: ['docker-entrypoint.sh'],
    ports: { '8080/tcp': port(E2E.mockLlmUrl) }, mounts: [[repoFile('stack/mock-llm/server.mjs'), '/srv/server.mjs'], [repoFile('stack/mock-llm/wire.mjs'), '/srv/wire.mjs']] };
  const sink: FakeContainer = { service: 'webhook-sink', cmd: ['node', '/srv/server.mjs'], entrypoint: ['docker-entrypoint.sh'],
    ports: { '8080/tcp': port(E2E.webhookSinkUrl) }, mounts: [[repoFile('stack/webhook-sink/server.mjs'), '/srv/server.mjs']] };
  const mail: FakeContainer = { service: 'smtp-sink', ports: { '8025/tcp': port(E2E.mailSinkUrl) } };
  const mockSlack: FakeContainer = { service: 'mock-slack', cmd: ['node', '/srv/server.mjs'], entrypoint: ['docker-entrypoint.sh'],
    ports: { '8080/tcp': port(E2E.slackMockUrl) },
    mounts: [[repoFile('stack/mock-slack/server.mjs'), '/srv/server.mjs']] };
  const stores: FakeContainer[] = [{ service: 'postgres', ports: { '5432/tcp': port(E2E.pgUrl) } },
    { service: 'clickhouse', ports: { '8123/tcp': port(E2E.chUrl) } }];
  const services: FakeContainer[] = stack === 'standalone' ? [{ service: 'app', env: composedStandaloneEnvironment(),
    ports: { '3000/tcp': port(E2E.appUrl), '8000/tcp': port(E2E.apiUrl), '8080/tcp': port(E2E.gatewayUrl) },
    extraHosts: ['code-executor:127.0.0.1', 'agentcc-gateway:127.0.0.1'],
    mounts: [[gatewayConfig, '/etc/futureagi/secrets/agentcc.yaml'], ['/dev/null', '/etc/futureagi/secrets/vertex.json']],
  }, mock, sink, mail, ...stores, ...mcpMock] : [{ service: 'agentcc-gateway', cmd: ['--config', '/app/config.yaml'],
    entrypoint: ['/app/agentcc-gateway'], ports: { '8080/tcp': port(E2E.gatewayUrl) },
    mounts: [[gatewayConfig, '/app/config.yaml']] }, mock, mockSlack, sink, mail,
  { service: 'backend', ports: { '80/tcp': port(E2E.apiUrl) }, env: { OPENAI_API_KEY: 'e2e-mock' } },
  { service: 'worker' }, { service: 'frontend', ports: { '80/tcp': port(E2E.appUrl) } }, ...stores];
  services.push(...WALK_MOCKS.filter(service => !PROFILE_RUNS_MCP_MOCK[profile] || service !== MCP_MOCK.service)
    .map(service => ({ service, cmd: WALK_COMMANDS[service], ports: WALK_PORTS[service],
    mounts: WALK_SOURCES.filter(([s]) => s === service).map(([, dest, source]) => [source, dest] as [string, string]) })));
  const containers = services.map((c, i) => ({
    Id: (i + 1).toString(16).repeat(64), Image: `sha256:${(i + 1).toString(16).repeat(64)}`,
    State: { Running: true, StartedAt: '2999-01-01T00:00:00Z' },
    Config: { Labels: { 'com.docker.compose.service': c.service, 'com.docker.compose.project': project },
      Env: Object.entries({
        ...(['app', 'backend', 'worker', 'agentcc-gateway', 'agentcc-gateway-2'].includes(c.service) ? {
          AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED: 'false', AGENTCC_MCP_CLIENT_OAUTH_ENABLED: 'false',
          AGENTCC_MCP_K2_APPS_ENABLED: 'false',
        } : {}),
        ...(['app', 'backend', 'worker'].includes(c.service) ? { AGENTCC_MCP_CLIENT_INSTALLS_ENABLED: 'false' } : {}),
        ...c.env,
      }).map(([key, value]) => `${key}=${value}`), Cmd: c.cmd ?? [], Entrypoint: c.entrypoint ?? [],
      ...(c.image ? { Image: c.image } : {}) },
    HostConfig: { ExtraHosts: c.extraHosts ?? null },
    Mounts: (c.mounts ?? []).map(([Source, Destination]) => ({ Type: 'bind', Source, Destination, RW: false })),
    NetworkSettings: { Networks: {
      ...(c.publicOnly ? {} : { [network]: { NetworkID: 'e2e-network', Aliases: [c.service], IPAddress: '172.18.0.2' } }),
      ...(members.includes(c.service) || c.publicOnly ? { [publicNetwork]: { NetworkID: 'e2e-public-network',
        Aliases: [c.service, ...(pinned[c.service] ? [pinned[c.service].alias] : [])],
        IPAddress: pinned[c.service]?.ip ?? `11.88.0.${20 + i}` } } : {}) },
      Ports: Object.fromEntries(Object.entries(c.ports ?? {}).map(([p, host]) => [p, [{ HostIp: '127.0.0.1', HostPort: host }]])) },
  }));
  return {
    'context.json': [{ Endpoints: { docker: { Host: 'unix:///var/run/docker.sock' } } }],
    'config.json': { name: project, networks: { default: { name: network }, [PUBLIC_NETWORK.key]: { name: publicNetwork } },
      services: {} },
    'ps.txt': containers.map(c => c.Id).join('\n'),
    'containers.json': containers,
    'network-e2e-network.json': [{ Driver: 'bridge', Labels: { 'com.docker.compose.project': project, 'com.docker.compose.network': 'default' } }],
    'network-e2e-public-network.json': [{ Driver: 'bridge', Internal: false,
      IPAM: { Driver: 'default', Config: [{ Subnet: PUBLIC_NETWORK.subnet, Gateway: '11.88.0.1' }] },
      Labels: { 'com.docker.compose.project': project, 'com.docker.compose.network': PUBLIC_NETWORK.key },
      Containers: Object.fromEntries(containers.filter(c => members.includes(c.Config.Labels['com.docker.compose.service'])
        || c.Config.Labels['com.docker.compose.service'] === MCP_MOCK.service)
        .map(c => [c.Id, { Name: c.Config.Labels['com.docker.compose.service'],
          IPv4Address: `${c.NetworkSettings.Networks[publicNetwork].IPAddress}/24` }])) }],
  } as Record<string, any>;
}

function withFakeDocker<T>(label: string, stack: 'standalone' | 'distributed', files: Record<string, unknown>,
  inspect: () => T, fail = ''): T {
  // 708 add.78: the fake Docker state carries the stacks' synthetic env (keys, passwords). It
  // is a test INPUT, so it lives under the OS temp directory, never in the output directory
  // that lib/trace-hygiene/global-teardown.ts audits as evidence; `label` only names it.
  const dir = mkdtempSync(join(tmpdir(), `e2e-fake-${basename(label)}-`));
  mkdirSync(dir, { recursive: true });
  writeFileSync(`${dir}/docker`, fakeDockerScript, { mode: 0o755 });
  for (const [name, body] of Object.entries(files)) writeFileSync(`${dir}/${name}`, typeof body === 'string' ? body : JSON.stringify(body));
  const saved = Object.fromEntries(['PATH', 'DOCKER_CONTEXT', 'DOCKER_HOST', 'E2E_STACK', 'FAKE_DOCKER', 'FAKE_DOCKER_FAIL',
    'E2E_GATEWAY_PROFILE'].map(key => [key, process.env[key]]));
  Object.assign(process.env, { PATH: `${dir}:${process.env.PATH}`, DOCKER_CONTEXT: 'e2e-fake', E2E_STACK: stack,
    FAKE_DOCKER: dir, FAKE_DOCKER_FAIL: fail });
  delete process.env.DOCKER_HOST;
  delete process.env.E2E_GATEWAY_PROFILE; // a case declares its profile inside `inspect`
  try {
    return inspect();
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
}

const offlineEndpoints = () => [E2E.appUrl, E2E.apiUrl, E2E.gatewayUrl, E2E.pgUrl, E2E.chUrl, E2E.slackMockUrl,
  E2E.mockLlmUrl, E2E.webhookSinkUrl, E2E.mailSinkUrl].every(url => new URL(url).hostname === 'localhost');
const inspectedServices = (stack: 'standalone' | 'distributed', profile: WalkGatewayProfile = 'stock') => (stack === 'standalone'
  ? ['app', 'mock-llm', 'webhook-sink', 'smtp-sink', 'postgres', 'clickhouse', ...WALK_MOCKS]
  : ['agentcc-gateway', 'mock-llm', 'mock-slack', 'webhook-sink', 'smtp-sink', 'backend', 'worker', 'frontend', 'postgres', 'clickhouse', ...WALK_MOCKS]);
const fakeService = (files: Record<string, any>, service: string) =>
  files['containers.json'].find((c: any) => c.Config.Labels['com.docker.compose.service'] === service);

for (const stack of ['standalone', 'distributed'] as const) {
  test(`managed mock inspects a well-formed ${stack} stack offline`, ({}, testInfo) => {
    test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
    const receipt = withFakeDocker(testInfo.outputPath('docker'), stack, fakeStack(stack), () => inspectManagedMock());
    expect(receipt.services.map(s => s.service)).toEqual(inspectedServices(stack));
    expect(receipt.publicNetworkId).toBe('e2e-public-network');
  });

  test(`managed mock refuses a ${stack} network the project does not own`, ({}, testInfo) => {
    test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
    const files = fakeStack(stack);
    files['network-e2e-network.json'][0].Driver = 'host';
    expect(() => withFakeDocker(testInfo.outputPath('docker'), stack, files, () => inspectManagedMock()))
      .toThrow('STOP: managed mock unmanaged network');
  });

  test(`managed mock ${stack} inspection failures never echo arguments or Docker output`, ({}, testInfo) => {
    test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
    expect(() => withFakeDocker(testInfo.outputPath('docker'), stack, fakeStack(stack), () => inspectManagedMock(),
      'private-provider-credential')).toThrow(/^STOP: managed mock read-only Docker inspection failed \(code=unknown, status=1, signal=unknown\)$/);
  });
}

// H0's network rule (rel-r7 PLANS.md H0): the public-classified network holds
// exactly the declared members, as the project's own internal bridge on exactly
// its subnet, with the pinned addresses and aliases. One case per rule, each
// asserting that rule's own STOP message.
type Stack = 'standalone' | 'distributed';
const publicCases: [string, string, (files: Record<string, any>, stack: Stack) => void, string | ((stack: Stack) => string)][] = [
  ['N1', 'a service on a third network', (files) => {
    fakeService(files, 'mock-llm').NetworkSettings.Networks.other_net = { NetworkID: 'other', Aliases: ['mock-llm'], IPAddress: '10.9.0.2' };
  }, 'service mock-llm network or DNS override'],
  ['N2', 'the public network attached to a non-member (postgres)', (files, stack) => {
    const pg = fakeService(files, 'postgres');
    const name = Object.keys(fakeService(files, 'mock-llm').NetworkSettings.Networks).find(n => n.endsWith('_e2e-public'))!;
    pg.NetworkSettings.Networks[name] = { NetworkID: 'e2e-public-network', Aliases: ['postgres'], IPAddress: '11.88.0.40' };
    files['network-e2e-public-network.json'][0].Containers[pg.Id] = { Name: `${stack}-postgres` };
  }, 'service postgres network or DNS override'],
  ['N3', 'an internal-only public network', (files) => {
    files['network-e2e-public-network.json'][0].Internal = true;
  }, 'public network must support loopback-published browser access'],
  ['N4', 'a public network on another subnet', (files) => {
    files['network-e2e-public-network.json'][0].IPAM.Config[0].Subnet = '11.89.0.0/24';
  }, 'public network subnet is not 11.88.0.0/24'],
  ['N4b', 'a public network with a second subnet', (files) => {
    files['network-e2e-public-network.json'][0].IPAM.Config.push({ Subnet: '10.0.0.0/24' });
  }, 'public network subnet is not 11.88.0.0/24'],
  ['N5', 'a public network the project does not label', (files) => {
    files['network-e2e-public-network.json'][0].Labels['com.docker.compose.project'] = 'another-project';
  }, 'unmanaged public network'],
  ['N6', 'mock-llm on the public network at another address', (files) => {
    const mock = fakeService(files, 'mock-llm');
    mock.NetworkSettings.Networks[Object.keys(mock.NetworkSettings.Networks).find(n => n.endsWith('_e2e-public'))!].IPAddress = '11.88.0.99';
  }, 'service mock-llm public address or alias mismatch'],
  ['N6b', 'mock-llm on the public network without its alias', (files) => {
    const mock = fakeService(files, 'mock-llm');
    mock.NetworkSettings.Networks[Object.keys(mock.NetworkSettings.Networks).find(n => n.endsWith('_e2e-public'))!].Aliases = ['mock-llm'];
  }, 'service mock-llm public address or alias mismatch'],
  ['N6c', 'the webhook alias also defined on the default network', (files) => {
    const sink = fakeService(files, 'webhook-sink');
    sink.NetworkSettings.Networks[Object.keys(sink.NetworkSettings.Networks).find(n => n.endsWith('_default'))!].Aliases
      .push('hooks.e2e-public.test');
  }, 'service webhook-sink public address or alias mismatch'],
  ['N6d', 'an app container answering to the mock\'s public alias', (files, stack) => {
    const app = fakeService(files, stack === 'standalone' ? 'app' : 'agentcc-gateway');
    app.NetworkSettings.Networks[Object.keys(app.NetworkSettings.Networks).find(n => n.endsWith('_e2e-public'))!].Aliases
      .push('llm.e2e-public.test');
  }, (stack) => `service ${stack === 'standalone' ? 'app' : 'agentcc-gateway'} public address or alias mismatch`],
  ['N7', 'a container the inspection does not list attached to the public network', (files) => {
    files['network-e2e-public-network.json'][0].Containers['f'.repeat(64)] = { Name: 'unlisted' };
  }, 'public network has an unlisted member'],
];
for (const stack of ['standalone', 'distributed'] as const) {
  for (const [id, name, mutate, reason] of publicCases) {
    test(`managed mock ${id} refuses a ${stack} stack with ${name}`, ({}, testInfo) => {
      test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
      const files = fakeStack(stack);
      mutate(files, stack);
      expect(() => withFakeDocker(testInfo.outputPath('docker'), stack, files, () => inspectManagedMock()))
        .toThrow(`STOP: managed mock ${typeof reason === 'string' ? reason : reason(stack)}`);
    });
  }
}

// H0's profile rule (rel-r7 PLANS.md H0, H5): the declared gateway profile picks
// the file the gateway must mount; r7alt is Standalone only and must be exactly
// the stock file plus R7ALT_DELTA; the routing contract stays the stock file's.
const stockSource = readFileSync(repoFile('stack/gateway.e2e.yaml'), 'utf8');
const r7altSource = readFileSync(repoFile('stack/gateway.r7alt.yaml'), 'utf8');
const declare = (profile: string | undefined, inspect: () => ReturnType<typeof inspectManagedMock>) => () => {
  if (profile !== undefined) process.env.E2E_GATEWAY_PROFILE = profile;
  return inspect();
};
const mountR7alt = (files: Record<string, any>) => {
  const app = fakeService(files, 'app');
  app.Mounts.find((m: any) => m.Destination === '/etc/futureagi/secrets/agentcc.yaml').Source = repoFile('stack/gateway.r7alt.yaml');
};

test('managed mock P3 inspects a Standalone stack with r7alt declared and mounted offline', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  const files = fakeStack('standalone');
  mountR7alt(files);
  const receipt = withFakeDocker(testInfo.outputPath('docker'), 'standalone', files, declare('r7alt', inspectManagedMock));
  expect(receipt.profile).toBe('r7alt');
  expect(receipt.gatewaySha).toBe(createHash('sha256').update(readFileSync(repoFile('stack/gateway.r7alt.yaml'))).digest('hex'));
  const stock = withFakeDocker(testInfo.outputPath('docker'), 'standalone', fakeStack('standalone'), declare(undefined, inspectManagedMock));
  expect(stock.profile).toBe('stock');
});

test('managed mock P4 the committed r7alt file is exactly stock plus the declared delta', () => {
  validateR7altProfile(stockSource, r7altSource);
  expect(parse(r7altSource)).toEqual(r7altConfig(stockSource));
  expect(PROFILE_FILES).toEqual({ stock: 'e2e/stack/gateway.e2e.yaml', r7alt: 'e2e/stack/gateway.r7alt.yaml',
    mcp: 'e2e/stack/gateway.mcp.yaml', 'mcp-catalog': 'e2e/stack/gateway.mcp-catalog.yaml',
    'mcp-signin': 'e2e/stack/gateway.mcp-signin.yaml', 'mcp-otel': 'e2e/stack/gateway.mcp-otel.yaml',
    pricefeed: 'e2e/stack/gateway.pricefeed.yaml', 'onekey-cat': 'e2e/stack/gateway.onekey-cat.yaml',
    'cloud-mode': 'e2e/stack/gateway.e2e.yaml' });
});

test('managed mock N8 refuses the r7alt mount when no profile is declared', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  const files = fakeStack('standalone');
  mountR7alt(files);
  expect(withFakeDocker.bind(null, testInfo.outputPath('docker'), 'standalone', files, declare(undefined, inspectManagedMock)))
    .toThrow('STOP: managed mock gateway profile stock expects e2e/stack/gateway.e2e.yaml mounted');
});

test('managed mock N9 refuses r7alt declared with the stock file mounted', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  expect(withFakeDocker.bind(null, testInfo.outputPath('docker'), 'standalone', fakeStack('standalone'), declare('r7alt', inspectManagedMock)))
    .toThrow('STOP: managed mock gateway profile r7alt expects e2e/stack/gateway.r7alt.yaml mounted');
});

for (const [name, mutate] of [
  ['an extra routing section', (c: any) => { c.routing = { model_fallbacks: { 'gpt-4o': ['gpt-4o-mini'] } }; }],
  ['logging at debug', (c: any) => { c.logging.level = 'debug'; }],
  ['a delta value changed', (c: any) => { c.server.read_timeout = '8s'; }],
  ['a stock key dropped', (c: any) => { delete c.providers['fagi-managed']; }],
  ['the cache off', (c: any) => { delete c.cache; }],
] as [string, (c: any) => void][]) {
  test(`managed mock N10 refuses an r7alt file with ${name}`, () => {
    const cfg = parse(r7altSource); mutate(cfg);
    expect(() => validateR7altProfile(stockSource, stringify(cfg)))
      .toThrow('STOP: managed mock gateway profile r7alt is not stock plus the declared delta');
  });
}

test('managed mock N10b refuses an r7alt file with environment expansion or aliases', () => {
  const cfg = parse(r7altSource); cfg.providers.openai.base_url = '${UPSTREAM}';
  expect(() => validateR7altProfile(stockSource, stringify(cfg))).toThrow('STOP: managed mock gateway profile r7alt is environment-dependent');
  const aliased = parse(r7altSource); aliased.providers.other = aliased.providers.openai;
  expect(() => validateR7altProfile(stockSource, stringify(aliased))).toThrow('STOP: managed mock gateway profile r7alt uses YAML aliases');
});

test('managed mock N11 still refuses request logging in the stock file', () => {
  const cfg = parse(stockSource); cfg.logging.request_logging = { enabled: true, include_bodies: true };
  expect(() => validateMockRouting(stringify(cfg))).toThrow('STOP: managed mock unexpected logging configuration');
});

test('managed mock N12 refuses r7alt declared on Distributed', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  expect(withFakeDocker.bind(null, testInfo.outputPath('docker'), 'distributed', fakeStack('distributed'), declare('r7alt', inspectManagedMock)))
    .toThrow('STOP: managed mock gateway profile r7alt is Standalone only');
});

// H-MCP's profile rule: mcp is Standalone only, mounts gateway.mcp.yaml, which is
// exactly stock plus MCP_DELTA and names no MCP upstream, and runs mcp-mock
// (agentcc-mcpmock) on e2e-public only, at its pinned address, as the profile
// starts it. One case per rule, each asserting that rule's own STOP message.
const mcpSource = readFileSync(repoFile('stack/gateway.mcp.yaml'), 'utf8');

test('managed mock P5 inspects a Standalone stack with mcp declared, mounted and its mock on e2e-public only', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  const receipt = withFakeDocker(testInfo.outputPath('docker'), 'standalone', fakeStack('standalone', 'mcp'),
    declare('mcp', inspectManagedMock));
  expect(receipt.profile).toBe('mcp');
  expect(receipt.services.map(s => s.service)).toEqual(inspectedServices('standalone', 'mcp'));
  expect(standaloneServices('mcp')).toEqual(inspectedServices('standalone', 'mcp'));
  expect(standaloneServices('stock')).toEqual(inspectedServices('standalone'));
  expect(receipt.gatewaySha).toBe(createHash('sha256').update(readFileSync(repoFile('stack/gateway.mcp.yaml'))).digest('hex'));
});

test('managed mock P6 the committed mcp file is exactly stock plus the declared delta, naming no MCP upstream', () => {
  validateMcpProfile(stockSource, mcpSource);
  expect(parse(mcpSource)).toEqual(mcpConfig(stockSource));
  expect(parse(mcpSource).mcp).toEqual(MCP_DELTA.mcp);
  expect(Buffer.byteLength(MCP_DELTA.mcp.state_keys[0])).toBeGreaterThanOrEqual(32);
});

test('managed mock P7 the Standalone overlay runs mcp-mock exactly as the inspector expects, on e2e-public only', () => {
  const overlay = parse(readFileSync(repoFile('stack/docker-compose.mcp-e2e.yml'), 'utf8'));
  const service = overlay.services[MCP_MOCK.service];
  const pin = (PUBLIC_NETWORK.pinned as Record<string, { ip: string; alias: string }>)[MCP_MOCK.service];
  expect(service).toEqual({
    image: MCP_MOCK.image, pull_policy: 'never', command: [...MCP_MOCK.command],
    environment: { E2E_MCP_MOCK_AUTH: '${E2E_MCP_MOCK_AUTH:-bearer:' + MCP_MOCK.bearer + '}' },
    networks: { [PUBLIC_NETWORK.key]: { ipv4_address: pin.ip, aliases: [pin.alias] } }, restart: 'no',
  });
  expect(MCP_MOCK.url).toBe(`https://${pin.alias}:8443/mcp`);
  const entrypoint = readFileSync(repoFile('stack/mcp-mock/main.go'), 'utf8');
  // MANAGED-FIXTURE-713-038: exact overlay equality still forbids extra env,
  // mounts, commands and networks. Pin default bearer, explicit OAuth and the
  // fail-closed selector, then bind BOTH serving fixtures to its checked result.
  expect(entrypoint).toContain([
    'func selectedMockAuth(value string) (string, error) {',
    `\tconst bearer = "bearer:${MCP_MOCK.bearer}"`,
    '\tswitch value {',
    '\tcase "", bearer:',
    '\t\treturn bearer, nil',
    '\tcase "oauth":',
    '\t\treturn "oauth", nil',
    '\tdefault:',
    '\t\treturn "", errors.New("E2E_MCP_MOCK_AUTH must select the reviewed bearer or OAuth fixture")',
    '\t}',
    '}',
  ].join('\n'));
  expect(entrypoint).toContain([
    '\tauth, err := selectedMockAuth(os.Getenv("E2E_MCP_MOCK_AUTH"))',
    '\tif err != nil {',
    '\t\tlog.Fatal(err)',
    '\t}',
  ].join('\n'));
  for (const [variable, path, fixtures] of [['mock', '/mcp', 'fixtures'], ['next', '/mcp-next', 'nextFixtures']]) {
    expect(entrypoint).toContain([
      `\t${variable}, err := mcpmock.New(mcpmock.Config{`,
      '\t\tEra: era, Auth: auth,',
      `\t\tPath: "${path}", PublicURL: "https://${pin.alias}:8443",`,
      '\t\tServerName: "agentcc-mcpmock",',
      `\t}, ${fixtures})`,
    ].join('\n'));
  }
  expect(entrypoint).toContain('mcpmock.LoadFixtures("/fixtures")');
  expect(entrypoint).toContain('mcpmock.LoadFixtures("/fixtures-next")');
});

test('managed mock N13 refuses mcp declared with the stock file mounted', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  const files = fakeStack('standalone', 'mcp');
  fakeService(files, 'app').Mounts.find((m: any) => m.Destination === '/etc/futureagi/secrets/agentcc.yaml').Source =
    repoFile('stack/gateway.e2e.yaml');
  expect(withFakeDocker.bind(null, testInfo.outputPath('docker'), 'standalone', files, declare('mcp', inspectManagedMock)))
    .toThrow('STOP: managed mock gateway profile mcp expects e2e/stack/gateway.mcp.yaml mounted');
});

for (const [name, mutate] of [
  ['an operator MCP server, even at the mock', (c: any) => { c.mcp.servers = { mock: { transport: 'http', url: MCP_MOCK.url } }; }],
  ['a stdio server process', (c: any) => { c.mcp.servers = { fs: { transport: 'stdio', command: 'npx' } }; }],
  ['a managed tool server', (c: any) => { c.mcp.managed_servers = { search: { url: 'https://mcp.invalid' } }; }],
  ['a private egress allowance', (c: any) => { c.mcp.egress = { private_allow: ['10.0.0.0/8'] }; }],
  ['an OAuth sign-in', (c: any) => { c.mcp.oauth = { enabled: true }; }],
  ['a static org', (c: any) => { c.mcp.static = { org_id: 'org-1' }; }],
  ['a public URL', (c: any) => { c.mcp.public_url = 'https://gateway.invalid'; }],
  ['anonymous MCP', (c: any) => { c.mcp.allow_anonymous_dev = true; }],
] as [string, (c: any) => void][]) {
  test(`managed mock N14 refuses an mcp file with ${name}`, () => {
    const cfg = parse(mcpSource); mutate(cfg);
    expect(() => validateMcpProfile(stockSource, stringify(cfg)))
      .toThrow('STOP: managed mock gateway profile mcp names an MCP upstream: the mock is the only one, saved as an org server');
  });
}

for (const [name, mutate] of [
  ['MCP off', (c: any) => { c.mcp.enabled = false; }],
  ['another state key', (c: any) => { c.mcp.state_keys = ['e2e-only-mcp-state-key-not-a-secret-1111']; }],
  ['no mcp section', (c: any) => { delete c.mcp; }],
  ['an extra routing section', (c: any) => { c.routing = { model_fallbacks: { 'gpt-4o': ['gpt-4o-mini'] } }; }],
  ['logging at debug', (c: any) => { c.logging.level = 'debug'; }],
  ['a stock key dropped', (c: any) => { delete c.providers['fagi-managed']; }],
] as [string, (c: any) => void][]) {
  test(`managed mock N14b refuses an mcp file with ${name}`, () => {
    const cfg = parse(mcpSource); mutate(cfg);
    expect(() => validateMcpProfile(stockSource, stringify(cfg)))
      .toThrow('STOP: managed mock gateway profile mcp is not stock plus the declared delta');
  });
}

test('managed mock N14c refuses an mcp file with environment expansion or aliases', () => {
  const cfg = parse(mcpSource); cfg.mcp.state_keys = ['${AGENTCC_MCP_STATE_KEYS}'];
  expect(() => validateMcpProfile(stockSource, stringify(cfg))).toThrow('STOP: managed mock gateway profile mcp is environment-dependent');
  const aliased = parse(mcpSource); aliased.providers.other = aliased.providers.openai;
  expect(() => validateMcpProfile(stockSource, stringify(aliased))).toThrow('STOP: managed mock gateway profile mcp uses YAML aliases');
});

const mcpMockCases: [string, string, (files: Record<string, any>) => void, string][] = [
  ['N15', 'the mock also on the default network', (files) => {
    const mock = fakeService(files, MCP_MOCK.service);
    mock.NetworkSettings.Networks['futureagi-e2e-standalone_default'] = { NetworkID: 'e2e-network', Aliases: [MCP_MOCK.service], IPAddress: '172.18.0.9' };
  }, 'service mcp-mock network or DNS override'],
  ['N15b', 'the mock at another address', (files) => {
    fakeService(files, MCP_MOCK.service).NetworkSettings.Networks['futureagi-e2e-standalone_e2e-public'].IPAddress = '11.88.0.99';
  }, 'service mcp-mock public address or alias mismatch'],
  ['N15l', 'the mock at the retired MCP address now owned by smtp-sink', (files) => {
    fakeService(files, MCP_MOCK.service).NetworkSettings.Networks['futureagi-e2e-standalone_e2e-public'].IPAddress =
      PUBLIC_NETWORK.pinned['smtp-sink'].ip;
  }, 'service mcp-mock public address or alias mismatch'],
  ['N15c', 'the mock without its alias', (files) => {
    fakeService(files, MCP_MOCK.service).NetworkSettings.Networks['futureagi-e2e-standalone_e2e-public'].Aliases = [MCP_MOCK.service];
  }, 'service mcp-mock public address or alias mismatch'],
  ['N15d', 'the mock not running', (files) => {
    files['containers.json'] = files['containers.json'].filter((c: any) => c.Config.Labels['com.docker.compose.service'] !== MCP_MOCK.service);
    files['ps.txt'] = files['containers.json'].map((c: any) => c.Id).join('\n');
  }, 'services missing or ambiguous'],
  ['N15e', 'the mock from another image', (files) => {
    fakeService(files, MCP_MOCK.service).Config.Image = 'futureagi/agentcc-mcpmock:latest';
  }, 'mcp-mock is not agentcc-mcpmock as the mcp profile runs it'],
  ['N15f', 'the mock with another command (no bearer)', (files) => {
    fakeService(files, MCP_MOCK.service).Config.Cmd = ['--mode', 'http', '--addr', '0.0.0.0:8080', '--auth', 'none',
      '--fixtures', '/fixtures'];
  }, 'mcp-mock is not agentcc-mcpmock as the mcp profile runs it'],
  ['N15g', 'the mock with a mount', (files) => {
    fakeService(files, MCP_MOCK.service).Mounts = [{ Type: 'bind', Source: '/', Destination: '/host', RW: false }];
  }, 'mcp-mock is not agentcc-mcpmock as the mcp profile runs it'],
  ['N15h', 'the mock with a published port', (files) => {
    fakeService(files, MCP_MOCK.service).NetworkSettings.Ports = { '8080/tcp': [{ HostIp: '0.0.0.0', HostPort: '28099' }] };
  }, 'mcp-mock is not agentcc-mcpmock as the mcp profile runs it'],
  ['N15i', 'the mock behind a proxy', (files) => {
    fakeService(files, MCP_MOCK.service).Config.Env.push('HTTPS_PROXY=http://proxy.invalid');
  }, 'mcp-mock has a proxy/preload override'],
  ['N15j', 'the mock answering to the LLM mock\'s alias too', (files) => {
    fakeService(files, MCP_MOCK.service).NetworkSettings.Networks['futureagi-e2e-standalone_e2e-public'].Aliases.push('llm.e2e-public.test');
  }, 'service mcp-mock public address or alias mismatch'],
];
for (const [id, name, mutate, reason] of mcpMockCases) {
  test(`managed mock ${id} refuses an mcp stack with ${name}`, ({}, testInfo) => {
    test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
    const files = fakeStack('standalone', 'mcp');
    mutate(files);
    expect(withFakeDocker.bind(null, testInfo.outputPath('docker'), 'standalone', files, declare('mcp', inspectManagedMock)))
      .toThrow(`STOP: managed mock ${reason}`);
  });
}

test('managed mock N15k refuses a stray MCP-profile container on a stock stack (an unlisted public member)', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  const files = fakeStack('standalone');
  // Stock now includes its own walk MCP recorder. Keep every declared service:
  // the negative control is an additional, unlisted container on e2e-public.
  expect(() => withFakeDocker(testInfo.outputPath('docker-honest'), 'standalone', files,
    declare(undefined, inspectManagedMock))).not.toThrow();
  const stray = 'f'.repeat(64);
  expect(files['containers.json'].some((c: any) => c.Id === stray)).toBe(false);
  files['network-e2e-public-network.json'][0].Containers[stray] = {
    Name: 'stray-mcp-profile-mock', IPv4Address: '11.88.0.99/24',
  };
  expect(withFakeDocker.bind(null, testInfo.outputPath('docker'), 'standalone', files, declare(undefined, inspectManagedMock)))
    .toThrow('STOP: managed mock public network has an unlisted member');
});

test('managed mock N16 refuses mcp declared on Distributed', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  expect(withFakeDocker.bind(null, testInfo.outputPath('docker'), 'distributed', fakeStack('distributed'), declare('mcp', inspectManagedMock)))
    .toThrow('STOP: managed mock gateway profile mcp is Standalone only');
});

// The mcp-catalog profile's rule: Standalone only, mounts gateway.mcp-catalog.yaml,
// which is exactly stock plus MCP_CATALOG_DELTA and names no MCP upstream and no
// connector catalogue (the gateway reads the one it embeds), and runs no MCP mock.
// One case per rule, each asserting that rule's own STOP message.
const mcpCatalogSource = () => readFileSync(repoFile('stack/gateway.mcp-catalog.yaml'), 'utf8');

test('managed mock P8 inspects a Standalone stack with mcp-catalog declared and mounted, and no MCP mock', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  const receipt = withFakeDocker(testInfo.outputPath('docker'), 'standalone', fakeStack('standalone', 'mcp-catalog'),
    declare('mcp-catalog', inspectManagedMock));
  expect(receipt.profile).toBe('mcp-catalog');
  expect(receipt.services.map(s => s.service)).toEqual(inspectedServices('standalone'));
  expect(standaloneServices('mcp-catalog')).toEqual(inspectedServices('standalone'));
  expect(receipt.gatewaySha).toBe(createHash('sha256').update(readFileSync(repoFile('stack/gateway.mcp-catalog.yaml'))).digest('hex'));
});

test('managed mock P9 the committed mcp-catalog file is exactly stock plus the declared delta, naming no upstream or catalogue', () => {
  validateMcpCatalogProfile(stockSource, mcpCatalogSource());
  expect(parse(mcpCatalogSource())).toEqual(mcpCatalogConfig(stockSource));
  expect(parse(mcpCatalogSource()).mcp).toEqual(MCP_CATALOG_DELTA.mcp);
  expect(parse(mcpCatalogSource()).connectors).toBeUndefined();
  expect(MCP_CATALOG_DELTA.mcp).toEqual(MCP_DELTA.mcp);
});

test('managed mock N17 refuses mcp-catalog declared with the mcp file mounted', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  const files = fakeStack('standalone', 'mcp-catalog');
  fakeService(files, 'app').Mounts.find((m: any) => m.Destination === '/etc/futureagi/secrets/agentcc.yaml').Source =
    repoFile('stack/gateway.mcp.yaml');
  expect(withFakeDocker.bind(null, testInfo.outputPath('docker'), 'standalone', files, declare('mcp-catalog', inspectManagedMock)))
    .toThrow('STOP: managed mock gateway profile mcp-catalog expects e2e/stack/gateway.mcp-catalog.yaml mounted');
});

for (const [name, mutate] of [
  ['an operator MCP server', (c: any) => { c.mcp.servers = { mock: { transport: 'http', url: MCP_MOCK.url } }; }],
  ['a stdio server process', (c: any) => { c.mcp.servers = { fs: { transport: 'stdio', command: 'npx' } }; }],
  ['a managed tool server', (c: any) => { c.mcp.managed_servers = { search: { url: 'https://mcp.invalid' } }; }],
  ['a private egress allowance', (c: any) => { c.mcp.egress = { private_allow: ['10.0.0.0/8'] }; }],
  ['an OAuth sign-in', (c: any) => { c.mcp.oauth = { enabled: true }; }],
  ['a static org', (c: any) => { c.mcp.static = { org_id: 'org-1' }; }],
  ['anonymous MCP', (c: any) => { c.mcp.allow_anonymous_dev = true; }],
] as [string, (c: any) => void][]) {
  test(`managed mock N17b refuses an mcp-catalog file with ${name}`, () => {
    const cfg = parse(mcpCatalogSource()); mutate(cfg);
    expect(() => validateMcpCatalogProfile(stockSource, stringify(cfg)))
      .toThrow('STOP: managed mock gateway profile mcp-catalog names an MCP upstream: it has none');
  });
}

for (const [name, mutate] of [
  ["H-MCP's fixture catalogue", (c: any) => { c.connectors = { catalog_dir: '/opt/e2e/mcp/catalog' }; }],
  ['an empty connectors section', (c: any) => { c.connectors = {}; }],
] as [string, (c: any) => void][]) {
  test(`managed mock N17c refuses an mcp-catalog file with ${name}`, () => {
    const cfg = parse(mcpCatalogSource()); mutate(cfg);
    expect(() => validateMcpCatalogProfile(stockSource, stringify(cfg)))
      .toThrow('STOP: managed mock gateway profile mcp-catalog names a connector catalogue: it reads the one the gateway embeds');
  });
}

for (const [name, mutate] of [
  ['MCP off', (c: any) => { c.mcp.enabled = false; }],
  ['another state key', (c: any) => { c.mcp.state_keys = ['e2e-only-mcp-state-key-not-a-secret-1111']; }],
  ['no mcp section', (c: any) => { delete c.mcp; }],
  ['logging at debug', (c: any) => { c.logging.level = 'debug'; }],
  ['a stock key dropped', (c: any) => { delete c.providers['fagi-managed']; }],
] as [string, (c: any) => void][]) {
  test(`managed mock N17d refuses an mcp-catalog file with ${name}`, () => {
    const cfg = parse(mcpCatalogSource()); mutate(cfg);
    expect(() => validateMcpCatalogProfile(stockSource, stringify(cfg)))
      .toThrow('STOP: managed mock gateway profile mcp-catalog is not stock plus the declared delta');
  });
}

test('managed mock N18 refuses mcp-catalog declared on Distributed', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  expect(withFakeDocker.bind(null, testInfo.outputPath('docker'), 'distributed', fakeStack('distributed'), declare('mcp-catalog', inspectManagedMock)))
    .toThrow('STOP: managed mock gateway profile mcp-catalog is Standalone only');
});

test('managed stack routing has runtime labels, network and read-only source proof', async ({}, testInfo) => {
  const receipt = inspectManagedMock();
  // Standalone runs the gateway, backend, worker and UI in one `app` container
  // (lib/managed-mock-standalone.ts).
  expect(receipt.services.map(s => s.service)).toEqual(
    inspectedServices(process.env.E2E_STACK === 'standalone' ? 'standalone' : 'distributed', walkGatewayProfile()));
  expect(receipt.profile).toBe(walkGatewayProfile());
  expect(receipt.gatewaySha).toMatch(/^[a-f0-9]{64}$/);
  expect(receipt.mockSha).toMatch(/^[a-f0-9]{64}$/);
  await testInfo.attach('verified-managed-mock', { contentType: 'application/json', body: JSON.stringify(receipt) });
});

// The integration export rule (lib/export-sinks.ts EXPORT_ENDPOINTS): an
// INTEGRATIONS_* override names only its declared local sink, only on the API
// and the worker; the export flows' opt-in requires every one of them there
// and the storage sink as this project's own container on default only.
for (const evalBackground of [false, true]) {
  test(`managed mock E1 accepts exactly the declared export sinks on backend and worker (background=${evalBackground})`, () => {
    for (const service of ['backend', 'worker']) {
      validateMockEnvironment(service, { ...backgroundEnvironment, ...EXPORT_ENDPOINTS }, evalBackground);
      validateMockEnvironment(service, { ...backgroundEnvironment, INTEGRATIONS_DATADOG_URL: '' }, evalBackground);
    }
  });

  for (const [name, service, override] of [
    ['a vendor host', 'worker', { INTEGRATIONS_DATADOG_URL: 'https://api.datadoghq.com' }],
    ['another sink channel', 'backend', { INTEGRATIONS_MIXPANEL_URL: `${EXPORT_ENDPOINTS.INTEGRATIONS_MIXPANEL_URL}x` }],
    ['an S3 endpoint elsewhere', 'worker', { INTEGRATIONS_S3_ENDPOINT_URL: 'http://minio:9000' }],
    ['an undeclared override', 'backend', { INTEGRATIONS_PAGERDUTY_URL: 'http://hooks.e2e-public.test:8080/r/x' }],
    ['a declared value on the gateway', 'agentcc-gateway', { ...EXPORT_ENDPOINTS }],
    ['a declared value on the mock', 'mock-llm', { INTEGRATIONS_S3_ENDPOINT_URL: EXPORT_ENDPOINTS.INTEGRATIONS_S3_ENDPOINT_URL }],
  ] as [string, string, Record<string, string>][]) {
    test(`managed mock E2 refuses ${name} (background=${evalBackground})`, () => {
      expect(() => validateMockEnvironment(service, { ...backgroundEnvironment, ...override }, evalBackground))
        .toThrow(`STOP: managed mock ${service} exports to an undeclared destination`);
    });
  }
}

test('managed mock E2 refuses any export override on the Standalone app', () => {
  expect(() => validateStandaloneAppEnvironment({ ...composedStandaloneEnvironment(), ...EXPORT_ENDPOINTS }, false))
    .toThrow('STOP: managed mock app exports to an undeclared destination');
});

/** A Distributed fake stack whose backend and worker carry `exportEnv`, with or without the storage sink. */
function exportStack(exportEnv: Record<string, string>, withStorageSink = true) {
  const files = fakeStack('distributed');
  const containers: any[] = files['containers.json'];
  for (const service of ['backend', 'worker']) {
    const c = containers.find((x) => x.Config.Labels['com.docker.compose.service'] === service);
    c.Config.Env = [...c.Config.Env, ...Object.entries(exportEnv).map(([k, v]) => `${k}=${v}`)];
  }
  if (withStorageSink) {
    const store = structuredClone(containers.find((x) => x.Config.Labels['com.docker.compose.service'] === 'postgres'));
    store.Id = 'f'.repeat(64);
    expect(containers.some((container) => container.Id === store.Id)).toBe(false);
    store.Config.Labels['com.docker.compose.service'] = 'storage-sink';
    store.NetworkSettings.Networks = Object.fromEntries(Object.entries(store.NetworkSettings.Networks)
      .map(([name, endpoint]: [string, any]) => [name, { ...endpoint, Aliases: ['storage-sink'] }]));
    store.NetworkSettings.Ports = {};
    containers.push(store);
    files['ps.txt'] = containers.map((c) => c.Id).join('\n');
  }
  return files;
}

test('managed mock E3 export opt-in requires every declared sink and the storage sink', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  const receipt = withFakeDocker(testInfo.outputPath('docker'), 'distributed', exportStack(EXPORT_ENDPOINTS),
    () => inspectManagedMock({ exportSinks: true }));
  // withWalkMocks appends walk services after the base stack's optional storage sink.
  expect(receipt.services.map((s) => s.service)).toEqual([
    ...inspectedServices('distributed').filter((service) => !WALK_MOCKS.some((mock) => mock === service)),
    'storage-sink', ...WALK_MOCKS,
  ]);
  const withoutS3 = Object.fromEntries(Object.entries(EXPORT_ENDPOINTS)
    .filter(([key]) => key !== 'INTEGRATIONS_S3_ENDPOINT_URL'));
  expect(() => withFakeDocker(testInfo.outputPath('docker-missing'), 'distributed', exportStack(withoutS3),
    () => inspectManagedMock({ exportSinks: true }))).toThrow('STOP: managed mock required backend INTEGRATIONS_S3_ENDPOINT_URL mismatch');
  expect(() => withFakeDocker(testInfo.outputPath('docker-nosink'), 'distributed', exportStack(EXPORT_ENDPOINTS, false),
    () => inspectManagedMock({ exportSinks: true }))).toThrow('STOP: managed mock services missing or ambiguous');
  // Without the opt-in the sinks are optional, and still exact.
  withFakeDocker(testInfo.outputPath('docker-plain'), 'distributed', exportStack({}, false), () => inspectManagedMock());
});

test('managed mock E4 export opt-in is Distributed only', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  expect(() => withFakeDocker(testInfo.outputPath('docker'), 'standalone', fakeStack('standalone'),
    () => inspectManagedMock({ exportSinks: true }))).toThrow('STOP: managed mock export sinks are Distributed only');
});

// Standalone post-recreation attestation. Deliberately outside the offline
// `--grep 'managed mock'` selection; no actor/model fixture or dispatch.
test('managed background routing has source, serving health and owned poller proof', async ({}, testInfo) => {
  // The Standalone inspection does not re-express the background constructor
  // attestation (managed-mock-background.py pins Distributed host names).
  test.skip(process.env.E2E_STACK === 'standalone', 'Distributed-only attestation');
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

// H8 is opt-in; old single-replica inspection above remains byte-for-byte in use.
for (const mutation of ['none', 'namespace', 'image', 'redis', 'entrypoint', 'mount', 'persistent-data', 'undeclared']) {
  test(`managed mock replica offline: ${mutation}`, ({}, testInfo) => {
    const files = fakeStack('standalone');
    const app = fakeService(files, 'app');
    app.Mounts[0].Source = repoFile('stack/gateway.mcp-catalog.yaml');
    app.Config.Env.push('AGENTCC_MCP_STATE_KEYS=e2e-only-mcp-state-key-not-a-secret-0000',
      'AGENTCC_REDIS_ADDRESS=127.0.0.1:6379', 'AGENTCC_REDIS_PASSWORD=local-dev-only-redis-password', 'AGENTCC_REDIS_DB=4');
    app.NetworkSettings.Ports['8081/tcp'] = [{ HostIp: '127.0.0.1', HostPort: '28091' }];
    const replica = structuredClone(app);
    replica.Id = 'e'.repeat(64);
    replica.Config.Labels['com.docker.compose.service'] = 'agentcc-gateway-2';
    replica.Config.Env = app.Config.Env.filter((x: string) => x.startsWith('AGENTCC_') && !x.startsWith('AGENTCC_MCP_CLIENT_INSTALLS_ENABLED='));
    replica.Config.Env.push('AGENTCC_PORT=8081');
    replica.Config.Entrypoint = ['agentcc-gateway'];
    replica.Config.Cmd = ['--config', '/etc/futureagi/secrets/agentcc.yaml'];
    replica.Mounts = [replica.Mounts[0]];
    replica.HostConfig = { NetworkMode: `container:${app.Id}`, ExtraHosts: null,
      Tmpfs: { '/data': 'rw,noexec,nosuid,size=16m' } };
    replica.NetworkSettings = { Networks: {}, Ports: {} };
    files['containers.json'].push(replica);
    files['ps.txt'] += '\n' + replica.Id;
    if (mutation === 'namespace') replica.HostConfig.NetworkMode = 'host';
    if (mutation === 'image') replica.Image = 'sha256:' + 'f'.repeat(64);
    if (mutation === 'redis') replica.Config.Env = replica.Config.Env.map((s: string) =>
      s.startsWith('AGENTCC_REDIS_DB=') ? 'AGENTCC_REDIS_DB=0' : s);
    if (mutation === 'entrypoint') replica.Config.Entrypoint = ['sh'];
    if (mutation === 'mount') replica.Mounts[0].RW = true;
    if (mutation === 'persistent-data') replica.Mounts.push({ Type: 'volume', Destination: '/data', RW: true });
    const inspect = () => withFakeDocker(testInfo.outputPath('replica'), 'standalone', files, () => {
      const saved = process.env.E2E_GATEWAY_REPLICAS;
      process.env.E2E_GATEWAY_PROFILE = 'mcp-catalog';
      process.env.E2E_GATEWAY_REPLICAS = mutation === 'undeclared' ? '1' : '2';
      try { return inspectManagedMock(); }
      finally { if (saved === undefined) delete process.env.E2E_GATEWAY_REPLICAS; else process.env.E2E_GATEWAY_REPLICAS = saved; }
    });
    if (mutation === 'none') expect(inspect().services.map(s => s.service)).toContain('agentcc-gateway-2');
    else expect(inspect).toThrow('STOP: managed mock');
  });
}

// E2E-MANAGED-MOCK-ATTEST-1 (DSX DATA-E2E-043, M6). That runner overrides the
// compose default network to `internal: true`, so no container publishes a host
// port; a host-side forwarder bound to 127.0.0.1 and ::1 only pipes each harness
// port to a container's address on that network and runs the test command as its
// child. The inspection accepts such an endpoint only on the forwarder's
// declaration (E2E_LOOPBACK_FORWARDS, lib/loopback-forwarder.ts) proven against
// procfs and Docker evidence; every mock identity check stays as strict. The
// procfs reads are recorded here as kernel text, so the inspection runs offline.
const FORWARDS_ENV = 'E2E_LOOPBACK_FORWARDS';
const SELF = 4242;
const FORWARDER = 4000;
type FakeSocket = { ip: string; port: number; inode: number; owner: number; state?: string; remote?: [string, number] };
type FakeProc = { self: number; read(path: string): string; list(path: string): string[]; link(path: string): string };
const procWord = (bytes: number[]) => (endianness() === 'LE' ? [...bytes].reverse() : bytes)
  .map(b => b.toString(16).padStart(2, '0')).join('').toUpperCase();
function procAddress(ip: string): string {
  if (isIPv4(ip)) return procWord(ip.split('.').map(Number));
  const bytes = Array<number>(16).fill(0);
  if (ip === '::1') bytes[15] = 1;
  else if (ip !== '::') throw new Error(`fixture address ${ip}`);
  return [0, 4, 8, 12].map(i => procWord(bytes.slice(i, i + 4))).join('');
}
const procPort = (port: number) => port.toString(16).toUpperCase().padStart(4, '0');
/** /proc/net/tcp{,6} as the kernel prints it (tcp4_seq_show, tcp6_seq_show headers). */
function procTable(sockets: FakeSocket[], v6 = false): string {
  return [v6
    ? '  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode'
    : '  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode',
    ...sockets.map((s, n) => {
      const [remoteIp, remotePort]: [string, number] = s.remote ?? [isIPv4(s.ip) ? '0.0.0.0' : '::', 0];
      return `${String(n).padStart(4)}: ${procAddress(s.ip)}:${procPort(s.port)} ${procAddress(remoteIp)}:${procPort(remotePort)} ` +
        `${s.state ?? '0A'} 00000000:00000000 00:00000000 00000000  1000        0 ${s.inode} 1 0000000000000000 100 0 0 10 0`;
    })].join('\n') + '\n';
}
/** This worker's procfs: its ancestors (nearest first) and every process's socket descriptors. */
function fakeProc(sockets: FakeSocket[], chain: number[], unreadable: number[] = []): FakeProc {
  const files: Record<string, string> = {
    'net/tcp': procTable(sockets.filter(s => isIPv4(s.ip))), 'net/tcp6': procTable(sockets.filter(s => !isIPv4(s.ip)), true) };
  [SELF, ...chain].forEach((pid, i) => {
    // A command name with spaces and parentheses, as /proc/<pid>/stat may print it.
    if (chain[i] !== undefined) files[`${pid}/stat`] = `${pid} (${pid === 4100 ? 'yarn (pw) run' : 'node'}) S ${chain[i]} ${pid} ${pid} 0 -1 4194560 7`;
  });
  const inodes = (pid: number) => sockets.filter(s => s.owner === pid).map(s => s.inode);
  const missing = (path: string) => Object.assign(new Error(`ENOENT ${path}`), { code: 'ENOENT' });
  return {
    self: SELF,
    read: path => { if (path in files) return files[path]; throw missing(path); },
    list: path => {
      const m = /^(\d+)\/fd$/.exec(path);
      if (!m || unreadable.includes(Number(m[1]))) throw Object.assign(new Error(`EACCES ${path}`), { code: 'EACCES' });
      return ['0', '1', '2', ...inodes(Number(m[1])).map((_, i) => String(20 + i))];
    },
    link: path => {
      const m = /^(\d+)\/fd\/(\d+)$/.exec(path);
      if (!m) throw missing(path);
      if (Number(m[2]) < 20) return '/dev/null';
      const inode = inodes(Number(m[1]))[Number(m[2]) - 20];
      if (inode === undefined) throw missing(path);
      return `socket:[${inode}]`;
    },
  };
}

/** DSX's forward table (runner ports.mjs) plus mock-slack, the attested endpoint it must add. */
const FORWARDED: [string, string, string][] = [['frontend', '80/tcp', E2E.appUrl], ['backend', '80/tcp', E2E.apiUrl],
  ['agentcc-gateway', '8080/tcp', E2E.gatewayUrl], ['postgres', '5432/tcp', E2E.pgUrl],
  ['clickhouse', '8123/tcp', E2E.chUrl], ['mock-slack', '8080/tcp', E2E.slackMockUrl]];
/** Members of the e2e-public network, which is never internal: Docker publishes their
 * ports even under DSX's internal default network, so its runner forwards only the rest. */
const DSX_PUBLISHED = ['backend', 'agentcc-gateway'];
const COLLECTOR = 'f0'.repeat(32);
type Forward = { service: string; listen: number; ip: string; port: number };
/** The active probes' plan and outcome as lib/loopback-forwarder.ts defines them (ProbeTarget, ProbeOutcome). */
type ProbeCall = { kind: string; service: string; hostPort: number; hosts: string[]; container: string; probes: number };
type ProbeResult = { service: string; hostPort: number; sent: number; delivered: number };
type FakeProber = (targets: ProbeCall[]) => ProbeResult[];
type ForwarderLayout = { files: Record<string, any>; declaration: unknown; forwards: Forward[]; sockets: FakeSocket[];
  chain: number[]; unreadable: number[]; ipOf: Record<string, string>; proc?: FakeProc;
  /** The recorded probe outcomes (every probe delivered unless a case says otherwise) and every plan it was given. */
  probe?: FakeProber; probed: ProbeCall[][] };
const deliverAll: FakeProber = targets =>
  targets.map(t => ({ service: t.service, hostPort: t.hostPort, sent: t.probes, delivered: t.probes }));

/** The distributed stack on an internal default network: `published` keep their Docker
 * ports, the rest are reached through the forwarder, which also carries fi-collector
 * (a project container outside the inspected set, like DSX's 24318 forward). */
function forwarderLayout(published: string[] = DSX_PUBLISHED): ForwarderLayout {
  const files = fakeStack('distributed');
  const network = 'futureagi-e2e_default';
  const net = files['network-e2e-network.json'][0];
  net.Internal = true;
  net.Containers = {};
  const ipOf: Record<string, string> = {};
  const attach = (c: any, ip: string) => {
    const service = c.Config.Labels['com.docker.compose.service'];
    c.NetworkSettings.Networks[network].IPAddress = ipOf[service] = ip;
    net.Containers[c.Id] = { Name: `futureagi-e2e-${service}-1`, IPv4Address: `${ip}/16` };
  };
  files['containers.json'].forEach((c: any, i: number) => attach(c, `172.18.0.${10 + i}`));
  const collector = structuredClone(fakeService(files, 'postgres'));
  Object.assign(collector, { Id: COLLECTOR, Image: `sha256:${COLLECTOR}` });
  collector.Config.Labels['com.docker.compose.service'] = 'fi-collector';
  collector.NetworkSettings.Ports = { '4318/tcp': null };
  attach(collector, '172.18.0.99');
  files[`inspect-${COLLECTOR}.json`] = [collector];
  const forwards: Forward[] = FORWARDED.filter(([service]) => !published.includes(service)).map(([service, port, url]) => {
    fakeService(files, service).NetworkSettings.Ports[port] = null; // Docker's view of an internal-only container
    return { service, listen: Number(new URL(url).port), ip: ipOf[service], port: parseInt(port, 10) };
  });
  forwards.push({ service: 'fi-collector', listen: 24318, ip: ipOf['fi-collector'], port: 4318 });
  const sockets: FakeSocket[] = forwards.flatMap((f, i) => ['127.0.0.1', '::1'].map((ip, j) =>
    ({ ip, port: f.listen, inode: 9000 + 2 * i + j, owner: FORWARDER })));
  sockets.push({ ip: '0.0.0.0', port: 22, inode: 100, owner: 1 }, { ip: '::', port: 22, inode: 101, owner: 1 },
    // One browser connection through the forwarder: the accepted socket, its upstream, the client.
    { ip: '127.0.0.1', port: 3100, inode: 9500, owner: FORWARDER, state: '01', remote: ['127.0.0.1', 41000] },
    { ip: '172.18.0.1', port: 50000, inode: 9501, owner: FORWARDER, state: '01', remote: [ipOf.frontend, 80] },
    { ip: '127.0.0.1', port: 41000, inode: 9502, owner: SELF, state: '01', remote: ['127.0.0.1', 3100] });
  return { files, declaration: { pid: FORWARDER, network, forwards }, forwards, sockets,
    chain: [4100, FORWARDER, 1], unreadable: [], ipOf, probed: [] };
}

type ForwardedReceipt = ReturnType<typeof inspectManagedMock> &
  { forwarded?: { service: string; hostPort: number; probes?: number; delivered?: number; kind?: string; reason?: string }[] };
/** The declaration as the forwarder exports it to its child; null leaves it unset. The
 * active probes are the layout's recorded outcomes (offline: no forwarder, no Docker). */
function inspectForwarded(testInfo: TestInfo, layout: ForwarderLayout): ForwardedReceipt {
  const proc = layout.proc ?? fakeProc(layout.sockets, layout.chain, layout.unreadable);
  const probe: FakeProber = targets => { layout.probed.push(structuredClone(targets)); return (layout.probe ?? deliverAll)(targets); };
  const options = { proc, probe };
  return withFakeDocker(testInfo.outputPath('docker'), 'distributed', layout.files, () => {
    const saved = process.env[FORWARDS_ENV];
    if (layout.declaration === null) delete process.env[FORWARDS_ENV];
    else process.env[FORWARDS_ENV] = typeof layout.declaration === 'string' ? layout.declaration : JSON.stringify(layout.declaration);
    try { return inspectManagedMock(options); }
    finally { if (saved === undefined) delete process.env[FORWARDS_ENV]; else process.env[FORWARDS_ENV] = saved; }
  });
}
const forwardOf = (layout: ForwarderLayout, service: string) => layout.forwards.find(f => f.service === service)!;
const noProc: FakeProc = { self: SELF, read: () => { throw new Error('procfs read'); },
  list: () => { throw new Error('procfs list'); }, link: () => { throw new Error('procfs link'); } };

test('managed mock offline (a) accepts the internal-network loopback forwarder layout (DSX DATA-E2E-043)', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  const receipt = inspectForwarded(testInfo, forwarderLayout());
  expect(receipt.services.map(s => s.service)).toEqual(inspectedServices('distributed'));
  expect(receipt.forwarded?.map(({ service, hostPort }) => ({ service, hostPort }))).toEqual(FORWARDED
    .filter(([service]) => !DSX_PUBLISHED.includes(service)).map(([service, , url]) => ({ service, hostPort: Number(new URL(url).port) })));
});

test('managed mock offline (a) accepts a mixed published and forwarded layout', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  const receipt = inspectForwarded(testInfo, forwarderLayout([...DSX_PUBLISHED, 'frontend', 'mock-slack']));
  expect(receipt.forwarded?.map(f => f.service)).toEqual(['postgres', 'clickhouse']);
});

test('managed mock offline (f) the published layout is accepted unchanged and never reads procfs or probes', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  const plain = withFakeDocker(testInfo.outputPath('plain'), 'distributed', fakeStack('distributed'), () => inspectManagedMock());
  const layout = { ...forwarderLayout(), proc: noProc };
  const declared = inspectForwarded(testInfo, { ...layout, files: fakeStack('distributed') });
  expect(Object.keys(plain)).not.toContain('forwarded');
  expect(declared).toEqual(plain);
  expect(layout.probed).toEqual([]);
});

// E2E-MANAGED-MOCK-ATTEST-1 r2 (the verifier's finding on r1): the declaration and the
// socket evidence say where a forwarder claims to pipe, not where it does. Every
// forwarded endpoint is therefore also proven by active probes (lib/forward-probe.mjs):
// eight fresh connections through each of its loopback listeners, each with its own
// nonce, every one confirmed from the inspected container's own side. Here the
// probes' outcomes are recorded; loopback-forwarder-lab.spec.ts runs the real ones
// against real containers and real unsafe forwarders.
const PROBE_KIND: Record<string, string> = { frontend: 'frontend', postgres: 'postgres', clickhouse: 'clickhouse', 'mock-slack': 'mock-slack' };
const containerIds = (layout: ForwarderLayout): Record<string, string> => Object.fromEntries(
  layout.files['containers.json'].map((c: any) => [c.Config.Labels['com.docker.compose.service'], c.Id]));
// r3 (the verifier's finding on r2): each receipt row says how its probes were confirmed.
// answer-bound: arrival in the inspected container AND an answer only it could give
// (postgres' backend pid, ClickHouse's planted file, the harness mocks' HMAC of a planted
// key); arrival-only: the frontend, the product's nginx, with the reason recorded.
const bindingOf = (service: string) => service === 'frontend'
  ? { kind: 'arrival-only', reason: expect.stringContaining('product-owned nginx') } : { kind: 'answer-bound' };

test('managed mock offline (g) proves each forwarded endpoint with probes through every listener and records their counts', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  const layout = forwarderLayout();
  const receipt = inspectForwarded(testInfo, layout);
  const forwarded = FORWARDED.filter(([service]) => !DSX_PUBLISHED.includes(service))
    .map(([service, , url]) => ({ service, hostPort: Number(new URL(url).port) }));
  const ids = containerIds(layout);
  expect(layout.probed).toEqual([forwarded.map(({ service, hostPort }) => ({ kind: PROBE_KIND[service], service, hostPort,
    hosts: ['127.0.0.1', '::1'], container: ids[service], probes: 16 }))]);
  expect(receipt.forwarded).toEqual(forwarded.map(f => ({ ...f, probes: 16, delivered: 16, ...bindingOf(f.service) })));
});

test('managed mock offline (g) probes only the listeners the forwarder holds, eight through each', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  const layout = forwarderLayout();
  layout.sockets = layout.sockets.filter(s => !(s.port === 25432 && s.ip === '::1'));
  const receipt = inspectForwarded(testInfo, layout);
  expect(layout.probed[0].find(t => t.service === 'postgres')).toMatchObject({ hosts: ['127.0.0.1'], probes: 8 });
  expect(receipt.forwarded?.find(f => f.service === 'postgres')).toEqual({ service: 'postgres', hostPort: 25432, probes: 8, delivered: 8,
    kind: 'answer-bound' });
});

const outcomeFor = (service: string, change: (o: ProbeResult) => ProbeResult[]): FakeProber => targets =>
  deliverAll(targets).flatMap(o => (o.service === service ? change(o) : [o]));
const notDelivered = (service: string) =>
  `loopback forwarder for ${service} did not deliver every probe to the managed ${service} container`;
// (h) a forwarder whose behaviour differs from its declaration, or one no probe can check.
const probeRefusals: [string, (layout: ForwarderLayout) => void, string][] = [
  ['one frontend probe of sixteen landing elsewhere (alternating upstreams)', l => {
    l.probe = outcomeFor('frontend', o => [{ ...o, delivered: o.delivered - 1 }]);
  }, notDelivered('frontend')],
  ['a postgres forwarder that answers every probe itself or chains to a non-mock', l => {
    l.probe = outcomeFor('postgres', o => [{ ...o, delivered: 0 }]);
  }, notDelivered('postgres')],
  ['fewer clickhouse probes sent than planned', l => {
    l.probe = outcomeFor('clickhouse', o => [{ ...o, sent: 8, delivered: 8 }]);
  }, notDelivered('clickhouse')],
  ['no probe outcome for mock-slack', l => { l.probe = outcomeFor('mock-slack', () => []); }, notDelivered('mock-slack')],
  ['two probe outcomes for frontend', l => { l.probe = outcomeFor('frontend', o => [o, o]); }, notDelivered('frontend')],
  ['probes that cannot run', l => { l.probe = () => { throw new Error('probe child failed'); }; },
    'loopback forwarder probes for frontend could not run'],
  ['the backend forwarded (no probe reaches it; Docker publishes it)', l => { Object.assign(l, forwarderLayout([])); },
    'loopback forwarder for backend cannot be probed'],
  ['the gateway forwarded (no probe reaches it; Docker publishes it)', l => { Object.assign(l, forwarderLayout(['backend'])); },
    'loopback forwarder for agentcc-gateway cannot be probed'],
];
for (const [name, mutate, reason] of probeRefusals) {
  test(`managed mock offline (h) refuses the forwarder layout with ${name}`, ({}, testInfo) => {
    test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
    const layout = forwarderLayout();
    mutate(layout);
    expect(() => inspectForwarded(testInfo, layout)).toThrow(`STOP: managed mock ${reason}`);
  });
}

test('managed mock offline keeps the exact STOP for an unpublished endpoint without a forwarder declaration', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  expect(() => inspectForwarded(testInfo, { ...forwarderLayout(), declaration: null }))
    .toThrow(/^STOP: managed mock endpoint is not published by frontend$/);
});

// (b) a target that is not the managed container on the internal network, (c) a
// non-mock upstream reached directly or behind the forwarder, (d) an
// undeterminable mapping, (e) a non-loopback bind; each case asserts its own STOP.
const forwarderRefusals: [string, string, (layout: ForwarderLayout) => void, string][] = [
  ['b', 'a forward to an address outside the project network', l => { forwardOf(l, 'frontend').ip = '10.20.0.5'; },
    'loopback forwarder target for frontend is not the managed frontend container on the internal network'],
  ['b', 'the mock-slack port forwarded to a managed non-mock container (backend)', l => {
    forwardOf(l, 'mock-slack').ip = l.ipOf.backend;
  }, 'loopback forwarder target for mock-slack is not the managed mock-slack container on the internal network'],
  ['b', 'the frontend port forwarded to the right address on the wrong container port', l => { forwardOf(l, 'frontend').port = 8080; },
    'loopback forwarder target for frontend is not the managed frontend container on the internal network'],
  ['b', 'an extra forward to an address no project container holds', l => { forwardOf(l, 'fi-collector').ip = '172.18.0.250'; },
    'loopback forwarder target for fi-collector is not the managed fi-collector container on the internal network'],
  ['b', 'an extra forward to a container of another project', l => {
    l.files[`inspect-${COLLECTOR}.json`][0].Config.Labels['com.docker.compose.project'] = 'another-project';
  }, 'loopback forwarder target for fi-collector is not the managed fi-collector container on the internal network'],
  ['b', 'a default network that is not internal', l => { l.files['network-e2e-network.json'][0].Internal = false; },
    'loopback forwarder is not on the managed internal network'],
  ['b', 'a declaration for another network', l => { (l.declaration as { network: string }).network = 'other_default'; },
    'loopback forwarder is not on the managed internal network'],
  ['b', 'a declared forwarder that is not an ancestor of this run', l => { l.chain = [4100, 3999, 1]; },
    'loopback forwarder is not an ancestor of this run'],
  ['c', 'a published mock-llm serving another source', l => {
    fakeService(l.files, 'mock-llm').Mounts[0].Source = repoFile('stack/webhook-sink/server.mjs');
  }, 'unexpected mock-llm source mount'],
  ['c', 'a forwarded mock-slack running another command', l => { fakeService(l.files, 'mock-slack').Config.Cmd = ['node', '/srv/other.mjs']; },
    'unexpected mock-slack command'],
  ['c', 'the mock-llm port served directly by something other than mock-llm', l => {
    fakeService(l.files, 'mock-llm').NetworkSettings.Ports['8080/tcp'] = null;
    l.sockets.push({ ip: '127.0.0.1', port: 28082, inode: 9600, owner: 7777 });
  }, 'loopback forwarder mapping for mock-llm is undeterminable'],
  ['d', 'a malformed declaration', l => { (l.declaration as Record<string, unknown>).bind = '0.0.0.0'; },
    'loopback forwarder declaration is malformed'],
  ['d', 'a declaration that is not JSON', l => { l.declaration = 'frontend=3100:80'; },
    'loopback forwarder declaration is malformed'],
  ['d', 'no declared forward for an unpublished endpoint', l => {
    (l.declaration as { forwards: Forward[] }).forwards = l.forwards.filter(f => f.service !== 'clickhouse');
    l.sockets = l.sockets.filter(s => s.port !== 28123);
  }, 'loopback forwarder mapping for clickhouse is undeterminable'],
  ['d', 'a declared forward with no listener', l => { l.sockets = l.sockets.filter(s => s.port !== 25432); },
    'loopback forwarder mapping for postgres is undeterminable'],
  ['d', "unreadable forwarder descriptors", l => { l.unreadable = [FORWARDER]; },
    'loopback forwarder mapping for frontend is undeterminable'],
  ['d', 'no procfs', l => { l.proc = noProc; }, 'loopback forwarder mapping for frontend is undeterminable'],
  ['e', 'the frontend forwarder also bound on all IPv4 addresses', l => {
    l.sockets.push({ ip: '0.0.0.0', port: 3100, inode: 9700, owner: FORWARDER });
  }, 'loopback forwarder for frontend is bound to a non-loopback address'],
  ['e', 'the clickhouse forwarder on the IPv6 wildcard instead of ::1', l => {
    l.sockets.find(s => s.port === 28123 && s.ip === '::1')!.ip = '::';
  }, 'loopback forwarder for clickhouse is bound to a non-loopback address'],
  ['e', 'the extra fi-collector forward bound on the VM address', l => {
    l.sockets.find(s => s.port === 24318 && s.ip === '127.0.0.1')!.ip = '10.128.0.7';
  }, 'loopback forwarder for fi-collector is bound to a non-loopback address'],
  ['e', 'another process holding the frontend port on ::1', l => {
    l.sockets.find(s => s.port === 3100 && s.ip === '::1')!.owner = 5555;
  }, 'loopback forwarder for frontend does not hold its port alone'],
];
for (const [id, name, mutate, reason] of forwarderRefusals) {
  test(`managed mock offline (${id}) refuses the forwarder layout with ${name}`, ({}, testInfo) => {
    test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
    const layout = forwarderLayout();
    mutate(layout);
    expect(() => inspectForwarded(testInfo, layout)).toThrow(`STOP: managed mock ${reason}`);
  });
}

// The mcp-signin profile's rule: Standalone only, mounts gateway.mcp-signin.yaml,
// which is exactly stock plus MCP_SIGNIN_DELTA (mcp's delta, the declared sign-in
// and public URL) and names no other MCP upstream, token channel or sign-in
// setting, and runs mcp-mock exactly as mcp does. One case per rule, each
// asserting that rule's own STOP message.
const mcpSigninSource = () => readFileSync(repoFile('stack/gateway.mcp-signin.yaml'), 'utf8');

test('managed mock P10 inspects a Standalone stack with mcp-signin declared and mounted, and its mock on e2e-public only', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  const receipt = withFakeDocker(testInfo.outputPath('docker'), 'standalone', fakeStack('standalone', 'mcp-signin'),
    declare('mcp-signin', inspectManagedMock));
  expect(receipt.profile).toBe('mcp-signin');
  expect(receipt.services.map(s => s.service)).toEqual(inspectedServices('standalone', 'mcp-signin'));
  expect(standaloneServices('mcp-signin')).toEqual(inspectedServices('standalone', 'mcp'));
  expect(receipt.gatewaySha).toBe(createHash('sha256').update(readFileSync(repoFile('stack/gateway.mcp-signin.yaml'))).digest('hex'));
});

test('managed mock P11 the committed mcp-signin file is exactly stock plus the declared delta: mcp\'s, the sign-in and the public URL', () => {
  validateMcpSigninProfile(stockSource, mcpSigninSource());
  expect(parse(mcpSigninSource())).toEqual(mcpSigninConfig(stockSource));
  expect(parse(mcpSigninSource()).mcp).toEqual(MCP_SIGNIN_DELTA.mcp);
  const { public_url: publicUrl, oauth, ...rest } = MCP_SIGNIN_DELTA.mcp;
  expect(rest).toEqual(MCP_DELTA.mcp);
  expect(MCP_SIGNIN_DELTA.connectors).toEqual(MCP_DELTA.connectors);
  expect(oauth).toEqual({ enabled: true, issuer: `${E2E.apiUrl}/agentcc/mcp-oauth` });
  expect(new URL(publicUrl).hostname).toBe('localhost');
});

test('managed mock N18 refuses mcp-signin declared with the mcp file mounted', ({}, testInfo) => {
  test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
  const files = fakeStack('standalone', 'mcp-signin');
  fakeService(files, 'app').Mounts.find((m: any) => m.Destination === '/etc/futureagi/secrets/agentcc.yaml').Source =
    repoFile('stack/gateway.mcp.yaml');
  expect(withFakeDocker.bind(null, testInfo.outputPath('docker'), 'standalone', files, declare('mcp-signin', inspectManagedMock)))
    .toThrow('STOP: managed mock gateway profile mcp-signin expects e2e/stack/gateway.mcp-signin.yaml mounted');
});

for (const [name, mutate] of [
  ['an operator MCP server, even at the mock', (c: any) => { c.mcp.servers = { mock: { transport: 'http', url: MCP_MOCK.url } }; }],
  ['a private egress allowance', (c: any) => { c.mcp.egress = { private_allow: ['10.0.0.0/8'] }; }],
  ['a token channel to Django', (c: any) => { c.mcp.django = { base_url: 'http://127.0.0.1:8000' }; }],
  ['an upstream OAuth adapter', (c: any) => { c.mcp.oauth_adapters = { retry_once_on_401: true }; }],
  ['a callback signing key in the sign-in', (c: any) => { c.mcp.oauth.callback_signing_key = 'not-declared'; }],
  ['a static org', (c: any) => { c.mcp.static = { org_id: 'org-1' }; }],
  ['anonymous MCP', (c: any) => { c.mcp.allow_anonymous_dev = true; }],
] as [string, (c: any) => void][]) {
  test(`managed mock N19 refuses an mcp-signin file with ${name}`, () => {
    const cfg = parse(mcpSigninSource()); mutate(cfg);
    expect(() => validateMcpSigninProfile(stockSource, stringify(cfg)))
      .toThrow('STOP: managed mock gateway profile mcp-signin names an MCP upstream: the mock is the only one, saved as an org server');
  });
}

for (const [name, mutate] of [
  ['the sign-in off', (c: any) => { c.mcp.oauth.enabled = false; }],
  ['another issuer', (c: any) => { c.mcp.oauth.issuer = 'https://issuer.invalid/agentcc/mcp-oauth'; }],
  ['another public URL', (c: any) => { c.mcp.public_url = 'https://gateway.invalid'; }],
  ['MCP off', (c: any) => { c.mcp.enabled = false; }],
  ['no connector fixture', (c: any) => { delete c.connectors; }],
] as [string, (c: any) => void][]) {
  test(`managed mock N19b refuses an mcp-signin file with ${name}`, () => {
    const cfg = parse(mcpSigninSource()); mutate(cfg);
    expect(() => validateMcpSigninProfile(stockSource, stringify(cfg)))
      .toThrow('STOP: managed mock gateway profile mcp-signin is not stock plus the declared delta');
  });
}

// U3: inspect the actual selected container entries, not a vector inferred from them.
for (const [selection, profile, vector, auth] of [
  ['W07', 'mcp', ['false', 'false', 'false', 'false'], 'bearer:e2e-mcp-upstream-bearer-not-a-secret'],
  ['W08', 'mcp', ['true', 'false', 'false', 'false'], 'oauth'],
  ['W09', 'mcp-signin', ['false', 'true', 'false', 'false'], 'bearer:e2e-mcp-upstream-bearer-not-a-secret'],
  ['W10', 'mcp-catalog', ['true', 'false', 'true', 'false'], 'bearer:e2e-mcp-upstream-bearer-not-a-secret'],
  ['W12', 'mcp-signin', ['false', 'true', 'false', 'true'], 'bearer:e2e-mcp-upstream-bearer-not-a-secret'],
] as [string, WalkGatewayProfile, string[], string][]) {
  for (const mutation of ['none', 'missing', 'malformed', 'wrong', 'wrong-service', 'duplicate', 'inherited']) {
    test(`managed mock U3 ${selection} declared environment: ${mutation}`, ({}, testInfo) => {
      test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
      const keys = ['AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED', 'AGENTCC_MCP_CLIENT_OAUTH_ENABLED',
        'AGENTCC_MCP_K2_APPS_ENABLED', 'AGENTCC_MCP_CLIENT_INSTALLS_ENABLED'];
      const hostKeys = ['E2E_MCP_SUBFEATURE_SELECTION', 'E2E_MCP_MOCK_AUTH', ...keys];
      const saved = Object.fromEntries(hostKeys.map(key => [key, process.env[key]]));
      try {
        for (const key of keys) delete process.env[key];
        process.env.E2E_MCP_SUBFEATURE_SELECTION = selection;
        process.env.E2E_MCP_MOCK_AUTH = auth;
        const files = fakeStack('standalone', profile);
        const app = fakeService(files, 'app');
        app.Config.Env = app.Config.Env.filter((entry: string) => !keys.some(key => entry.startsWith(`${key}=`)));
        app.Config.Env.push(...keys.map((key, i) => `${key}=${vector[i]}`),
          'AGENTCC_MCP_STATE_KEYS=e2e-only-mcp-state-key-not-a-secret-0000');
        if (PROFILE_RUNS_MCP_MOCK[profile]) {
          const mock = fakeService(files, MCP_MOCK.service);
          mock.Config.Env = mock.Config.Env.filter((entry: string) => !entry.startsWith('E2E_MCP_MOCK_AUTH='));
          mock.Config.Env.push(`E2E_MCP_MOCK_AUTH=${auth}`);
        }
        if (mutation === 'missing') app.Config.Env = app.Config.Env.filter((entry: string) => !entry.startsWith(`${keys[0]}=`));
        if (mutation === 'malformed') app.Config.Env = app.Config.Env.map((entry: string) => entry.startsWith(`${keys[0]}=`) ? `${keys[0]}=TRUE` : entry);
        if (mutation === 'wrong') app.Config.Env = app.Config.Env.map((entry: string) => entry.startsWith(`${keys[0]}=`) ? `${keys[0]}=${vector[0] === 'true' ? 'false' : 'true'}` : entry);
        if (mutation === 'wrong-service') fakeService(files, 'mock-llm').Config.Env.push(`${keys[0]}=false`);
        if (mutation === 'duplicate') app.Config.Env.push(`${keys[0]}=${vector[0]}`);
        if (mutation === 'inherited') process.env[keys[0]] = vector[0] === 'true' ? 'false' : 'true';
        const inspect = () => withFakeDocker(testInfo.outputPath('u3-docker'), 'standalone', files, declare(profile, inspectManagedMock));
        if (mutation === 'none') expect(inspect().services.map(service => service.service)).toEqual(inspectedServices('standalone', profile));
        else expect(inspect).toThrow('STOP: managed mock');
      } finally {
        for (const [key, value] of Object.entries(saved)) {
          if (value === undefined) delete process.env[key]; else process.env[key] = value;
        }
      }
    });
  }
}
