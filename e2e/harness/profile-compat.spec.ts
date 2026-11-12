import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { test, expect } from '@playwright/test';
import { parse, stringify } from 'yaml';
import { gatewayProfile, MCP_STATE_KEY, newOrgDefaultDeclared, PROFILE_FILES, profileTag,
  r7altConfig, validateProfileSource, validateR7altProfile } from '../lib/profile';
import { requiredMockValues, validateEnvironmentEntries } from '../lib/managed-mock';
import { grepInvertPattern } from '../lib/quarantine';

// Compatibility surface since INT-2 c99dc71f35: profile.ts, managed-mock.ts,
// scope-actors.ts (scope-compat.spec.ts). Every pre-existing stack file and the
// datasets fixtures are unchanged. New profiles/overlays must remain opt-in.
const repo = fileURLToPath(new URL('../../', import.meta.url));
const stockSource = readFileSync(new URL('../stack/gateway.e2e.yaml', import.meta.url), 'utf8');
const alternateSource = readFileSync(new URL('../stack/gateway.r7alt.yaml', import.meta.url), 'utf8');
const envNames = ['E2E_GATEWAY_PROFILE', 'E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS', 'E2E_ALL_GATEWAY_PROFILES', 'E2E_LIVE_LLM'];
const saved = new Map(envNames.map(name => [name, process.env[name]]));
test.beforeEach(() => { for (const name of envNames) delete process.env[name]; });
test.afterEach(() => {
  for (const [name, value] of saved) {
    if (value === undefined) delete process.env[name]; else process.env[name] = value;
  }
});

test('profile compatibility offline: legacy defaults, profile tags and explicit new-org opt-in remain unchanged', () => {
  expect(gatewayProfile()).toBe('stock');
  for (const name of ['', 'stock', 'r7alt']) expect(gatewayProfile(name)).toBe(name || 'stock');
  for (const name of ['unknown', ' stock', 'STOCK']) expect(() => gatewayProfile(name)).toThrow('is not one of');
  expect(PROFILE_FILES.stock).toBe('e2e/stack/gateway.e2e.yaml');
  expect(PROFILE_FILES.r7alt).toBe('e2e/stack/gateway.r7alt.yaml');
  expect(profileTag('stock')).toBe('@gw-profile-stock');
  expect(profileTag('r7alt')).toBe('@gw-profile-r7alt');
  for (const value of [undefined, '', '0', 'true', 'yes']) expect(newOrgDefaultDeclared(value)).toBe(false);
  expect(newOrgDefaultDeclared('1')).toBe(true);
});

test('profile compatibility offline: r7alt still deep-merges only its original three node settings', () => {
  const stock = parse(stockSource);
  expect(r7altConfig(stockSource)).toEqual({ ...stock,
    cache: { ...stock.cache, enabled: true },
    logging: { ...stock.logging, level: 'warn', request_logging: { enabled: true, include_bodies: true } },
    server: { ...stock.server, read_timeout: '7s', idle_timeout: '90s' },
  });
  expect(parse(alternateSource)).toEqual(r7altConfig(stockSource));
  expect(() => validateR7altProfile(stockSource, alternateSource)).not.toThrow();
  expect(() => validateProfileSource('r7alt', stockSource, alternateSource)).not.toThrow();
});

test('profile compatibility offline: r7alt retains inherited disabled sections instead of applying new-profile restrictions', () => {
  // The old helper's contract is exact stock + delta, not a second routing
  // validator. The managed-mock inspector separately validates stock routing.
  const stock = stringify({ ...parse(stockSource), mcp: { enabled: false, oauth: { enabled: false } },
    otel: { enabled: false, endpoint: 'http://localhost:4318' }, public_price_feed: { enabled: false, sources: [] } });
  const alternate = stringify(r7altConfig(stock));
  expect(() => validateR7altProfile(stock, alternate)).not.toThrow();
  expect(() => validateProfileSource('r7alt', stock, alternate)).not.toThrow();
});

for (const [name, mutate] of [
  ['timeout', (value: any) => { value.server.read_timeout = '8s'; }],
  ['cache', (value: any) => { value.cache.enabled = false; }],
  ['provider', (value: any) => { delete value.providers.openai; }],
] as const) {
  test(`profile compatibility offline: r7alt still refuses an undeclared ${name} change`, () => {
    const value = parse(alternateSource); mutate(value);
    expect(() => validateR7altProfile(stockSource, stringify(value)))
      .toThrow('STOP: managed mock gateway profile r7alt is not stock plus the declared delta');
  });
}

