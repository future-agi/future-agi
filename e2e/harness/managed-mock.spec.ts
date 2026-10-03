import { test, expect } from '@playwright/test';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parse, stringify } from 'yaml';
import { E2E } from '../lib/env';
import { inspectManagedMock, managedMockInspectionError, MOCK_BASE, MOCK_MODEL, registerMockModel, validateBackgroundMockMounts, validateMockEnvironment, validateMockRouting } from '../lib/managed-mock';
import { validateStandaloneAppEnvironment } from '../lib/managed-mock-standalone';
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
  *" network inspect "*) cat "$FAKE_DOCKER/network.json" ;;
  *" compose "*" config "*) cat "$FAKE_DOCKER/config.json" ;;
  *" compose "*" ps "*) cat "$FAKE_DOCKER/ps.txt" ;;
  *" inspect "*) cat "$FAKE_DOCKER/containers.json" ;;
  *) echo "unexpected docker call" >&2; exit 64 ;;
esac
`;
const repoFile = (path: string) => fileURLToPath(new URL(`../${path}`, import.meta.url));
type FakeContainer = { service: string; ports?: Record<string, string>; cmd?: string[]; entrypoint?: string[];
  env?: Record<string, string>; extraHosts?: string[]; mounts?: [string, string][] };

function fakeStack(stack: 'standalone' | 'distributed') {
  const project = stack === 'standalone' ? 'futureagi-e2e-standalone' : 'futureagi-e2e';
  const network = `${project}_default`;
  const port = (url: string) => new URL(url).port;
  const gatewayConfig = repoFile('stack/gateway.e2e.yaml');
  const mock: FakeContainer = { service: 'mock-llm', cmd: ['node', '/srv/server.mjs'], entrypoint: ['docker-entrypoint.sh'],
    mounts: [[repoFile('stack/mock-llm/server.mjs'), '/srv/server.mjs']] };
  const stores: FakeContainer[] = [{ service: 'postgres', ports: { '5432/tcp': port(E2E.pgUrl) } },
    { service: 'clickhouse', ports: { '8123/tcp': port(E2E.chUrl) } }];
  const services: FakeContainer[] = stack === 'standalone' ? [{ service: 'app', env: composedStandaloneEnvironment(),
    ports: { '3000/tcp': port(E2E.appUrl), '8000/tcp': port(E2E.apiUrl), '8080/tcp': port(E2E.gatewayUrl) },
    extraHosts: ['code-executor:127.0.0.1', 'agentcc-gateway:127.0.0.1'],
    mounts: [[gatewayConfig, '/etc/futureagi/secrets/agentcc.yaml'], ['/dev/null', '/etc/futureagi/secrets/vertex.json']],
  }, mock, ...stores] : [{ service: 'agentcc-gateway', cmd: ['--config', '/app/config.yaml'],
    entrypoint: ['/app/agentcc-gateway'], ports: { '8080/tcp': port(E2E.gatewayUrl) },
    mounts: [[gatewayConfig, '/app/config.yaml']] }, mock,
  { service: 'backend', ports: { '80/tcp': port(E2E.apiUrl) }, env: { OPENAI_API_KEY: 'e2e-mock' } },
  { service: 'worker' }, { service: 'frontend', ports: { '80/tcp': port(E2E.appUrl) } }, ...stores];
  const containers = services.map((c, i) => ({
    Id: String(i + 1).repeat(64), Image: `sha256:${String(i + 1).repeat(64)}`,
    State: { Running: true, StartedAt: '2999-01-01T00:00:00Z' },
    Config: { Labels: { 'com.docker.compose.service': c.service, 'com.docker.compose.project': project },
      Env: Object.entries(c.env ?? {}).map(([key, value]) => `${key}=${value}`), Cmd: c.cmd ?? [], Entrypoint: c.entrypoint ?? [] },
    HostConfig: { ExtraHosts: c.extraHosts ?? null },
    Mounts: (c.mounts ?? []).map(([Source, Destination]) => ({ Type: 'bind', Source, Destination, RW: false })),
    NetworkSettings: { Networks: { [network]: { NetworkID: 'e2e-network', Aliases: [c.service], IPAddress: '172.18.0.2' } },
      Ports: Object.fromEntries(Object.entries(c.ports ?? {}).map(([p, host]) => [p, [{ HostIp: '127.0.0.1', HostPort: host }]])) },
  }));
  return {
    'context.json': [{ Endpoints: { docker: { Host: 'unix:///var/run/docker.sock' } } }],
    'config.json': { name: project, networks: { default: { name: network } }, services: {} },
    'ps.txt': containers.map(c => c.Id).join('\n'),
    'containers.json': containers,
    'network.json': [{ Driver: 'bridge', Labels: { 'com.docker.compose.project': project, 'com.docker.compose.network': 'default' } }],
  } as Record<string, any>;
}

function withFakeDocker<T>(dir: string, stack: 'standalone' | 'distributed', files: Record<string, unknown>,
  inspect: () => T, fail = ''): T {
  mkdirSync(dir, { recursive: true });
  writeFileSync(`${dir}/docker`, fakeDockerScript, { mode: 0o755 });
  for (const [name, body] of Object.entries(files)) writeFileSync(`${dir}/${name}`, typeof body === 'string' ? body : JSON.stringify(body));
  const saved = Object.fromEntries(['PATH', 'DOCKER_CONTEXT', 'DOCKER_HOST', 'E2E_STACK', 'FAKE_DOCKER', 'FAKE_DOCKER_FAIL']
    .map(key => [key, process.env[key]]));
  Object.assign(process.env, { PATH: `${dir}:${process.env.PATH}`, DOCKER_CONTEXT: 'e2e-fake', E2E_STACK: stack,
    FAKE_DOCKER: dir, FAKE_DOCKER_FAIL: fail });
  delete process.env.DOCKER_HOST;
  try {
    return inspect();
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
}

const offlineEndpoints = () => [E2E.appUrl, E2E.apiUrl, E2E.gatewayUrl, E2E.pgUrl, E2E.chUrl]
  .every(url => new URL(url).hostname === 'localhost');

for (const stack of ['standalone', 'distributed'] as const) {
  test(`managed mock inspects a well-formed ${stack} stack offline`, ({}, testInfo) => {
    test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
    const receipt = withFakeDocker(testInfo.outputPath('docker'), stack, fakeStack(stack), () => inspectManagedMock());
    expect(receipt.services.map(s => s.service)).toEqual(stack === 'standalone'
      ? ['app', 'mock-llm', 'postgres', 'clickhouse']
      : ['agentcc-gateway', 'mock-llm', 'backend', 'worker', 'frontend', 'postgres', 'clickhouse']);
  });

  test(`managed mock refuses a ${stack} network the project does not own`, ({}, testInfo) => {
    test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
    const files = fakeStack(stack);
    files['network.json'][0].Driver = 'host';
    expect(() => withFakeDocker(testInfo.outputPath('docker'), stack, files, () => inspectManagedMock()))
      .toThrow('STOP: managed mock unmanaged network');
  });

  test(`managed mock ${stack} inspection failures never echo arguments or Docker output`, ({}, testInfo) => {
    test.skip(!offlineEndpoints(), 'attach mode points the harness at another host');
    expect(() => withFakeDocker(testInfo.outputPath('docker'), stack, fakeStack(stack), () => inspectManagedMock(),
      'private-provider-credential')).toThrow(/^STOP: managed mock read-only Docker inspection failed \(code=unknown, status=1, signal=unknown\)$/);
  });
}

test('managed stack routing has runtime labels, network and read-only source proof', async ({}, testInfo) => {
  const receipt = inspectManagedMock();
  // Standalone runs the gateway, backend, worker and UI in one `app` container
  // (lib/managed-mock-standalone.ts).
  expect(receipt.services.map(s => s.service)).toEqual(process.env.E2E_STACK === 'standalone'
    ? ['app', 'mock-llm', 'postgres', 'clickhouse']
    : ['agentcc-gateway', 'mock-llm', 'backend', 'worker', 'frontend', 'postgres', 'clickhouse']);
  expect(receipt.gatewaySha).toMatch(/^[a-f0-9]{64}$/);
  expect(receipt.mockSha).toMatch(/^[a-f0-9]{64}$/);
  await testInfo.attach('verified-managed-mock', { contentType: 'application/json', body: JSON.stringify(receipt) });
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
