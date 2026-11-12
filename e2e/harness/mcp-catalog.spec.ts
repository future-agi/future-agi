import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parse, stringify } from 'yaml';
import { mcpConfig, validateMcpProfile } from '../lib/profile';
import { validateMcpCatalog } from '../lib/mcp-catalog';

// H-MCP: the API-key app must use the same locally built catalogue in Django
// and in the gateway. Its only provider is the isolated mock, never a vendor.
const file = (name: string) => fileURLToPath(new URL(`../stack/${name}`, import.meta.url));
const stock = readFileSync(file('gateway.e2e.yaml'), 'utf8');
const catalogDir = '/opt/e2e/mcp/catalog';

const fixtureBundle = () => ({
  schema: 'connector-catalog-bundle/v1',
  apps: [parse(readFileSync(file('mcp-mock/catalog/apps/e2e-echo/app.yaml'), 'utf8'))],
  providers: [parse(readFileSync(file('mcp-mock/catalog/apps/e2e-echo/providers/key.yaml'), 'utf8'))],
  platforms: [],
});

test('MCP catalog offline: any different upstream, variable, provider or app is refused', () => {
  expect(() => validateMcpCatalog(fixtureBundle())).not.toThrow();
  for (const mutate of [
    (bundle: any) => { bundle.providers[0].transport.url = 'https://other.invalid/mcp'; },
    (bundle: any) => { bundle.providers[0].transport.urlVariables = { host: { default: 'other.invalid' } }; },
    (bundle: any) => { bundle.providers.push(structuredClone(bundle.providers[0])); },
    (bundle: any) => { bundle.apps.push(structuredClone(bundle.apps[0])); },
  ]) {
    const bundle = fixtureBundle(); mutate(bundle);
    expect(() => validateMcpCatalog(bundle)).toThrow('MCP catalog is not the generated single-mock fixture');
  }
});

test('MCP catalog offline: the declared profile accepts exactly the test catalog directory', () => {
  const cfg = mcpConfig(stock) as Record<string, unknown>;
  cfg.connectors = { catalog_dir: catalogDir };
  expect(() => validateMcpProfile(stock, stringify(cfg))).not.toThrow();
  cfg.connectors = { catalog_dir: '/another/catalog' };
  expect(() => validateMcpProfile(stock, stringify(cfg))).toThrow('not stock plus the declared delta');
});

test('MCP catalog offline: the API-key fixture has exactly one fixed mock upstream and no URL variables', () => {
  const provider = parse(readFileSync(file('mcp-mock/catalog/apps/e2e-echo/providers/key.yaml'), 'utf8'));
  expect(provider.transport).toEqual({ kind: 'http', url: 'https://mcp.e2e-public.test:8443/mcp', era: 'auto' });
  expect(provider.auth).toEqual({ kind: 'api_key', credentialParts: [{
    in: 'header', name: 'Authorization', prefix: 'Bearer ', secret: true, label: 'API key',
  }] });
  expect(provider.tools).toEqual({ source: 'upstream_mcp', defaultPosture: 'ask' });
});

test('MCP catalog offline: the mcp-only overlay mounts one bundle into both readers and enables state sealing', () => {
  const overlay = parse(readFileSync(file('docker-compose.mcp-e2e.yml'), 'utf8'));
  expect(overlay.services.app.environment).toEqual({
    AGENTCC_MCP_STATE_KEYS: 'e2e-only-mcp-state-key-not-a-secret-0000',
    SSL_CERT_FILE: '/opt/e2e/mcp/ca.pem',
  });
  expect(overlay.services.app.volumes).toEqual([
    './e2e/.mcp/catalog:/opt/e2e/mcp/catalog:ro',
    './e2e/.mcp/catalog/dist/catalog.json:/app/backend/agentcc/connector_catalog/catalog.json:ro',
    './e2e/.mcp/ca.pem:/opt/e2e/mcp/ca.pem:ro',
  ]);
});