test('profile compatibility offline: strict YAML refusals retain the legacy error contracts', () => {
  expect(() => validateR7altProfile(stockSource, `${alternateSource}\ncache: {}\n`)).toThrow('r7alt is invalid YAML');
  const expanded = parse(alternateSource); expanded.server.host = '${HOST}';
  expect(() => validateR7altProfile(stockSource, stringify(expanded))).toThrow('r7alt is environment-dependent');
  const aliases = parse(alternateSource); aliases.providers.other = aliases.providers.openai;
  expect(() => validateR7altProfile(stockSource, stringify(aliases))).toThrow('r7alt uses YAML aliases');
});

const topology = { gateway: 'http://agentcc-gateway:8080', controlPlane: 'http://backend:8000',
  temporal: 'temporal:7233', settingsModule: 'tfc.settings.e2e' };
for (const profile of [undefined, 'stock', 'r7alt']) {
  test(`profile compatibility offline: ${profile ?? 'unset'} preserves legacy environment admission and refuses MCP state`, () => {
    if (profile !== undefined) process.env.E2E_GATEWAY_PROFILE = profile;
    for (const strict of [false, true]) {
      for (const service of ['app', 'backend', 'worker', 'agentcc-gateway']) {
        const env = requiredMockValues(topology, strict);
        expect(() => validateEnvironmentEntries(service, env, topology, strict)).not.toThrow();
        expect(() => validateEnvironmentEntries(service, { ...env, AGENTCC_MCP_STATE_KEYS: MCP_STATE_KEY }, topology, strict))
          .toThrow('unsupported gateway override');
        expect(() => validateEnvironmentEntries(service, { ...env, AGENTCC_INTERNAL_URL: 'https://remote.invalid' }, topology, strict))
          .toThrow('unsupported gateway override');
        expect(() => validateEnvironmentEntries(service, { ...env, AGENTCC_WEBHOOK_SECRET: 'e2e-agentcc-webhook-secret' }, topology, strict))
          .not.toThrow();
      }
    }
  });
}

test('profile compatibility offline: MCP state allowance is limited to declared MCP app contexts', () => {
  for (const profile of ['mcp', 'mcp-catalog', 'mcp-signin', 'mcp-otel']) {
    process.env.E2E_GATEWAY_PROFILE = profile;
    for (const strict of [false, true]) {
      expect(() => validateEnvironmentEntries('app', { AGENTCC_MCP_STATE_KEYS: MCP_STATE_KEY }, topology, strict)).not.toThrow();
      for (const service of ['backend', 'worker', 'agentcc-gateway', 'mock-llm']) {
        expect(() => validateEnvironmentEntries(service, { AGENTCC_MCP_STATE_KEYS: MCP_STATE_KEY }, topology, strict))
          .toThrow('unsupported gateway override');
      }
      expect(() => validateEnvironmentEntries('app', { AGENTCC_MCP_STATE_KEYS: 'not-the-fixture' }, topology, strict))
        .toThrow('unsupported gateway override');
    }
  }
  for (const profile of ['pricefeed', 'onekey-cat', 'unknown']) {
    process.env.E2E_GATEWAY_PROFILE = profile;
    expect(() => validateEnvironmentEntries('app', { AGENTCC_MCP_STATE_KEYS: MCP_STATE_KEY }, topology, false))
      .toThrow('unsupported gateway override');
  }
});

for (const profile of ['stock', 'r7alt']) {
  test(`profile compatibility offline: ${profile} still selects dataset flows and keeps unrelated opt-ins excluded`, () => {
    process.env.E2E_GATEWAY_PROFILE = profile;
    const exclude = grepInvertPattern()!;
    for (const id of ['DATA-E2E-001', 'DATA-E2E-002', 'DATA-E2E-003']) expect(exclude.test(`${id} @flow`)).toBe(false);
    expect(exclude.test(`legacy @gw-profile-${profile}`)).toBe(false);
    expect(exclude.test(`other @gw-profile-${profile === 'stock' ? 'r7alt' : 'stock'}`)).toBe(true);
    expect(exclude.test('new @gw-profile-mcp')).toBe(true);
    expect(exclude.test('new @new-org-default')).toBe(true);
    expect(exclude.test('legacy @new-org-default-off')).toBe(false);
    expect(exclude.test('external @live-llm')).toBe(true);
    // Listing uses the real existing fixtures/imports, without provisioning or
    // running a browser. DATA-E2E-003 has not landed here.
    const require = createRequire(import.meta.url);
    const listed = execFileSync(process.execPath, [require.resolve('@playwright/test/cli'), 'test',
      'flows/datasets', 'flows/gateway/settings-enforcement.spec.ts', '--list', '--reporter=list'],
      { cwd: `${repo}/e2e`, encoding: 'utf8', env: process.env });
    expect(listed).toContain('DATA-E2E-001');
    expect(listed).toContain('DATA-E2E-002');
    expect(listed).toContain('GW-E2E-010');
  });
}
