import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parse } from 'yaml';
import { validateRealCatalog, validateRealCatalogMounts } from '../lib/real-catalog';

// The mcp-catalog profile (lib/profile.ts): Django serves the connector catalogue
// this build bundles, and nothing is mounted over it. Offline cases for the two
// checks its flow runs before it reads the catalogue (lib/real-catalog.ts).
const file = (path: string) => fileURLToPath(new URL(`../../${path}`, import.meta.url));
const bundle = () => JSON.parse(readFileSync(file('futureagi/agentcc/connector_catalog/catalog.json'), 'utf8'));
const DJANGO_COPY = '/app/backend/agentcc/connector_catalog/catalog.json';
const GATEWAY_CONFIG = '/etc/futureagi/secrets/agentcc.yaml';
const app = (mounts: [string, string][]) => ({
  Mounts: mounts.map(([Source, Destination]) => ({ Type: 'bind', Source, Destination, RW: false })),
});

test('real catalog offline: the catalogue the API names is the one this build bundles, and no other', () => {
  const { sha256, version, sourceCommit } = bundle();
  expect(() => validateRealCatalog({ sha256, version, source_commit: sourceCommit }, bundle())).not.toThrow();
  for (const named of [
    { sha256: '0'.repeat(64), version, source_commit: sourceCommit },
    { sha256, version: '2000.01.01.1', source_commit: sourceCommit },
    { sha256, version, source_commit: 'another' },
    null,
    {},
  ]) {
    expect(() => validateRealCatalog(named, bundle()), JSON.stringify(named))
      .toThrow('STOP: the connector catalogue Django serves is not the one this build bundles');
  }
});

test('real catalog offline: the app mounts nothing over the catalogue it bundles', () => {
  expect(() => validateRealCatalogMounts(app([['/repo/e2e/stack/gateway.mcp-catalog.yaml', GATEWAY_CONFIG],
    ['/dev/null', '/etc/futureagi/secrets/vertex.json']]))).not.toThrow();
  for (const mounts of [
    [['/repo/e2e/.mcp/catalog/dist/catalog.json', DJANGO_COPY]],
    [['/repo/e2e/.mcp/catalog', '/app/backend/agentcc/connector_catalog']],
    [['/repo/e2e/.mcp/catalog', '/opt/e2e/mcp/catalog']],
    [['/repo/e2e/.mcp/ca.pem', '/opt/e2e/mcp/ca.pem']],
  ] as [string, string][][]) {
    expect(() => validateRealCatalogMounts(app(mounts)), JSON.stringify(mounts))
      .toThrow('STOP: the app mounts a connector catalogue over the one this build bundles');
  }
});

test('real catalog offline: the mcp-catalog overlay only seals MCP state, and mounts nothing', () => {
  const overlay = parse(readFileSync(file('e2e/stack/docker-compose.mcp-catalog-e2e.yml'), 'utf8'));
  expect(overlay.services).toEqual({
    app: { environment: {
      AGENTCC_MCP_STATE_KEYS: 'e2e-only-mcp-state-key-not-a-secret-0000',
      AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED: '${AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED:-true}',
      AGENTCC_MCP_CLIENT_OAUTH_ENABLED: '${AGENTCC_MCP_CLIENT_OAUTH_ENABLED:-false}',
      AGENTCC_MCP_K2_APPS_ENABLED: '${AGENTCC_MCP_K2_APPS_ENABLED:-true}',
    } },
  });
});

// Reuse the exact equality oracle: extra keys/mounts/services are never a subset match.
test('real catalog offline: the overlay exact oracle rejects an extra mount or environment key', () => {
  const services = parse(readFileSync(file('e2e/stack/docker-compose.mcp-catalog-e2e.yml'), 'utf8')).services;
  for (const mutate of [
    (s: typeof services) => { s.app.volumes = ['unexpected:/catalog']; },
    (s: typeof services) => { s.app.environment.UNDECLARED = 'false'; },
    (s: typeof services) => { s.extra = {}; },
  ]) {
    const altered = structuredClone(services); mutate(altered);
    expect(() => expect(altered).toEqual(services)).toThrow();
  }
});
