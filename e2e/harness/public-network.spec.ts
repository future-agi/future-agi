import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { parse } from 'yaml';
import { MCP_MOCK, PUBLIC_NETWORK } from '../lib/public-network';

const stackFile = (name: string) =>
  parse(readFileSync(new URL(`../stack/${name}`, import.meta.url), 'utf8'));

// 708i composes HOSTED-HARDEN-1's SMTP sink with H-MCP. Agreement between
// inspector and compose alone is insufficient: moving both to .12 would pass
// that check while assigning the SMTP sink and MCP mock the same address.
test('public network offline: MCP and SMTP retain distinct composed addresses', () => {
  expect(PUBLIC_NETWORK.pinned['mcp-mock']).toEqual({
    ip: '11.88.0.13', alias: 'mcp.e2e-public.test',
  });
  expect(PUBLIC_NETWORK.pinned['smtp-sink']).toEqual({
    ip: '11.88.0.12', alias: 'smtp.e2e-public.test',
  });
  const pins = Object.values(PUBLIC_NETWORK.pinned);
  expect(new Set(pins.map(pin => pin.ip)).size).toBe(pins.length);
  expect(new Set(pins.map(pin => pin.alias)).size).toBe(pins.length);
});

for (const [stack, file] of [
  ['standalone', 'docker-compose.standalone-e2e.yml'],
  ['distributed', 'docker-compose.e2e.yml'],
] as const) {
  test(`public network offline: ${stack} compose matches every inspector pin without collisions`, () => {
    const compose = stackFile(file);
    expect(compose.networks[PUBLIC_NETWORK.key].internal).toBe(true);
    expect(compose.networks[PUBLIC_NETWORK.key].ipam.config).toEqual([
      { subnet: PUBLIC_NETWORK.subnet },
    ]);
    const expected = Object.keys(PUBLIC_NETWORK.pinned)
      .filter(service => stack === 'standalone' || service !== MCP_MOCK.service)
      .sort();
    const assigned = Object.entries(compose.services)
      .flatMap(([service, config]: [string, any]) => {
        const endpoint = config.networks?.[PUBLIC_NETWORK.key];
        return endpoint?.ipv4_address ? [{ service, endpoint }] : [];
      });
    expect(assigned.map(row => row.service).sort()).toEqual(expected);
    expect(new Set(assigned.map(row => row.endpoint.ipv4_address)).size).toBe(assigned.length);
    for (const { service, endpoint } of assigned) {
      const pin = PUBLIC_NETWORK.pinned[service as keyof typeof PUBLIC_NETWORK.pinned];
      expect(endpoint, service).toEqual({ ipv4_address: pin.ip, aliases: [pin.alias] });
    }
  });
}

test('public network offline: MCP flow and catalog origins resolve to the compose-only alias', () => {
  const compose = stackFile('docker-compose.standalone-e2e.yml');
  const mock = compose.services[MCP_MOCK.service];
  expect(Object.keys(mock.networks)).toEqual([PUBLIC_NETWORK.key]);
  expect(mock.ports).toBeUndefined();
  const [alias] = mock.networks[PUBLIC_NETWORK.key].aliases;
  const url = `https://${alias}:8443/mcp`;
  expect(MCP_MOCK.url).toBe(url);
  expect(MCP_MOCK.catalogUrl).toBe(url);
  const provider = stackFile('mcp-mock/catalog/apps/e2e-echo/providers/key.yaml');
  expect(provider.transport).toEqual({ kind: 'http', url, era: 'auto' });
  const app = stackFile('mcp-mock/catalog/apps/e2e-echo/app.yaml');
  expect(app.vendor.domains).toEqual([alias]);
  expect(app.docsUrl).toBe(url);
  expect(app.sources.map((source: { url: string }) => source.url)).toEqual([url]);
});
